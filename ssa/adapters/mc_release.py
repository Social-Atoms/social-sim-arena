"""When Morning Consult actually published a wave, read out of a sheet we
already download.

The problem this exists for
---------------------------
An arena round must lock before its answer exists. For every other tracker we
know when the number goes public: Michigan and the Economist/YouGov waves have
recorded calendars, Civiqs is a daily dashboard we archive ourselves. Morning
Consult we only ever saw second-hand, through Silver Bulletin's sheet, whose
`createddate` is the day *Silver Bulletin* added the row -- measured 1 to 15
days after the field period ends, median 2. That is an upper bound on
publication, not a publication date, so `tools/generate_rounds.py` refuses to
schedule Morning Consult at all and the season's six MC rounds are hand-written
against an assumed calendar.

What was missed for months: the sheet's `url` column is not decorative. It
records where Silver Bulletin got each poll, and for Morning Consult it points
at whatever Morning Consult published that week --

    .../wp-uploads/2026/08/MCPI-PI-Weekly.html      the public dashboard
    https://drive.google.com/file/d/1mz53nb.../view a weekly deck, shared file

Both are public. The Drive file answers with
`Content-Disposition: filename="20260831_US_MorningConsult.pdf"` and
`Last-Modified: Mon, 31 Aug 2026 20:11:19 GMT`, which is the publication moment
itself, to the second, from a link Silver Bulletin published rather than from
crawling Morning Consult (whose robots.txt disallows everything but the root).

So this reads the URLs out of the sheet already on disk, asks each *new* one
for its headers once, and writes down what came back. Nothing here is used to
resolve a round; Silver Bulletin remains the resolution source. It accumulates
the one fact nobody had: the weekday and hour Morning Consult publishes.

**It can only ever look forward.** The Drive links are unindexed and the
dashboard file is overwritten in place, so the history that exists is the
history we start recording. That is the argument for turning it on before it is
needed rather than after.
"""
import json
import os
import re
import time
from datetime import datetime, timezone

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LEDGER = os.path.join(ROOT, "sources", "mc_release", "observed.json")

# Browser headers, because Cloudflare sits in front of the asset host and its
# 403 looks exactly like "this file does not exist". Nine dates were written off
# as missing before that was noticed, and then three more: measured, a request
# carrying only `User-Agent` is refused and the same request carrying
# `Accept` and `Accept-Language` as well is answered. All three are needed.
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 "
                   "Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = (10, 30)

_DRIVE = re.compile(r"drive\.google\.com/file/d/([A-Za-z0-9_-]+)")
_FILENAME = re.compile(r'filename="?([^"\r\n;]+)"?')
# A weekly artifact carries its week in the name -- `20260831_US_MorningConsult`
# or `MCPI-PI-Weekly_260727`. A bare tracker URL does not: it is the landing
# page, cited by 63 waves at once, and its modification time dates none of them.
# Only the dated ones are evidence about when a wave was published.
_DATED = re.compile(r"(?<!\d)(20\d{6})(?!\d)|(?<!\d)_(\d{6})(?!\d)")


def wave_label(*texts):
    """The YYYY-MM-DD a weekly artifact names, or None."""
    for t in texts:
        m = _DATED.search(t or "")
        if not m:
            continue
        raw = m.group(1) or ("20" + m.group(2))
        try:
            datetime.strptime(raw, "%Y%m%d")
        except ValueError:
            continue
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return None


def urls_for(rows, pollster="Morning Consult"):
    """{url: [wave end dates]} for one pollster, oldest wave first.

    Takes the parsed sheet rather than a path: the caller already has it, and a
    second read of a 1.6 MB CSV to look at one column is a waste.
    """
    out = {}
    for r in rows:
        if (r.get("pollster") or "").strip() != pollster:
            continue
        url = (r.get("url") or "").strip()
        if url:
            out.setdefault(url, []).append((r.get("enddate") or "").strip())
    return {u: sorted(set(d for d in ds if d)) for u, ds in out.items()}


def head(url, session=None):
    """(status, headers) for one URL, following redirects. Never raises.

    A Drive `/view` link is rewritten to its download endpoint, which is the
    one that answers with the filename and the modification time; the view page
    is an HTML shell that carries neither.
    """
    m = _DRIVE.search(url)
    if m:
        url = f"https://drive.google.com/uc?export=download&id={m.group(1)}"
    get = (session or requests).head
    try:
        r = get(url, headers=HEADERS, timeout=TIMEOUT,
                allow_redirects=True)
        return r.status_code, dict(r.headers)
    except Exception as e:                     # noqa: BLE001 - recorded, not raised
        return None, {"error": f"{type(e).__name__}: {e}"}


def observation(url, waves, status, headers, seen_at):
    """One ledger row. `published_at` is the fact this module exists for."""
    disp = headers.get("Content-Disposition") or headers.get("content-disposition") or ""
    name = (_FILENAME.search(disp) or [None, None])[1]
    row = {"url": url, "waves": waves, "first_seen_at": seen_at,
           "http_status": status,
           "published_at": headers.get("Last-Modified") or headers.get("last-modified"),
           "filename": name, "wave_label": wave_label(name, url)}
    if headers.get("error"):
        row["error"] = headers["error"]
    return row


def load(path=None):
    try:
        with open(path or LEDGER) as fh:
            got = json.load(fh)
    except (OSError, ValueError):
        return []
    return got if isinstance(got, list) else []


def update(rows, path=None, session=None, now=None, pause=1.0):
    """Ask about every Morning Consult URL not asked about before; append.

    A URL is asked once and never again: the file behind it can be overwritten,
    but the first answer is the one that dates the wave it was cited for, and
    re-asking would quietly replace a recorded publication time with a later
    edit's.
    """
    path = path or LEDGER
    ledger = load(path)
    # Only an answered request counts as asked. A 403 from the CDN and a
    # timeout are failures, not observations, and a failure that is never
    # retried is a wave whose publication time is lost for good -- these files
    # are overwritten in place, so there is no second chance later.
    known = {row["url"] for row in ledger if row.get("http_status") == 200}
    ledger = [row for row in ledger if row.get("http_status") == 200]
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    added = []
    for url, waves in sorted(urls_for(rows).items()):
        if url in known:
            continue
        status, headers = head(url, session=session)
        added.append(observation(url, waves, status, headers, stamp))
        if pause:
            time.sleep(pause)          # the host 403s a burst; it answers a walk
    if not added:
        return ledger, []
    ledger = ledger + added
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as fh:
        json.dump(ledger, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)
    return ledger, added


def schedule(ledger=None):
    """What the recorded publications say about when Morning Consult publishes.

    Returns {n, weekdays, hours_utc}; the caller decides whether that is enough
    to schedule on. Deliberately not a verdict: two observations agreeing is not
    a calendar, and this module has no opinion about how many are.
    """
    # Dated artifacts only. The landing page's modification time is real and
    # says nothing about any particular wave, which is worse than missing.
    rows = [r for r in (ledger if ledger is not None else load())
            if r.get("published_at") and r.get("wave_label")]
    days, hours = {}, []
    for r in rows:
        try:
            t = datetime.strptime(r["published_at"], "%a, %d %b %Y %H:%M:%S %Z")
        except ValueError:
            continue
        days[t.weekday()] = days.get(t.weekday(), 0) + 1
        hours.append(t.hour)
    return {"n": len(hours), "weekdays": days,
            "hours_utc": sorted(hours)}
