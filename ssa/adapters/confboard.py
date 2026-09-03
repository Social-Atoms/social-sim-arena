"""The Conference Board Consumer Confidence Index, from its own release page.

  GET https://www.conference-board.org/topics/consumer-confidence

**The history is not on this page, but it is recoverable.** The Conference
Board sells it -- Data Central, $2,370, monthly Excel back to 1967 -- and there
is no free mirror; FRED's US consumer confidence indicator is an OECD
amplitude-adjusted composite in different units, not this series.

What is free is the **Internet Archive**, which holds monthly captures of this
exact page back to 2020. Those carry the *first print* -- the number as it was
published, before the following month restates it -- which is what a round has
to resolve against and is the one thing the paid file does **not** contain.
`tools/backfill_cci.py` walks the CDX index and replays them through `parse`.

That only works because this parser anchors on the base year rather than on a
sentence shape. The page has used at least three house styles in five years:

    2022  "...Index (R) decreased in November after also losing ground in
           October. The Index now stands at 100.2 (1985=100), down from
           102.2 in October."               <- level in a *later* sentence
    2024  "...Index(R) increased in November to 111.7 (1985=100), up 2.1
           points from 109.6 in October."   <- month *before* the level
    2026  "...Index(R) decreased by 1.4 points to 90.8 (1985=100) in July,
           down from an upwardly revised 92.2 in June."  <- month *after*

A parser matching the newest wording refused every archived page, and the
refusals were indistinguishable from "the page never carried the number" --
which is how this nearly got written off as a source with no free history at
all. `(1985=100)` is printed on the headline and on nothing else, and it has
outlived every rewrite of the prose around it.

**The revision is the interesting part, and it is why the first print must be
pinned.** Every release restates the previous month -- June was published at
91.2 and is quoted here as "an upwardly revised 92.2". A round that resolves
against "the CCI for month M" without saying *which printing* is unanswerable,
because the number changes a month after the round closes. `parse` therefore
returns both: the month being reported, and the previous month as now restated.
"""
import calendar
import json
import os
import re
from datetime import date, datetime, timezone

import requests

URL = "https://www.conference-board.org/topics/consumer-confidence"
TIMEOUT = 45
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

MONTHS = {m: i for i, m in enumerate(calendar.month_name) if m}

_TAG = re.compile(r"<[^>]+>")
_DOWN = re.compile(r"decreas|fell|drop|declin|slipp|dipp|down", re.I)
_MOVE = (r"(?:decreased|increased|fell|rose|dropped|declined|slipped|dipped|"
         r"climbed|gained|jumped|remained unchanged|was unchanged|held steady)")

# **Anchor on `(1985=100)`, not on a sentence shape.** The house style has been
# through at least three templates in five years, and each one moves the pieces
# around:
#
#   2022  "...Index (R) decreased in November after also losing ground in
#          October. The Index now stands at 100.2 (1985=100), down from 102.2
#          in October."                          <- value in a *later* sentence
#   2024  "...Index(R) increased in November to 111.7 (1985=100), up 2.1 points
#          from 109.6 in October."               <- month *before* the value
#   2026  "...Index(R) decreased by 1.4 points to 90.8 (1985=100) in July, down
#          from an upwardly revised 92.2 in June."  <- month *after* the value
#
# Matching templates cost five years of history: every 2020-2024 archived
# snapshot was refused by a parser that only knew the 2026 wording, and the
# refusals looked exactly like "the page never carried the number". What
# survives every rewrite is the base year, and it is printed on the headline
# and on nothing else -- the Present Situation and Expectations Indexes are
# quoted bare, one clause later. So the base year locates the number and
# everything else is read outward from it.
BASE_YEAR = r"\(\s*1985\s*=\s*100\s*\)"

# The level: the number immediately before the base year.
LEVEL = re.compile(rf"(\d{{1,3}}\.\d)\s*{BASE_YEAR}")
# The month the reading is *for*. It sits on either side depending on vintage,
# so both sides are searched and the nearest one wins.
IN_MONTH = re.compile(r"\bin\s+([A-Z][a-z]+)\b")
# The previous month's value as now restated, and which month that was.
PREV = re.compile(
    r"\b(?:from|than)\s+(?:an?\s+)?(?:[a-z]+\s+){0,2}?(\d{1,3}\.\d)"
    r"(?:[^.]{0,40}?\bin\s+([A-Z][a-z]+))?", re.I)
# How much it moved, when the release says so at all.
CHANGE = re.compile(r"\b(\d{1,2}\.\d)\s+points?\b", re.I)
# When the release actually went out: "Latest Press Release Updated: Tuesday,
# November 26, 2024". Better than inferring the year from a snapshot timestamp,
# which is only ever close.
RELEASED = re.compile(
    r"Updated:\s*(?:[A-Z][a-z]+day),?\s*([A-Z][a-z]+)\s+(\d{1,2}),\s*(\d{4})")

SUBINDEX = {
    "present_situation": re.compile(
        rf"Present Situation Index\b.{{0,220}}?{_MOVE}(?:\s+by)?\s*"
        rf"(?:\d{{1,2}}\.\d\s+points?)?\s*(?:to|at)\s+(\d{{1,3}}\.\d)",
        re.I | re.S),
    "expectations": re.compile(
        rf"Expectations Index\b.{{0,220}}?{_MOVE}(?:\s+by)?\s*"
        rf"(?:\d{{1,2}}\.\d\s+points?)?\s*(?:to|at)\s+(\d{{1,3}}\.\d)",
        re.I | re.S),
}


def fetch_text(timeout=TIMEOUT, url=URL):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
    r.raise_for_status()
    return r.text


def _sentence(text):
    """Up to the end of the clause the level sits in.

    The Present Situation Index states its own move one sentence later, in the
    identical shape -- "fell by 3.6 points to 114.9" -- so an unbounded window
    reads the wrong index's change and reports it as consumer confidence. A
    period followed by a space and a capital ends a sentence; a period inside
    114.9 does not.
    """
    cut = re.search(r"\.\s+[A-Z]", text)
    return text[:cut.start()] if cut else text[:220]


def _flatten(html):
    text = re.sub(r"<(style|script)\b.*?</\1>", " ", html or "", flags=re.S | re.I)
    text = _TAG.sub(" ", text)
    for a, b in (("&reg;", "®"), ("&mdash;", " "), ("&ndash;", "-"),
                 ("&rsquo;", "'"), ("&nbsp;", " "), ("&amp;", "&")):
        text = text.replace(a, b)
    return re.sub(r"\s+", " ", text).strip()


def _utc_date(now=None):
    """UTC calendar day for an as-of decision; ``now`` is a test seam.

    A naive datetime has no defensible meaning here. Reject it instead of
    silently interpreting it in whichever timezone happens to run the adapter.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if isinstance(now, datetime):
        if now.tzinfo is None:
            raise ValueError("Conference Board as-of datetime must carry a timezone")
        return now.astimezone(timezone.utc).date()
    if isinstance(now, date):
        return now
    raise TypeError("Conference Board as-of value must be a date or datetime")


def parse(text, year=None, now=None):
    """Page HTML -> the release it is showing.

    {month, value, change, present_situation, expectations, previous_month,
     previous_value_restated, released_on}

    `year` only matters when the page does not carry its own release date; the
    archived pages do ("Updated: Tuesday, November 26, 2024") and it is used in
    preference, because a snapshot taken in December of a November release
    would otherwise be dated a month wrong every December-January boundary.

    Raises rather than returning a partial reading. A consumer confidence
    number with no month attached is not a data point.
    """
    flat = _flatten(text)
    m = LEVEL.search(flat)
    if not m:
        raise RuntimeError(
            "no Conference Board headline reading on the page: nothing is "
            "printed against (1985=100), which is the only mark that "
            "distinguishes the index from the Present Situation and "
            "Expectations numbers beside it. Body starts: " + flat[:200])

    # The release date, from the page when it says so.
    released = RELEASED.search(flat)
    if released:
        rmon, rday, ryear = released.groups()
        released_on = (f"{ryear}-{MONTHS.get(rmon, 1):02d}-{int(rday):02d}"
                       if rmon in MONTHS else None)
        year = year or int(ryear)
    else:
        released_on = None
    # This fallback is a data decision: at the Dec/Jan boundary it chooses the
    # year attached to a release. `date.today()` used the runner's local zone,
    # so two machines reading the same page at the same instant could archive
    # different months. Every other live adapter makes as-of decisions in UTC;
    # this one now shares that invariant.
    today = _utc_date(now)
    year = year or today.year

    # **The reporting month is the month after the one it compares itself to.**
    # Reading it out of the text directly is a trap in both directions: the
    # 2024 wording puts it before the level ("increased in November to 111.7
    # (1985=100) ... from 109.6 in October"), the 2026 wording puts it after
    # ("to 90.8 (1985=100) in July, down from ... 92.2 in June"), and the 2022
    # wording puts it in a different sentence entirely, next to a second month
    # that is not it ("decreased in November after also losing ground in
    # October"). Nearest-match gets two of those three wrong.
    #
    # What is true of every vintage is that a release compares itself to the
    # month immediately before. So the comparison month is read -- it is
    # unambiguous, it sits inside a "from ... in <Month>" clause -- and the
    # reporting month is derived from it. Only when no comparison month is
    # named does this fall back to reading the text.
    before, after = flat[:m.start()], flat[m.end():]
    prev = PREV.search(after[:220])
    prev_month = MONTHS.get(prev.group(2)) if prev and prev.group(2) else None
    if prev_month is not None:
        month = 1 if prev_month == 12 else prev_month + 1
    else:
        cands = [(m.start() - x.end(), x.group(1))
                 for x in IN_MONTH.finditer(before[-220:])]
        cands += [(x.start(), x.group(1)) for x in IN_MONTH.finditer(after[:220])]
        named = [(d, mo) for d, mo in sorted(cands) if mo in MONTHS]
        if not named:
            raise RuntimeError(
                f"read {m.group(1)} against (1985=100) but no month is named "
                "beside it; refusing to date a reading by guesswork")
        month = MONTHS[named[0][1]]
    if released_on:
        # A release reports the month it went out in, or the one before.
        ry, rm = int(released_on[:4]), int(released_on[5:7])
        year = ry - 1 if month > rm else ry
    elif month > today.month and year == today.year:
        year -= 1                      # a December release read in January

    out = {"month": f"{year}-{month:02d}-01", "value": float(m.group(1)),
           "released_on": released_on}

    if prev:
        pm = MONTHS.get(prev.group(2)) if prev.group(2) else None
        if pm is None:
            pm = 12 if month == 1 else month - 1
        py = year if pm < month else year - 1
        out["previous_month"] = f"{py}-{pm:02d}-01"
        out["previous_value_restated"] = float(prev.group(1))
    else:
        out["previous_month"] = out["previous_value_restated"] = None

    # **The change is derived, not read.** "up 2.1 points" is stated only in
    # some vintages, the sign lives in a verb several clauses away, and a bare
    # "X.X points" near the level is as likely to belong to a sub-index. Two
    # levels subtract exactly and cannot disagree with themselves. Where the
    # release does state a figure it is used as a check, and a disagreement is
    # loud, because it means one of the two numbers was read out of the wrong
    # sentence.
    if out["previous_value_restated"] is not None:
        out["change"] = round(out["value"] - out["previous_value_restated"], 1)
        ch = CHANGE.search(_sentence(after))
        if ch and abs(abs(out["change"]) - float(ch.group(1))) > 0.15:
            raise RuntimeError(
                f"CCI {out['month']}: the page says the index moved "
                f"{ch.group(1)} points, but {out['value']} against a restated "
                f"{out['previous_value_restated']} is {out['change']:+.1f}. "
                "One of those numbers came from the wrong sentence.")
    else:
        ch = CHANGE.search(_sentence(after))
        out["change"] = None if not ch else (
            -float(ch.group(1)) if _DOWN.search(
                flat[max(0, m.start() - 220):m.start() + 200])
            else float(ch.group(1)))

    for key, pat in SUBINDEX.items():
        sub = pat.search(flat)
        out[key] = float(sub.group(1)) if sub else None
    return out


def current(text=None, now=None):
    return parse(text if text is not None else fetch_text(), now=now)


def _is_live_failure(exc):
    """True for transport/status failures, never parser-contract failures."""
    if isinstance(exc, (TimeoutError, ConnectionError,
                        requests.RequestException)):
        return True
    msg = str(exc).lower()
    return any(mark in msg for mark in (
        "http 403", "403 forbidden", "http 429", "429 too many",
        "rate-limit", "rate limit", "timeout", "timed out", "unreachable",
        "connection", "sslerror", "sslzeroreturn"))


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARCHIVE = os.path.join(ROOT, "sources", "confboard")


def history(fetch=True, now=None, diagnostics=None):
    """First prints from sources/confboard/, [{date, value}] oldest first.

    Rows are dated by the month measured (the "2026-08-01" label style
    Michigan uses), not by the release day -- which means the same caveat
    applies: `date < lock_at` cannot freeze this series, and rounds rely on
    the lock snapshot like every other monthly tracker.

    The archive is `tools/backfill_cci.py`'s output plus whatever this
    function adds: with fetch=True the live page is read once, and a release
    the archive lacks is written down (write-once, keyed by released_on)
    before it can be restated -- that is the only way a *first print* can be
    captured, and it is the entire reason this series is usable at all.

    A fetch failure serves the committed archive with a warning instead of
    raising: a monthly series with years of committed prints must not take a
    whole refresh down because one page timed out. That mirrors what the
    Civiqs adapter does when the courier is a day behind.
    """
    rows = {}
    if os.path.isdir(ARCHIVE):
        for name in sorted(os.listdir(ARCHIVE)):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(ARCHIVE, name)) as f:
                rec = json.load(f)
            month = rec["month"][:7] + "-01"
            if month not in rows:                 # earliest print per month wins
                rows[month] = rec["value"]
    live_error = None
    if fetch:
        try:
            rec = current() if now is None else current(now=now)
        except Exception as e:                                  # noqa: BLE001
            # `current` remains the public seam used by probes and tests. Its
            # parser errors are not archive-eligible; only recognizable
            # transport/status failures degrade to a committed first print.
            if not _is_live_failure(e) or not rows:
                raise
            live_error = e
            print(f"  confboard: fetch failed ({type(e).__name__}: "
                  f"{str(e)[:120]}); serving the committed archive")
        else:
            # Archiving is deliberately outside the fetch-error fallback. A
            # failed evidence write is not an unreachable server.
            day = rec.get("released_on") or rec["month"][:7] + "-28"
            path = os.path.join(ARCHIVE, day + ".json")
            if not os.path.exists(path):
                os.makedirs(ARCHIVE, exist_ok=True)
                rec["source"] = URL
                with open(path, "w") as f:
                    json.dump(rec, f, indent=1, sort_keys=True)
            rows.setdefault(rec["month"][:7] + "-01", rec["value"])
    if not rows:
        raise RuntimeError(
            "no Conference Board history: sources/confboard/ is empty and the "
            "live page could not be read. Run tools/backfill_cci.py --execute.")
    out = [{"date": d, "value": rows[d]} for d in sorted(rows)]
    if live_error is not None and diagnostics is not None:
        diagnostics.append({
            "source": "confboard",
            "scope": "live",
            "error": live_error,
            "archive_evidence": (
                f"{os.path.relpath(ARCHIVE, ROOT)} ({len(out)} first-print "
                f"rows; newest {out[-1]['date']})"),
        })
    return out
