"""Wikimedia pageview counts -- the arena's first behavioral series.

Everything else in the registry measures what people *say*: a survey wave, or
a model over survey waves. This counts what they *do*. The Wikimedia Pageviews
REST API publishes, per article and per day, how many times the article was
requested:

    GET https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/
        en.wikipedia/all-access/user/{article}/daily/{YYYYMMDD00}/{YYYYMMDD00}

Free and keyless, with one courtesy the API enforces: a descriptive User-Agent.
Wikimedia rejects requests bearing a bare library UA outright, so the header
below is load-bearing, not decoration.

A day's count is computed once from the request logs and never revised, which
makes this the simplest resolution source in the arena: no house effects, no
nightly re-modelling, no archive needed to keep the past from moving. Three
choices here are deliberate:

- **`user` traffic only.** The access logs classify every request as user,
  spider, or automated, and this adapter asks for the `user` slice alone. Bot
  traffic is noise for this target: a scraper re-crawling the wiki moves the
  raw count without a single human having cared, and the question the arena
  asks is about human attention. The classifier is the source's own and it is
  applied uniformly across the whole history, so the split does not move under
  a series the way a changed filter would.

- **Complete Monday-Sunday weeks only.** Daily counts swing hard by weekday,
  so the forecastable quantity is the weekly total, dated by the Sunday it
  ends. A week is emitted only when all seven of its days came back from the
  API. That drops the trailing week still being counted, the leading fragment
  when the history starts mid-week, and any week with a hole in it -- a
  six-day sum published as a weekly total reads as a one-seventh traffic
  collapse that never happened.

- **Freshness is read off the response, never the clock.** The request's end
  bound is today, and that is the only place the wall clock appears: it bounds
  what may be asked for, never what is claimed to exist. The API runs a day or
  two behind the present while the logs aggregate, so the last day it actually
  returned is the source's real as-of, and week completeness downstream is
  decided entirely by which days arrived. A series built at 23:59 and one
  built at 00:01 therefore agree.

Values are thousands of views to one decimal. The raw weekly count for a big
article is seven digits, and nobody can eyeball 8412973 against 8204118 in a
table; 8413.0 against 8204.1 says the same thing legibly, and the truncated
half-view is far below anything a forecast could resolve.
"""
import json
import os
import re
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone

import requests

BASE = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
PROJECT = "en.wikipedia"          # the English edition, not all Wikipedias
ACCESS = "all-access"             # desktop + mobile web + mobile app, summed
AGENT = "user"                    # human readers only; see the module docstring

UA = ("social-sim-arena/0.1 (https://social-simulation-arena.com; "
      "forecasting benchmark)")

# The API reaches back to 2015-07. Two years of history is plenty for every
# baseline the arena runs (persistence needs one week, climatology a season),
# and each extra year is another ~365 rows on a request that already carries
# plenty.
START = "2023-01-01"

TIMEOUT = 60
RETRIES = 4
BACKOFF = 3.0                     # seconds, multiplied by attempt number

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def url(article, start, end):
    """The per-article daily endpoint for one date range.

    Both timestamps carry a trailing `00`: the format is YYYYMMDDHH and the
    daily endpoint still requires the hour field, zeroed. The article is
    percent-encoded with `/` unsafe -- a slash in a title would otherwise be
    read as a path separator and silently ask for a different endpoint.
    """
    return "/".join((BASE, PROJECT, ACCESS, AGENT,
                     urllib.parse.quote(article, safe=""), "daily",
                     start.strftime("%Y%m%d") + "00",
                     end.strftime("%Y%m%d") + "00"))


def _get_json(u, timeout=TIMEOUT, retries=RETRIES):
    """One request, retrying transient network failures.

    Same trade as silverbulletin.fetch_text: the refresh runs unattended every
    six hours with a hard deadline at each round's lock, so one dropped
    connection must not cost a run and transport errors get a linear backoff.
    An HTTP error is not retried -- a 404 here means the article name is wrong
    (or has no data in the range), and repeating the request will not change
    that.
    """
    last = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(u, headers={"User-Agent": UA}, timeout=timeout)
        except requests.RequestException as e:
            last = e
            if attempt < retries:
                time.sleep(BACKOFF * attempt)
                continue
            raise RuntimeError(
                f"Wikimedia pageviews unreachable after {retries} attempts "
                f"({type(e).__name__}: {e}): {u}") from e
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"Wikimedia pageviews unreachable: {u} ({last})")


def parse(payload, article=""):
    """The API's `items` -> [{date, views}] oldest first, or a hard error.

    An empty `items` is a wrong request or a source outage, never a quiet
    article -- the API answers a valid request for a real article with rows,
    and 404s one it cannot answer. Returning [] would let a plausible-looking
    empty series flow downstream, which is the same failure `build_trackers`
    refuses for an empty VoteHub response.
    """
    items = (payload or {}).get("items") or []
    rows = []
    for it in items:
        ts, v = it.get("timestamp") or "", it.get("views")
        if len(ts) < 8 or v is None:
            continue
        rows.append({"date": f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}", "views": int(v)})
    if not rows:
        raise RuntimeError(
            "Wikimedia pageviews returned no daily rows for "
            f"{article or 'this request'}; refusing to build an empty series")
    rows.sort(key=lambda r: r["date"])
    return rows


def fetch_daily(article, start=START, end=None, timeout=TIMEOUT,
                retries=RETRIES):
    """Daily rows for one article: [{date, views}] oldest first.

    `end` defaults to today (UTC) as the request's upper bound only. The API
    returns just the days it has finished counting -- usually stopping a day
    or two short of the bound -- and the caller reads freshness off the rows
    that arrived, never off the bound it asked with.
    """
    first = date.fromisoformat(str(start))
    last = date.fromisoformat(str(end)) if end \
        else datetime.now(timezone.utc).date()
    return parse(_get_json(url(article, first, last), timeout, retries),
                 article)


def weekly_series(article, start=None, daily=None):
    """[{date, value}] oldest first: one row per complete Monday-Sunday week,
    dated by the Sunday it ends, in thousands of views to one decimal.

    `daily` injects already-fetched rows (tests, or one fetch shared across
    several series on the same article); otherwise one request covers the
    whole history from `start`.

    Only weeks with all seven days present are emitted, so the newest row
    advances exactly when a whole new week is final -- the cadence the
    question asks about -- and a week the API returned with a hole in it is
    dropped rather than summed short. The last row's date is therefore also
    this source's as-of: the newest complete week the API had actually
    counted, not an assumption read off the wall clock.
    """
    rows = daily if daily is not None else fetch_daily(article, start or START)
    if not rows:
        raise RuntimeError(
            f"no daily pageview rows for {article}; refusing to build an "
            "empty series")
    by_sunday = {}
    for r in rows:
        d = date.fromisoformat(r["date"])
        sunday = d + timedelta(days=6 - d.weekday())
        by_sunday.setdefault(sunday, {})[d] = r["views"]
    out = []
    for sunday in sorted(by_sunday):
        got = by_sunday[sunday]
        if len(got) < 7:
            continue
        out.append({"date": sunday.isoformat(),
                    "value": round(sum(got.values()) / 1000.0, 1)})
    if not out:
        raise RuntimeError(
            f"pageviews for {article} built to zero complete weeks; refusing "
            "to publish an empty series (the history may be shorter than one "
            "Monday-Sunday week)")
    return out


# --- the daily top list, and the week it adds up to --------------------------
#
# A second endpoint, and a different question. `per-article` above answers "how
# many people read *this* article"; `top` answers "what did everyone read":
#
#     GET https://wikimedia.org/api/rest_v1/metrics/pageviews/top/
#         en.wikipedia/all-access/{YYYY}/{MM}/{DD}
#     -> {"items": [{"articles": [{"article": .., "views": .., "rank": ..}]}]}
#
# One thousand rows per day, ranked. The weekly list a ranking round asks for is
# the sum of those seven daily lists, ranked again.
#
# **There is no agent split here.** The per-article endpoint takes `user` and
# this one does not: `top` is served for all agents, spiders included, and the
# arena cannot filter what the source does not separate. That is the whole
# reason the exclusion rule below is part of the question rather than a tidying
# step -- on 2026-08-16 the raw list opened Main_Page (7.0M), Special:Search
# (782k), Wikipedia:Featured_pictures (718k), and a "top ten" containing those
# three would be measuring the software rather than the readers, identically
# every week, for every entrant.
#
# **An article missing from a day's thousand contributes zero for that day.**
# That is the source's own cut, not one added here, and it is stated in the
# round's `resolve` so it cannot be mistaken for a bug at scoring time. The
# headroom is large: over the week of 2026-08-10 the thousandth row ran 7.3k-9.0k
# views a day (at most ~54k over seven days) against a tenth-place weekly total
# of 798k, so an article invisible all week cannot displace one in the top ten.

TOP_BASE = "https://wikimedia.org/api/rest_v1/metrics/pageviews/top"
TOP_ARCHIVE = os.path.join(ROOT, "wikitop")

# How old a day must be before it is fetched at all. Two reasons, and the
# second is the one that matters:
#
# - a day the logs have not finished aggregating is a 404, and a refresh that
#   asks for the whole of an in-progress week every six hours spends dozens of
#   requests to be told so;
# - an archived day is written *once and never overwritten*, so a count fetched
#   while it was still settling would be wrong in this repository forever, and
#   would resolve a round against a number Wikimedia never published. The
#   endpoint is not known to serve partial days, but "not known to" is a weak
#   guarantee to hang a permanent record on, and waiting costs nothing: the
#   week the round asks about is scored days after it ends.
TOP_FINAL_LAG_DAYS = 2

# The exclusion rule, versioned, because it *is* the question.
#
# Changing which titles count changes the answer, and a round that locked under
# one rule must never be resolved under another. So the rule carries an id, the
# round definition names the id it was written against, and `is_excluded`
# refuses an id it does not implement. A future rule is a new id and a new
# constant beside this one, never an edit to this one.
EXCLUSION_RULE_ID = "main_page_and_namespaces_v1"

# Dropped by exact title. Main_Page is the site's front door: it is first every
# single day by an order of magnitude and nobody navigated to it on purpose.
EXCLUDED_TITLES = ("Main_Page",)

# Dropped by prefix: everything outside the main (article) namespace, plus the
# talk namespace of each. These are the encyclopedia's machinery -- search
# results, project pages, file description pages, maintenance categories -- not
# things a reader chose to read about.
#
# **Prefix-matched against this closed list, never "contains a colon".** A colon
# test looks equivalent and is catastrophically wrong: on the week of
# 2026-08-10 it would have dropped Spider-Man:_Brand_New_Day (1.95M views, third
# for the week), Avengers:_Doomsday, X-Men:_..., Star_Wars:_..., and thirty
# other real articles whose titles happen to contain a colon, while the actual
# namespaces present were only File:, Wikipedia:, Special:, Help: and Portal:.
NAMESPACE_PREFIXES = (
    "Special:", "Wikipedia:", "Portal:", "Help:", "File:", "Template:",
    "Category:", "Draft:", "User:", "Talk:",
    "Wikipedia_talk:", "Portal_talk:", "Help_talk:", "File_talk:",
    "Template_talk:", "Category_talk:", "Draft_talk:", "User_talk:",
)


def is_excluded(title, rule=EXCLUSION_RULE_ID):
    """True when `title` is dropped from a weekly ranking. Raises on an unknown
    rule id rather than falling back to the current one: a round that names a
    rule this build does not implement must fail, not be answered by a
    different rule wearing the same name."""
    if rule != EXCLUSION_RULE_ID:
        raise ValueError(
            f"unknown Wikipedia exclusion rule {rule!r}; this build implements "
            f"{EXCLUSION_RULE_ID!r}")
    return title in EXCLUDED_TITLES or title.startswith(NAMESPACE_PREFIXES)


_SPACES = re.compile(r"\s+")


def canonical_title(raw):
    """A submitted or archived title in one canonical form. Raises on junk.

    MediaWiki's own two rules, and nothing beyond them: spaces and underscores
    are the same character in a title, and the first letter of a main-namespace
    title is case-insensitive (it is stored capitalized). So `donald trump`,
    `Donald trump` and `Donald_Trump` are one article and normalize together,
    while `Donald_TRUMP` stays distinct -- because on en.wikipedia it is.

    Applied to *both* sides, the submission and the resolved truth, from this
    one function: a normalization applied to only one side would score an
    entrant against a title it never had the chance to write.
    """
    if not isinstance(raw, str):
        raise ValueError(f"article title must be a string, got {type(raw).__name__}")
    t = _SPACES.sub("_", raw.strip()).strip("_")
    if not t:
        raise ValueError("empty article title")
    if len(t) > 256:
        raise ValueError(f"article title is {len(t)} characters, over the 256 "
                         "MediaWiki allows")
    return t[0].upper() + t[1:]


def top_url(day, project=PROJECT, access=ACCESS):
    """The daily top endpoint for one day. Month and day are zero-padded."""
    d = day if isinstance(day, date) else date.fromisoformat(str(day))
    return "/".join((TOP_BASE, project, access,
                     f"{d.year:04d}", f"{d.month:02d}", f"{d.day:02d}"))


def parse_top(payload, day):
    """The API's `items` -> {article: views} for one day, or a hard error.

    Three refusals, each a way a plausible-looking file could otherwise be
    archived and later resolve a round wrongly:

    - an empty list is a wrong request or an outage, never a quiet day (the
      same refusal `parse` makes for the per-article endpoint);
    - a payload whose own date fields disagree with the day asked for would be
      archived under the wrong name and silently move a week's boundary;
    - a repeated title would make the sum depend on iteration order.
    """
    d = day if isinstance(day, date) else date.fromisoformat(str(day))
    items = (payload or {}).get("items") or []
    if not items:
        raise RuntimeError(
            f"Wikimedia top pageviews returned no items for {d.isoformat()}; "
            "refusing to archive an empty day")
    it = items[0]
    got = (f"{it.get('year', '')}-{str(it.get('month', '')).zfill(2)}-"
           f"{str(it.get('day', '')).zfill(2)}")
    if got != d.isoformat():
        raise RuntimeError(
            f"Wikimedia top pageviews answered for {got} when {d.isoformat()} "
            "was asked for; refusing to archive it under the wrong day")
    out = {}
    for a in it.get("articles") or []:
        title, views = a.get("article"), a.get("views")
        if not title or views is None:
            continue
        if title in out:
            raise RuntimeError(
                f"Wikimedia top pageviews listed {title!r} twice on "
                f"{d.isoformat()}; the day's totals would depend on order")
        out[title] = int(views)
    if not out:
        raise RuntimeError(
            f"Wikimedia top pageviews returned no articles for {d.isoformat()}; "
            "refusing to archive an empty day")
    return out


def fetch_top(day, project=PROJECT, access=ACCESS, timeout=TIMEOUT,
              retries=RETRIES):
    """{article: views} for one day, straight off the endpoint."""
    d = day if isinstance(day, date) else date.fromisoformat(str(day))
    return parse_top(_get_json(top_url(d, project, access), timeout, retries), d)


# --- the archive -------------------------------------------------------------
#
# `trends.as_archived`'s discipline, with one difference that simplifies it. A
# Trends snapshot is dated by the day it was *fetched*, because the same
# completed week reads differently on different days and the earliest reading
# wins. A day's pageview count is computed once from the request logs and never
# revised, so a snapshot here is dated by the day it *measures* -- one file per
# day, ever, written once and never overwritten.
#
# The archive still earns its place for the reason Trends' does: it is the
# resolution source. A week resolves from seven committed files, so the answer
# is recomputable by anyone with the repository and no network, the endpoint
# being unreachable on resolution day costs nothing, and a silent upstream
# revision could not move a score that was already published.

def top_archive_key(project=PROJECT, access=ACCESS):
    """Directory name for one (project, access) pair: `en.wikipedia.all-access`."""
    return f"{project}.{access}"


def top_archive_dir(key):
    return os.path.join(TOP_ARCHIVE, key)


def top_archive_path(key, day):
    d = day if isinstance(day, date) else date.fromisoformat(str(day))
    return os.path.join(top_archive_dir(key), f"{d.isoformat()}.json")


def read_top_archive(key, day):
    path = top_archive_path(key, day)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def top_archive_days(key):
    """Measured days held for this key, oldest first."""
    d = top_archive_dir(key)
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


def build_top_snapshot(articles, day, project, access, fetched_at):
    """The record written to disk for one measured day.

    The full thousand rows as served, unfiltered: the archive holds what the
    API said, and the exclusion rule is applied when a week is ranked. Keeping
    the two apart means a mistake in the rule is a recomputation rather than a
    corrupted archive, and it lets a reader check the rule's effect against the
    raw list in the same repository.

    `rank` is dropped. It is redundant with `views` for everything downstream --
    a week is the sum of views, and no daily rank enters it -- and dropping it
    is a third of the file.
    """
    d = day if isinstance(day, date) else date.fromisoformat(str(day))
    return {
        "project": project,
        "access": access,
        "day": d.isoformat(),
        "url": top_url(d, project, access),
        "fetched_at": fetched_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n_articles": len(articles),
        "unit": "pageviews, all agents (the top endpoint publishes no agent split)",
        "articles": dict(articles),
    }


def write_top_snapshot(key, snap):
    """Write one day, once. An existing file is never overwritten."""
    path = top_archive_path(key, snap["day"])
    if os.path.exists(path):
        return path
    os.makedirs(top_archive_dir(key), exist_ok=True)
    with open(path, "w") as f:
        json.dump(snap, f, indent=1, sort_keys=True, ensure_ascii=False)
        f.write("\n")
    return path


def top_snapshot(day, project=PROJECT, access=ACCESS, fetch=True,
                 use_archive=True, now=None):
    """One day's {article: views}: the archived file, or a fetch that writes it.

    Once a day's file exists the day is closed and no request is made for it
    again -- the counts are final, so a re-fetch could only cost a request and
    risk disagreeing with a number a round already resolved against.

    Returns None when the day is not available: `fetch=False` (the archive alone
    and no network, which is what tests and any rerun over committed data want)
    or a day younger than `TOP_FINAL_LAG_DAYS`. The caller decides what a
    missing day means -- for `weekly_totals` it means the week is not rankable
    yet, which is a different thing from an error.
    """
    d = day if isinstance(day, date) else date.fromisoformat(str(day))
    key = top_archive_key(project, access)
    if use_archive:
        have = read_top_archive(key, d)
        if have:
            return have["articles"]
    if not fetch:
        return None
    now = now or datetime.now(timezone.utc)
    if d > now.date() - timedelta(days=TOP_FINAL_LAG_DAYS):
        return None
    arts = fetch_top(d, project, access)
    if use_archive:
        write_top_snapshot(key, build_top_snapshot(arts, d, project, access, now))
    return arts


# --- weeks -------------------------------------------------------------------

def week_days(week_end):
    """The seven dates of the Monday-Sunday week ending `week_end`.

    Raises unless `week_end` really is a Sunday: a week boundary off by a day
    would silently answer a different question than the round asked.
    """
    d = week_end if isinstance(week_end, date) else date.fromisoformat(str(week_end))
    if d.weekday() != 6:
        raise ValueError(f"{d.isoformat()} is a "
                         f"{d.strftime('%A')}, not a Sunday; a Wikipedia "
                         "ranking week runs Monday to Sunday")
    return [d - timedelta(days=i) for i in range(6, -1, -1)]


def weekly_totals(week_end, project=PROJECT, access=ACCESS, fetch=False,
                  now=None):
    """{article: views summed over the week}, from the archive. Or a raise.

    All seven days or none. A six-day sum ranked as a week is not a slightly
    noisier answer, it is a different question -- weekday traffic patterns are
    strong enough that dropping a Saturday reorders the tail -- and the round
    would resolve looking finished.
    """
    days = week_days(week_end)
    totals, missing = {}, []
    for d in days:
        arts = top_snapshot(d, project, access, fetch=fetch, now=now)
        if arts is None:
            missing.append(d.isoformat())
            continue
        for title, views in arts.items():
            totals[title] = totals.get(title, 0) + int(views)
    if missing:
        raise RuntimeError(
            f"week ending {days[-1].isoformat()} is missing "
            f"{len(missing)} of 7 days from the {top_archive_key(project, access)} "
            f"archive ({', '.join(missing)}); refusing to rank a partial week")
    return totals


def rank_totals(totals, n, rule=EXCLUSION_RULE_ID):
    """Weekly totals -> the ordered top `n` titles, exclusions applied.

    Ties are broken by title, ascending. Two articles reaching the same weekly
    total to the view is vanishingly unlikely at these magnitudes, but "unlikely"
    is not "deterministic": without a stated tiebreak the answer would depend on
    dictionary order, and the arena's reproducibility claim is not the sort of
    thing to spend on a coin flip.
    """
    kept = [(t, v) for t, v in totals.items() if not is_excluded(t, rule)]
    if len(kept) < n:
        raise RuntimeError(
            f"only {len(kept)} article(s) survive the {rule} exclusions; "
            f"cannot rank a top {n}")
    kept.sort(key=lambda tv: (-tv[1], tv[0]))
    return [canonical_title(t) for t, _ in kept[:n]]


def weekly_top(week_end, n=10, rule=EXCLUSION_RULE_ID, project=PROJECT,
               access=ACCESS, fetch=False, now=None):
    """(ordered top-`n` titles, {article: weekly views}) for one complete week."""
    totals = weekly_totals(week_end, project, access, fetch=fetch, now=now)
    return rank_totals(totals, n, rule), totals


def archived_weeks(project=PROJECT, access=ACCESS, upto=None):
    """Every Monday-Sunday week the archive holds all seven days of, oldest first.

    `upto` bounds the newest week end inclusive, which is how a round asks for
    "the weeks that existed before my lock" without the caller re-deriving
    calendars.
    """
    have = set(top_archive_days(top_archive_key(project, access)))
    if not have:
        return []
    limit = None
    if upto is not None:
        limit = upto if isinstance(upto, date) else date.fromisoformat(str(upto))
    ends = set()
    for d in have:
        ends.add(d + timedelta(days=6 - d.weekday()))
    out = []
    for end in sorted(ends):
        if limit is not None and end > limit:
            continue
        if all(d in have for d in week_days(end)):
            out.append(end)
    return out
