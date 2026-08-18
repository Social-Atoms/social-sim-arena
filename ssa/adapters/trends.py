"""Google Trends weekly search interest, and the archive that makes it scorable.

The first *behavioral* source in the registry: it counts what people typed into
a search box, not what they told an interviewer. There is no survey instrument
anywhere in this module and no respondent behind any number in it.

There is also no official API. The route used here is the one the Trends web UI
itself uses, and it takes two hops:

    GET https://trends.google.com/trends/api/explore?req=<json>
        -> widget descriptors, each carrying a one-time token
    GET https://trends.google.com/trends/api/widgetdata/multiline?req=...&token=...
        -> the timeseries JSON the chart is drawn from

Both responses open with an anti-JSON junk line (`)]}'` or `)]}',`) that has to
be stripped before parsing, the host wants a browser-shaped User-Agent and an
NID cookie from a prior visit to the homepage, and none of it is documented or
promised. `ssa/health.py`'s rule applies with extra force: if this module goes
quiet, suspect the contract changed, not the world.

**Three design constraints are not optional, and everything else here follows
from them.**

1. **The request window is fixed at the trailing 12 months (`today 12-m`).**
   Trends values are not counts. Each response is normalized to itself: the
   busiest week *in the requested window* is 100 and everything else is scaled
   to it. Ask for a different window and every number changes. A series whose
   points were fetched under different windows would not be one series, so the
   window is a module constant, both registered series state it in their unit,
   and nothing here accepts a window argument.

2. **Weekly granularity, taken natively.** A 12-month window returns weekly
   rows (Sunday through Saturday, timestamped at the week's start) without any
   resampling on our side. `parse_timeline` *verifies* the rows are seven days
   apart and fails loud otherwise -- if Google ever changes what a 12-month
   window returns, that is a contract change to be looked at, not silently
   averaged over.

3. **The archived snapshot is the ground truth, Civiqs-style.** Two upstream
   behaviors make the live endpoint unusable as a resolution source. The
   normalization above means the whole scale can shift when a new peak enters
   the trailing window or an old one leaves it. And the index is computed from
   a *sample* of searches, so the same completed week can read a point or two
   differently on different days -- the in-progress week moved five points
   between two fetches during this module's feasibility probes. So every fetch
   is archived under `trends/` at the repository root, one immutable file per
   (query, fetch day), named exactly the way `civiqs/` names its snapshots, and
   a registered series is built *from the archive*: a completed week's value is
   whatever the earliest snapshot that contains it showed, forever. Delete the
   archive and no Trends resolution in this repository is auditable.

**One keyword per request, never a comparison list.** The explore endpoint
accepts up to five keywords at once, which would halve this module's request
count -- and normalize all five to the *shared* maximum, so Tesla's index would
change whenever iPhone's peak did. Each registered series must be its own
0-100, so each is its own request cycle.

**Rate limiting is real and was measured, not assumed.** With a shared session
and three-second spacing, the second widgetdata call of a run came back 429;
in a faster burst the throttle surfaced as SSLZeroReturnError with no HTTP
status at all, exactly as Civiqs does it. The discipline here:

- one full cycle (homepage -> explore -> multiline) per query, on a *fresh*
  session with its own NID cookie, requests serialized and spaced;
- transport errors are retried with a long backoff -- the throttle lives on
  that path too, so the backoff is minutes-scale, not Civiqs's seconds-scale;
- an HTTP error is **not** retried in-process: a 429 will not clear in the
  seconds a refresh can afford to wait. The failed day writes no file, so the
  *next* six-hourly refresh retries naturally, and `as_archived` serves the
  archive (one day stale) in the meantime rather than going dark;
- the archive is the fetch cache: once today's snapshot exists, today costs
  zero requests. Steady state is one cycle -- three requests -- per query per
  day, which is also why there is no intra-day vintage re-check: a new
  completed week appears once a week, and re-checking would spend 429 budget
  to learn nothing.

**`asof` comes from the response, never the wall clock.** A snapshot's `asof`
is the end date of the newest *complete* week in it, read off the rows and
their `isPartial` flag. The in-progress week is archived too (it is what the
dashboard showed, and its drift is worth keeping evidence of) but it never
enters a series: its value changes all week by construction.

**Geography is fixed to the United States.** Every other tracker in the arena
measures a US population; a worldwide search index next to them would be the
one series answering a different question. `geo` is a parameter with a fixed
default rather than a constant only so the archive key spells it out --
`Tesla.geo-US` -- and a future non-US series would be a new key, not a rewrite
of this one. `tz=0` and `hl=en-US` are pinned for the same reason: week
bucketing follows the requested timezone, and a determinism claim cannot
depend on the server guessing one.
"""
import json
import os
import re
import threading
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone

import requests

HOME = "https://trends.google.com/"
EXPLORE = "https://trends.google.com/trends/api/explore"
MULTILINE = "https://trends.google.com/trends/api/widgetdata/multiline"

# A browser-shaped User-Agent, unlike the honest research UA the other adapters
# send. Not a disguise for its own sake: the endpoints are the web UI's own,
# they are only served to things that look like the web UI, and the homepage
# will not hand the NID cookie to a bare script. The repository remains the
# public record of what was fetched and how.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

WINDOW = "today 12-m"          # see design constraint 1: never parameterized
GEO = "US"
TIMEOUT = 60
WEEK_SECONDS = 7 * 24 * 3600

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARCHIVE = os.path.join(ROOT, "trends")

# One request in flight at a time, spaced far apart. Ten seconds is not
# politeness theatre: three-second spacing drew a 429 on the second widgetdata
# call during the feasibility probes. The backoff is minutes-scale because the
# throttle also surfaces as a dropped TLS connection on the transport path,
# and retrying that in seconds just spends the next attempt while still hot.
MIN_INTERVAL = 10.0
MAX_RETRIES = 3
BACKOFF = 45.0                     # seconds, multiplied by attempt number
_last_call = [0.0]
_call_lock = threading.Lock()


# --- fetching ----------------------------------------------------------------

def _get(session, url, params=None):
    """One spaced, serialized GET. Transport errors retry; HTTP errors do not.

    The split matters here more than on any other adapter. A dropped connection
    is this host's rate limiter as often as it is the network, so it gets a
    long backoff and another chance. A 429 is a decision the server has already
    made about this IP for the next while; retrying it inside one refresh run
    burns goodwill and stalls the run past lock margins, so it raises at once
    and the retry happens for free at the next six-hourly refresh, from a
    process the throttle has had hours to forget.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        with _call_lock:
            wait = MIN_INTERVAL - (time.monotonic() - _last_call[0])
            if wait > 0:
                time.sleep(wait)
            try:
                r = session.get(url, params=params, timeout=TIMEOUT)
            except requests.RequestException as e:
                _last_call[0] = time.monotonic()
                if attempt == MAX_RETRIES:
                    raise RuntimeError(
                        f"Google Trends unreachable after {MAX_RETRIES} "
                        f"attempts ({type(e).__name__}: {e}): {url}") from e
                time.sleep(BACKOFF * attempt)
                continue
            _last_call[0] = time.monotonic()
        if r.status_code == 429:
            raise RuntimeError(
                "Google Trends rate-limited this address (HTTP 429). Not "
                "retried in-process: the next scheduled refresh retries, and "
                "registered series serve their archive until then.")
        r.raise_for_status()
        return r.text
    raise RuntimeError(f"Google Trends unreachable: {url}")


def strip_junk(text):
    """Drop the anti-JSON prefix line (`)]}'` or `)]}',`) if present.

    Google prepends it to keep the payload from being readable as a script
    tag; the exact spelling differs between the two endpoints, so this keys on
    the prefix rather than a full match.
    """
    head, sep, rest = text.partition("\n")
    if sep and head.strip().startswith(")]}'"):
        return rest
    return text


def parse_widgets(text):
    """The widget descriptors out of an explore response."""
    try:
        obj = json.loads(strip_junk(text))
    except json.JSONDecodeError as e:
        raise RuntimeError(
            "Google Trends explore response did not parse as JSON; the "
            "endpoint contract changed and this adapter needs revisiting") from e
    widgets = obj.get("widgets") or []
    if not widgets:
        raise RuntimeError("Google Trends explore response carries no widgets")
    return widgets


def timeseries_widget(widgets):
    """The TIMESERIES widget, whose token unlocks the multiline endpoint."""
    for w in widgets:
        if w.get("id") == "TIMESERIES" and w.get("token") and w.get("request"):
            return w
    raise RuntimeError(
        "no TIMESERIES widget in the Google Trends explore response; it "
        f"offered {[w.get('id') for w in widgets]}")


def _iso(epoch_seconds):
    """Row timestamps are epoch seconds at the week's start, UTC (tz=0)."""
    return datetime.fromtimestamp(int(epoch_seconds), timezone.utc).date()


def parse_timeline(text):
    """Multiline JSON -> weekly rows, oldest first, verified weekly.

    Each row: {week_start, week_end, value, partial}, dates as ISO strings.
    The verifications are the endpoint's contract written as code:

    - rows exist (an empty timeline published as a series would be a tracker
      that does not exist);
    - every `value` is a one-element list, because this module only ever sends
      one comparison item -- a longer list means the request was not the one
      this adapter builds, and its numbers would be jointly normalized;
    - consecutive rows are exactly seven days apart, because constraint 2 says
      weekly rows are taken natively or not at all.
    """
    try:
        obj = json.loads(strip_junk(text))
    except json.JSONDecodeError as e:
        raise RuntimeError(
            "Google Trends multiline response did not parse as JSON; the "
            "endpoint contract changed and this adapter needs revisiting") from e
    rows = ((obj.get("default") or {}).get("timelineData")) or []
    if not rows:
        raise RuntimeError(
            "Google Trends returned an empty timeline; refusing to build an "
            "empty series")
    out, prev = [], None
    for r in rows:
        t = int(r["time"])
        if prev is not None and t - prev != WEEK_SECONDS:
            raise RuntimeError(
                f"Google Trends rows are {t - prev}s apart, not weekly; the "
                "12-month window stopped returning weekly rows and this "
                "adapter must not resample its way past that")
        prev = t
        vals = r.get("value") or []
        if len(vals) != 1:
            raise RuntimeError(
                f"Google Trends row carries {len(vals)} values; this adapter "
                "sends one keyword per request precisely so each series is "
                "normalized to itself")
        start = _iso(t)
        out.append({
            "week_start": start.isoformat(),
            "week_end": (start + timedelta(days=6)).isoformat(),
            "value": vals[0],
            # Only the trailing in-progress week carries isPartial, and older
            # rows omit the key rather than writing false. Read permissively,
            # store definitively.
            "partial": bool(r.get("isPartial")),
        })
    return out


def fetch_timeline(query, geo=GEO):
    """One full request cycle for one query, on a fresh session.

    Fresh because the NID cookie and the widget token are the unit the
    throttle reasons about: reusing a session across queries is exactly the
    shape that drew a 429 in the probes, while one cycle per session did not.
    """
    session = requests.Session()
    session.headers.update({"User-Agent": UA,
                            "Accept-Language": "en-US,en;q=0.9"})
    _get(session, HOME)                       # sets the NID cookie
    req = {"comparisonItem": [{"keyword": query, "geo": geo, "time": WINDOW}],
           "category": 0, "property": ""}
    widgets = parse_widgets(_get(
        session, EXPLORE,
        {"hl": "en-US", "tz": "0", "req": json.dumps(req)}))
    ts = timeseries_widget(widgets)
    body = _get(session, MULTILINE,
                {"hl": "en-US", "tz": "0",
                 "req": json.dumps(ts["request"]), "token": ts["token"]})
    return parse_timeline(body)


# --- the archive -------------------------------------------------------------

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def archive_key(query, geo=GEO):
    """Directory name for one (query, geo) pair: `Tesla.geo-US`.

    Same construction and same sanitizer as `civiqs.archive_key`, so anyone
    who can audit one archive can audit the other.
    """
    return _SAFE.sub("_", f"{query}.geo-{geo}")


def archive_dir(key):
    return os.path.join(ARCHIVE, key)


def archive_days(key):
    """Fetch dates we hold for this key, oldest first."""
    d = archive_dir(key)
    if not os.path.isdir(d):
        return []
    out = []
    for fn in os.listdir(d):
        if fn.endswith(".json") and len(fn) == 15:
            try:
                out.append(date.fromisoformat(fn[:10]))
            except ValueError:
                continue
    return sorted(out)


def read_archive(key, day):
    path = os.path.join(archive_dir(key), f"{day.isoformat()}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def public_url(query, geo=GEO):
    """The UI page a human would check this snapshot against."""
    q = urllib.parse.urlencode({"q": query, "geo": geo, "date": WINDOW},
                               quote_via=urllib.parse.quote)
    return f"https://trends.google.com/trends/explore?{q}"


def build_snapshot(rows, query, geo, fetched_at):
    """The record written to disk for one fetch.

    The full window is kept every day, partial trailing week included --
    fifty-three short rows, a couple of kilobytes, so unlike Civiqs there is
    no full-versus-tail split to manage. Committing every vintage is also the
    only way the sampling jitter and window renormalization stay *measurable*
    from the repository instead of being this docstring's word.
    """
    complete = [r for r in rows if not r["partial"]]
    if not complete:
        raise RuntimeError(
            f"Google Trends snapshot for {query!r} holds no complete week; "
            "refusing to archive a record that could never resolve anything")
    return {
        "query": query,
        "geo": geo,
        "window": WINDOW,
        "resolution": "WEEK",
        "url": public_url(query, geo),
        "fetched_at": fetched_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        # The newest date the data speaks for: the end of the newest complete
        # week, read off the rows. Never the fetch day -- a fetch made on a
        # Wednesday holds data through last Saturday, and labelling it
        # Wednesday would claim four days nobody measured.
        "asof": max(r["week_end"] for r in complete),
        "unit": "search interest index (0-100, 12-month window)",
        # [week_start, week_end, value, partial] -- same information as the
        # dict rows, a third of the bytes, and this file is written daily.
        "points": [[r["week_start"], r["week_end"], r["value"], r["partial"]]
                   for r in rows],
    }


def write_snapshot(key, snap, day):
    os.makedirs(archive_dir(key), exist_ok=True)
    path = os.path.join(archive_dir(key), f"{day.isoformat()}.json")
    with open(path, "w") as f:
        json.dump(snap, f, indent=1, sort_keys=True, ensure_ascii=False)
        f.write("\n")
    return path


def snapshot(query, geo=GEO, now=None, use_archive=True):
    """Today's snapshot for a key: read it, or fetch it and write it.

    Once a day's file exists the day is closed -- no vintage re-check, unlike
    `civiqs.snapshot`. Civiqs re-checks because its upstream publishes a new
    model run nightly and the 00:17 refresh predates it; here a new completed
    week lands weekly, the value it lands with is whatever the first snapshot
    to see it says (that is the resolution rule, not an approximation of it),
    and every extra request is 429 budget. A fetch that ran before Google
    rolled the week simply archives a window one week older, and tomorrow's
    file picks the new week up.
    """
    now = now or datetime.now(timezone.utc)
    today = now.date()
    key = archive_key(query, geo)
    if use_archive:
        have = read_archive(key, today)
        if have:
            return have
    snap = build_snapshot(fetch_timeline(query, geo), query, geo, fetched_at=now)
    if use_archive:
        write_snapshot(key, snap, today)
    return snap


# --- what a registered series reads ------------------------------------------

def as_archived(query, geo=GEO, now=None, fetch=True, use_archive=True):
    """The archive's own series: [{date, value}] oldest first.

    One point per *completed* week, dated by the week's last day (the
    Saturday), valued at whatever the earliest snapshot that contains that
    completed week showed. That is the whole rule. Its consequences:

    - **Points never move.** Later snapshots may re-read the same week a
      point or two differently (sampling) or on a shifted scale (a new peak
      entering the trailing window); they lose to the snapshot that got there
      first, so a round's frozen history is still true at resolution time.
      This is `civiqs.as_displayed`'s rule with the day loop removed, because
      the rows are already the cadence the questions ask about.

    - **Dating by week end keeps the resolver honest.** `resolve.candidate`
      answers a round with the first (date, value) pair the frozen history
      did not contain. A completed week dated by its Saturday enters the
      series only after that Saturday has passed, so the pair it offers the
      resolver is a genuinely new observation, not a relabelled old one. Dated
      by week start, the newest point would carry a date eight days in its own
      past and sort into history the lock snapshot had already frozen.

    - **Days before the archive began are backfilled from the first
      snapshot**, which carries a year of completed weeks and is the only
      record of them this repository holds -- the same bootstrap Civiqs uses.

    `fetch=False` builds from the archive alone and touches no network, which
    is what tests and any rerun over committed data want.
    """
    now = now or datetime.now(timezone.utc)
    key = archive_key(query, geo)
    if fetch:
        try:
            snapshot(query, geo, now=now, use_archive=use_archive)
        except Exception as e:                    # noqa: BLE001 - reported
            # The civiqs trade, for the same reason: a refresh that dies here
            # files nothing for any tracker, and rounds lock on a hard
            # deadline. Stale beats dark -- but only when there is an archive
            # to be stale from.
            if not archive_days(key):
                raise
            print(f"  trends {key}: fetch failed ({type(e).__name__}: {e}); "
                  "serving the archive, which may be a day behind")

    days = archive_days(key)
    if not days:
        raise RuntimeError(
            f"no Trends archive for {key} -- refusing to publish an empty "
            f"series. Run a refresh, or check {archive_dir(key)}")

    by_week = {}
    for d in days:                                # oldest snapshot first
        snap = read_archive(key, d)
        if not snap:
            continue
        for start, end, value, partial in snap.get("points", []):
            if partial or end in by_week:
                continue                          # earliest snapshot wins
            by_week[end] = value
    if not by_week:
        raise RuntimeError(
            f"Trends archive for {key} holds no completed weeks; refusing to "
            "publish an empty series")
    return [{"date": d, "value": by_week[d]} for d in sorted(by_week)]
