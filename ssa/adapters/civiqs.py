"""Civiqs daily trackers, and the archive that makes them resolvable at all.

Civiqs publishes ~24 national trackers of registered voters -- Trump approval,
the economy, abortion, guns, four separate AI-attitude questions -- each as a
daily series back to 2025-01-20. There is no API. The numbers are embedded in
the page as the Remix loader payload, so the adapter fetches the HTML and reads
the JSON out of it:

    GET https://civiqs.com/results/<name>[?<label>=<value>]

    "routes/_app.results_.$question": { ...the object this module parses... }

**Civiqs is a modeled tracker, not a survey wave, and that changes everything
downstream.** YouGov and Morning Consult publish a wave: a fresh sample, a fresh
weighting, a number that stands forever. Civiqs runs an MRP model over a rolling
panel and republishes *the entire daily history* every night, so the number
printed against 2025-06-06 today is not the number that was printed against
2025-06-06 in June 2025. Two consequences, both load-bearing here:

1. **Without an archive, a Civiqs round can never be checked.** The season file
   says these rounds resolve against "the value on Friday, recorded manually
   with a screenshot"; a screenshot is not a pipeline. So every fetch is written
   into `civiqs/` at the repository root, one immutable file per (tracker,
   filters, fetch date), carrying what the dashboard actually showed that day.
   That directory is the evidence. Delete it and no Civiqs resolution in this
   repository is auditable, including ones already written.

2. **Persistence is an extremely strong baseline on this source, and that is
   correct, not a bug.** Measured on the daily Trump approval series, mean
   absolute day-to-day change is 0.069 points and `scoring.noise_floor` returns
   a measurement noise of 0.000 -- there is no publication noise to speak of,
   because the published series is a smoother's output rather than a sample.
   An entrant will find it very hard to beat "yesterday's number" here. Do not
   compensate by roughening the series or by scoring it on a different scale:
   the arena's whole claim is that the null is honest, and a target where the
   null is nearly unbeatable is a true fact about that target.

**What a point in a registered series means.** Not "Civiqs's current estimate
for day d" -- that value is revised nightly and would make a resolution
un-recheckable. Instead: *the freshest reading available on day d, according to
the earliest snapshot we hold that was taken on or after d.* For a day we
archived, that is literally the number on the dashboard when we looked. For a
day before the archive begins it is the estimate for that day as recorded in our
first snapshot, which is the only record that exists. One rule, and it never
changes its answer once written, because archives are only ever added forward in
time.

**The dashboard runs a day behind.** `job_finish_time` on 2026-08-12 was
01:43 UTC and `end_date` was 2026-08-11, so the newest point is dated yesterday
and the model rolls over in the small hours UTC. Every snapshot records both
fields rather than assuming a lag, and the "freshest reading available on day d"
rule above reads whichever date the snapshot actually had.

**Subgroup filtering keys on the demographic's `label`, not its predictor id.**
`?party=Republican` works and returns a full 569-point daily series;
`?party_3=Republican` is silently ignored and returns the national series under
a subgroup's name, which is the worst failure mode available here. So a filtered
fetch is verified: an applied filter makes `topline.filtered_topline` differ
from `topline.unfiltered_topline`, and a filter that did not take is a hard
error rather than a plausible-looking national number.

**Rate limiting is real.** Rapid sequential requests return
SSLError(SSLZeroReturnError). Requests are serialized with a minimum spacing and
retried with backoff, as `newsdigest` already does for Wikipedia. Each page is
~2 MB, so the archive is also the fetch cache: once a day's snapshot exists,
that day costs no requests at all, and the six-hourly refresh fetches each
tracker once per day rather than four times.
"""
import json
import os
import re
import threading
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone

import requests

BASE = "https://civiqs.com/results/"
UA = "social-simulation-arena/1.0 (research benchmark; contact via repository)"
TIMEOUT = 120

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARCHIVE = os.path.join(ROOT, "civiqs")

# The Remix route keys the payloads hang off. The question route is spelled with
# a literal `$question` -- it is the route file name, not a template hole.
QUESTION_ROUTE = "routes/_app.results_.$question"
INDEX_ROUTE = "routes/_app._index"

# How much daily history each snapshot keeps. The first snapshot for a tracker
# is written in full (29 KB, 569 days) and is the backfill base for everything
# before the archive began; later ones keep a tail, because a full history
# committed daily is ~10 MB a year of churn for data that differs only in its
# last few points. Sixty days matches `refresh.LOCK_SNAPSHOT_POINTS` for the
# same reason -- it is bounded and lossless in practice -- and it leaves sixty
# overlapping vintages of every day, which is what makes Civiqs's nightly
# revisions measurable from the repository rather than merely asserted.
SNAPSHOT_POINTS = 60

# One request in flight at a time, spaced. Measured the hard way: bursts of
# sequential requests to this host come back as SSLZeroReturnError rather than
# as an HTTP status, so there is nothing to read a Retry-After off.
MIN_INTERVAL = 0.6
MAX_RETRIES = 4
BACKOFF = 3.0                      # seconds, multiplied by attempt number
_last_call = [0.0]
_call_lock = threading.Lock()

# Per-process memo of fetched pages, keyed the same way the archive is: two
# series over one page must cost one request, and a run must never ask for the
# same URL twice. Entries carry the time they were taken and expire after
# RECHECK_SECONDS, so the memo never outlives the vintage check below -- keyed
# on the day alone it silently pinned a long-lived process to the first page it
# ever saw, and the nightly re-check became a no-op that still spent a request.
_payloads = {}
_payload_lock = threading.Lock()


# --- fetching ---------------------------------------------------------------

def tracker_url(name, filters=None):
    """The page URL, with subgroup filters as `label=value` query parameters.

    Values are URL-encoded, which matters: the `65+` age bucket has to go over
    the wire as `65%2B` or the plus is read as a space and the filter misses.
    """
    if not filters:
        return BASE + name
    q = urllib.parse.urlencode(sorted(filters.items()), quote_via=urllib.parse.quote)
    return f"{BASE}{name}?{q}"


def _get(url):
    for attempt in range(1, MAX_RETRIES + 1):
        with _call_lock:
            wait = MIN_INTERVAL - (time.monotonic() - _last_call[0])
            if wait > 0:
                time.sleep(wait)
            try:
                r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
            except requests.RequestException as e:
                _last_call[0] = time.monotonic()
                if attempt == MAX_RETRIES:
                    raise RuntimeError(
                        f"Civiqs unreachable after {MAX_RETRIES} attempts "
                        f"({type(e).__name__}: {e}): {url}") from e
                time.sleep(BACKOFF * attempt)
                continue
            _last_call[0] = time.monotonic()
        if r.status_code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES:
            time.sleep(BACKOFF * attempt)
            continue
        r.raise_for_status()
        return r.text
    raise RuntimeError(f"Civiqs unreachable: {url}")


def _page(key, url, now):
    """The page for one key, fetched at most once per RECHECK_SECONDS per run.

    `now` rather than the wall clock, so the expiry is driven by the same time
    the rest of the module reasons about and a caller can test it.
    """
    with _payload_lock:
        got = _payloads.get(key)
        if got and (now - got[0]).total_seconds() < RECHECK_SECONDS:
            return got[1]
    html = _get(url)
    with _payload_lock:
        _payloads[key] = (now, html)
    return html


# --- parsing the embedded payload -------------------------------------------

def extract_route(html, route):
    """The JSON object a Remix route key points at, or None.

    Brace-balanced from the first `{` after the key, honouring string literals
    and escapes -- the payload contains question text with braces and quotes in
    it, so a regex that stops at the first `}` truncates mid-object. There is no
    `<script>` boundary to key off either: the loader data is streamed inline.
    """
    marker = f'"{route}":'
    i = html.find(marker)
    if i < 0:
        return None
    j = html.find("{", i + len(marker))
    if j < 0:
        return None
    depth, k, in_str, esc = 0, j, False, False
    while k < len(html):
        c = html[k]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return json.loads(html[j:k + 1])
        k += 1
    return None


def parse_payload(html, url=""):
    """The tracker payload, or a hard error.

    An unrecognised slug does not 404 -- Civiqs serves the index page instead --
    so "no question payload" usually means the tracker name is wrong rather than
    that the site is down, and the message says so. Returning an empty series
    here would publish a tracker that does not exist.
    """
    where = url or "this request"
    obj = extract_route(html, QUESTION_ROUTE)
    if obj is None:
        if extract_route(html, INDEX_ROUTE) is not None:
            raise RuntimeError(
                f"Civiqs served its index page for {where}: the tracker name is "
                "not recognised. `trackers()` lists the valid ones.")
        raise RuntimeError(
            f"no Civiqs tracker payload in the response for {where}; the page "
            "structure changed and the parser needs revisiting")
    if not (obj.get("topline") or {}).get("line_chart_data"):
        raise RuntimeError(
            f"Civiqs payload for {where} carries no line_chart_data; refusing "
            "to build an empty series")
    return obj


def parse_index(html):
    """The tracker catalogue: [{name, display_text, population_model}]."""
    obj = extract_route(html, INDEX_ROUTE)
    rows = (obj or {}).get("results") or []
    out = []
    for r in rows:
        jd = r.get("job_description") or {}
        if not jd.get("name"):
            continue
        out.append({
            "name": jd["name"],
            "display_text": jd.get("display_text"),
            "population_model": jd.get("population_model"),
            "end_date": r.get("end_date"),
            "predictors": jd.get("predictor_list") or [],
        })
    if not out:
        raise RuntimeError(
            "Civiqs index parsed to zero trackers; refusing to report an empty "
            "catalogue (check whether the page structure changed)")
    out.sort(key=lambda r: r["name"])
    return out


def filters_applied(payload):
    """True when the request's subgroup filters actually took effect.

    Civiqs ignores an unrecognised filter silently and serves the national
    series, so this is the only way to tell `?party=Republican` (works) from
    `?party_3=Republican` (does not) without a second request to compare
    against. On an unfiltered page the two toplines are equal; on a filtered one
    the unfiltered copy is retained alongside the filtered result.
    """
    tl = payload.get("topline") or {}
    return tl.get("unfiltered_topline") != tl.get("filtered_topline")


def demographics(payload):
    """{label: [values]} -- the axes this tracker can be filtered on.

    Keyed by `label`, because the label is what the query parameter takes. The
    predictor id (`party_3`, `age_4`, `home_state`) is what the payload calls
    the same axis internally and is *not* accepted as a parameter name.
    """
    return {d["label"]: list(d.get("values") or [])
            for d in (payload.get("demographics") or []) if d.get("label")}


def choices(payload):
    return [s["key"] for s in payload["topline"]["line_chart_data"]]


def _iso(ms):
    """Civiqs dates are epoch milliseconds at UTC midnight."""
    return datetime.fromtimestamp(ms / 1000.0, timezone.utc).date().isoformat()


def to_points(payload, choice):
    """One choice's history as [{date, value}] oldest first, in points.

    **The payload is in fractions.** 0.351 is 35.1 percent, and a series handed
    to the scorer in fractions would be off by two orders of magnitude in every
    CRPS, quietly, while still looking like a plausible chart.
    """
    for s in payload["topline"]["line_chart_data"]:
        if s["key"] == choice:
            out = [{"date": _iso(v["date"]), "value": round(v["value"] * 100, 2)}
                   for v in s["values"]]
            out.sort(key=lambda p: p["date"])
            if not out:
                raise RuntimeError(f"Civiqs choice {choice!r} has no values")
            return out
    raise KeyError(f"no Civiqs choice {choice!r}; this tracker has "
                   f"{choices(payload)}")


def to_net(payload, minuend=None, subtrahend=None):
    """Approve minus disapprove, in points, oldest first.

    Computed from the two choice series rather than read from `topline.net_data`
    -- Civiqs publishes that field as a float difference of fractions
    (-0.056999999999999995), and the arena rounds once, at a defined place, so
    the same input always yields the same committed number. Which two choices to
    subtract comes from the tracker's own `display_net` when it declares one, so
    this works on favourability and right-track trackers too.
    """
    net = (payload.get("job_description") or {}).get("display_net") or {}
    a = minuend or net.get("minuend") or "Approve"
    b = subtrahend or net.get("subtrahend") or "Disapprove"
    plus = {p["date"]: p["value"] for p in to_points(payload, a)}
    minus = {p["date"]: p["value"] for p in to_points(payload, b)}
    both = sorted(set(plus) & set(minus))
    if not both:
        raise RuntimeError(f"Civiqs net {a} minus {b}: no overlapping dates")
    return [{"date": d, "value": round(plus[d] - minus[d], 2)} for d in both]


# --- the archive ------------------------------------------------------------

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def archive_key(name, filters=None):
    """Directory name for one (tracker, filters) pair.

    Filters are sorted so `{a, b}` and `{b, a}` are the same directory rather
    than two half-populated ones.
    """
    parts = [name]
    for k, v in sorted((filters or {}).items()):
        parts.append(f"{k}-{v}")
    return _SAFE.sub("_", ".".join(parts))


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


def build_snapshot(payload, name, filters, fetched_at, full):
    """The record written to disk for one fetch.

    All three choices are kept, not just the one a series happens to read: the
    marginal cost is a few kilobytes and the alternative is re-deriving a number
    that no longer exists. `full` writes the whole history -- true only for the
    first snapshot of a key, which is the backfill base for every day before the
    archive began.
    """
    ch = choices(payload)
    cols = [{p["date"]: p["value"] for p in to_points(payload, c)} for c in ch]
    dates = sorted(set().union(*cols)) if cols else []
    if not dates:
        raise RuntimeError(f"Civiqs snapshot for {name} has no dated points")
    if not full:
        dates = dates[-SNAPSHOT_POINTS:]
    jd = payload.get("job_description") or {}
    return {
        "tracker": name,
        "filters": dict(filters or {}),
        "url": tracker_url(name, filters),
        "fetched_at": fetched_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        # Civiqs's own provenance for the model run behind these numbers. The
        # pair (run_id, end_date) is what identifies a vintage; `end_date` is
        # the newest day the run covers and is what "the freshest reading
        # available" resolves to, so it is read rather than assumed.
        "run_id": payload.get("run_id"),
        "job_finish_time": payload.get("job_finish_time"),
        "end_date": payload.get("end_date"),
        "sample_size": payload.get("sample_size"),
        "population_model": jd.get("population_model"),
        "question_body": payload.get("question_body"),
        "display_text": jd.get("display_text"),
        "unit": "percentage points",
        "full_history": bool(full),
        "choices": ch,
        # [date, v0, v1, ...] rather than a dict per point: same information,
        # a quarter of the bytes, and this file is committed four times a day.
        "points": [[d] + [c.get(d) for c in cols] for d in dates],
    }


def snapshot_series(snap, choice):
    """{date: value} for one choice out of an archived snapshot."""
    try:
        i = snap["choices"].index(choice)
    except ValueError:
        raise KeyError(f"archived snapshot has no choice {choice!r}; it has "
                       f"{snap['choices']}") from None
    return {row[0]: row[i + 1] for row in snap["points"] if row[i + 1] is not None}


def write_snapshot(key, snap, day):
    os.makedirs(archive_dir(key), exist_ok=True)
    path = os.path.join(archive_dir(key), f"{day.isoformat()}.json")
    with open(path, "w") as f:
        json.dump(snap, f, indent=1, sort_keys=True, ensure_ascii=False)
        f.write("\n")
    return path


def snapshot(name, filters=None, now=None, use_archive=True):
    """Today's snapshot for a key: read it, or fetch it and write it.

    The first refresh of a day fetches; the other three read the file. A later
    fetch on the same day replaces the file only when Civiqs has published a
    newer `end_date` since, so a day's record converges on the freshest thing
    the dashboard showed that day and never walks backwards.

    Rewriting today's file cannot disturb a frozen round: `refresh.build_rounds`
    filters history to `date < lock_at[:10]`, so the lock day itself is never in
    any round's frozen history.
    """
    now = now or datetime.now(timezone.utc)
    today = now.date()
    key = archive_key(name, filters)
    have = read_archive(key, today) if use_archive else None
    if have and not _stale(have, now):
        return have
    url = tracker_url(name, filters)
    payload = parse_payload(_page(key, url, now), url)
    if filters and not filters_applied(payload):
        raise RuntimeError(
            f"Civiqs ignored the subgroup filter {filters!r} on {name} and "
            "served the national series. Filters key on the demographic's "
            f"label, not its predictor id -- this tracker accepts "
            f"{sorted(demographics(payload))}.")
    if have and (payload.get("end_date") or "") <= (have.get("end_date") or ""):
        return have               # nothing newer published today; keep the file
    # Whether this key's backfill base exists is decided by the days *before*
    # today: replacing today's own file must not silently demote the very first
    # snapshot, which is the only full history in the archive.
    full = have["full_history"] if have else not archive_days(key)
    snap = build_snapshot(payload, name, filters, fetched_at=now, full=full)
    if use_archive:
        write_snapshot(key, snap, today)
    return snap


# Two processes on this path run back to back in CI: `ssa.refresh` writes the
# day's snapshot and `ssa.resolve` then rebuilds the same series minutes later.
# They do not share the in-process memo, so a purely date-based staleness rule
# made the 00:17 UTC cron fetch every tracker twice -- the run at that hour
# always finds an end_date two days back, because Civiqs's own job finishes
# around 01:43 UTC. Re-checking at most once per cron interval collapses that
# back to one request while still picking up the night's vintage at 06:17.
RECHECK_SECONDS = 3 * 3600


def _stale(snap, now):
    """True when today's stored snapshot is behind and worth one re-check.

    Behind means Civiqs has probably published a newer nightly run than the one
    on disk; the second clause is the anti-thrash guard described above.
    """
    end = snap.get("end_date") or ""
    if end >= (now.date() - timedelta(days=1)).isoformat():
        return False                      # already carries the latest vintage
    try:
        got = datetime.strptime(snap["fetched_at"], "%Y-%m-%dT%H:%M:%SZ")
    except (KeyError, TypeError, ValueError):
        return True                       # undated file: re-check it
    age = (now - got.replace(tzinfo=timezone.utc)).total_seconds()
    return age >= RECHECK_SECONDS


# --- what a registered series actually reads --------------------------------

def as_displayed(name, filters=None, choice=None, net=False, weekday=None,
                 now=None, fetch=True, use_archive=True):
    """The archive's own series: [{date, value}] oldest first, in points.

    `date` is the day we (or, before the archive began, Civiqs) would have shown
    the number on, and `value` is *the freshest reading available on that day,
    according to the earliest snapshot taken on or after it*. That is the one
    rule; everything else here is bookkeeping for it.

    Why not just publish Civiqs's current daily history? Because it is rewritten
    nightly. A round frozen against last week's history would find every one of
    its frozen points changed by resolution time, and `ssa.resolve` identifies
    the answer as the first (date, value) pair the frozen history did not
    contain -- so a revised *old* point would be handed over as this week's
    release. The archive is what makes those points immutable.

    `weekday` (Monday=0) samples the series, and the round-facing registrations
    use Friday. This is not cosmetic. `ssa.resolve` answers a round with the
    first observation after the freeze, so on a daily series a Wednesday lock
    and a Friday release resolve against *Thursday's* number. Sampling the
    series at the cadence the question asks about is what makes the resolver's
    "next release" mean the release the round names -- and it also puts the
    persistence null a week back rather than a day back, which is the only
    honest null for a question asked a week ahead.

    `fetch=False` builds from the archive alone and touches no network, which is
    what tests and any rerun over committed data want.
    """
    now = now or datetime.now(timezone.utc)
    key = archive_key(name, filters)
    if fetch:
        try:
            snapshot(name, filters, now=now, use_archive=use_archive)
        except Exception as e:                     # noqa: BLE001 - reported
            # Same trade as series.michigan_history: take the extra day when the
            # source is there, never go dark when it is not. An archive that
            # stops one day short is a stale series; a refresh that dies here
            # files no forecasts at all for any tracker, and rounds lock on a
            # hard deadline.
            if not archive_days(key):
                raise
            print(f"  civiqs {key}: fetch failed ({type(e).__name__}: {e}); "
                  "serving the archive, which is now a day behind")

    days = archive_days(key)
    if not days:
        raise RuntimeError(
            f"no Civiqs archive for {key} -- refusing to publish an empty "
            f"series. Run a refresh, or check {archive_dir(key)}")

    snaps = []
    for d in days:
        snap = read_archive(key, d)
        if not snap:
            continue
        vals = (to_net_from_snapshot(snap) if net
                else snapshot_series(snap, choice or "Approve"))
        end = snap.get("end_date") or (max(vals) if vals else None)
        if vals and end:
            snaps.append((d, end, vals))
    if not snaps:
        raise RuntimeError(f"Civiqs archive for {key} holds no readable "
                           "snapshots; refusing to publish an empty series")

    first_day, _, first_vals = snaps[0]
    start = date.fromisoformat(min(first_vals))
    out, i = [], 0
    d = start
    while d <= snaps[-1][0]:
        if weekday is None or d.weekday() == weekday:
            while i < len(snaps) and snaps[i][0] < d:
                i += 1
            if i < len(snaps):
                fetch_day, end, vals = snaps[i]
                v = vals.get(min(end, d.isoformat()))
                # A missing date means an archive gap longer than a snapshot's
                # tail. Skipped rather than raised: a sampled tracker with a
                # hole is a series short one observation, and killing the whole
                # refresh over a months-old gap helps nobody. An empty result
                # still raises, below.
                if v is not None:
                    out.append({"date": d.isoformat(), "value": v})
        d += timedelta(days=1)
    if not out:
        raise RuntimeError(
            f"Civiqs series {key} built to zero points -- refusing to publish "
            "an empty series (check the weekday filter against the archive)")
    return out


def to_net_from_snapshot(snap):
    """{date: approve - disapprove} out of an archived snapshot, in points."""
    ch = snap["choices"]
    a = "Approve" if "Approve" in ch else ch[0]
    b = "Disapprove" if "Disapprove" in ch else ch[1]
    plus, minus = snapshot_series(snap, a), snapshot_series(snap, b)
    return {d: round(plus[d] - minus[d], 2) for d in set(plus) & set(minus)}


def archive_start(name, filters=None):
    """First day we hold a snapshot for, or None. Everything before it in a
    registered series is backfilled from that first snapshot and therefore
    carries whatever revisions Civiqs had already applied by then."""
    days = archive_days(archive_key(name, filters))
    return days[0].isoformat() if days else None


# --- the plain, current-vintage view ----------------------------------------
#
# These read whatever Civiqs is publishing right now. They are for exploration,
# for measuring a candidate subgroup before registering it, and for the site --
# never for resolution, which goes through `as_displayed` and the archive.

def payload(name, filters=None, now=None):
    """The parsed loader payload for one tracker, one request per key per run.

    Deliberately does not read `civiqs/`: the archive stores three choice series
    and their provenance, not the crosstab block or the demographic list, so
    serving it here would answer a question about the payload with something
    that is not the payload.
    """
    now = now or datetime.now(timezone.utc)
    key = archive_key(name, filters)
    url = tracker_url(name, filters)
    obj = parse_payload(_page(key, url, now), url)
    if filters and not filters_applied(obj):
        raise RuntimeError(
            f"Civiqs ignored the subgroup filter {filters!r} on {name}; it "
            f"accepts {sorted(demographics(obj))}")
    return obj


def series(name, choice="Approve", **filters):
    """Currently published daily history, [{date, value}] oldest first, points."""
    return to_points(payload(name, filters or None), choice)


def net_approval(name, **filters):
    """Currently published approve-minus-disapprove, oldest first, in points."""
    return to_net(payload(name, filters or None))


def trackers(now=None, use_archive=True):
    """The Civiqs catalogue: [{name, display_text, population_model, ...}].

    Requesting an unrecognised slug serves the index page, which is where the
    catalogue lives. The response is ~10 MB, so the parsed list is archived
    daily and a rerun costs nothing; this is a discovery tool and is
    deliberately not on any refresh path.
    """
    now = now or datetime.now(timezone.utc)
    day = now.date()
    have = read_archive("_index", day) if use_archive else None
    if have:
        return have["trackers"]
    rows = parse_index(_get(BASE + "_ssa_catalogue_probe"))
    body = {"fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "trackers": rows}
    if use_archive:
        write_snapshot("_index", body, day)
    return rows
