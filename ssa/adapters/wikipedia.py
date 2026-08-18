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
