"""The Conference Board Consumer Confidence Index, from its own release page.

  GET https://www.conference-board.org/topics/consumer-confidence

**Read this before planning a round on it: the free feed is one month deep.**

The page carries the current release in prose, and it is clean prose -- all
three indices and the previous month's restated value in a single paragraph:

    The Conference Board Consumer Confidence Index(R) decreased by 1.4 points
    to 90.8 (1985=100) in July, down from an upwardly revised 92.2 in June.
    The Present Situation Index ... fell by 3.6 points to 114.9 ...
    The Expectations Index ... remained unchanged at 74.7.

What the page does *not* carry is any earlier release. The Conference Board
publishes no free archive: `/press` lists exactly one consumer-confidence page
and it advertises the "CCI premium dataset". The history is a paid product, and
there is no FRED mirror -- FRED carries an OECD-normalised consumer confidence
indicator for the US, which is a different series in different units, not this
one.

**So this adapter cannot yet support a round, and saying so is the point.** The
arena's headline metric is `1 - CRPS(entrant)/CRPS(persistence)`, and with one
observation there is no persistence, no trend, no EWMA and no denominator. A
series registered without history produces rounds that no entrant can be scored
on -- which is exactly the state `midterm-2026-house-seats` is in, where not one
forecast has ever been filed and nothing in the pipeline said so.

The free path to a usable series is to snapshot this page every month and let
the history accumulate: `provenance.record` already keeps the body, so ten
releases is ten months. Slow, but real, and it costs nothing.

**The revision is the interesting part, and it is why the first print must be
pinned.** Every release restates the previous month -- June was published at
91.2 and is quoted here as "an upwardly revised 92.2". A round that resolves
against "the CCI for month M" without saying *which printing* is unanswerable,
because the number changes a month after the round closes. `parse` therefore
returns both: the month being reported, and the previous month as now restated.
"""
import calendar
import re

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

# Headline: name, movement, level, the (1985=100) base, the month, and the
# previous month as restated. The base year is required rather than optional --
# it is what distinguishes this index from the two sub-indices, which are
# written in the identical shape a sentence later.
HEADLINE = re.compile(
    r"Consumer Confidence Index\s*(?:&reg;|\(R\)|®)?\s*"
    rf"{_MOVE}(?:\s+by)?\s*(?:(\d{{1,2}}\.\d)\s+points?)?\s*(?:to|at)\s+"
    r"(\d{1,3}\.\d)\s*\(1985=100\)\s*in\s+([A-Z][a-z]+)"
    r"(?:[^.]{0,60}?revised\s+(\d{1,3}\.\d)\s+in\s+([A-Z][a-z]+))?", re.I)

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


def _flatten(html):
    text = re.sub(r"<(style|script)\b.*?</\1>", " ", html or "", flags=re.S | re.I)
    text = _TAG.sub(" ", text)
    for a, b in (("&reg;", "®"), ("&mdash;", " "), ("&ndash;", "-"),
                 ("&rsquo;", "'"), ("&nbsp;", " "), ("&amp;", "&")):
        text = text.replace(a, b)
    return re.sub(r"\s+", " ", text).strip()


def parse(text, year=None):
    """Page HTML -> the current release.

    {month: 'YYYY-MM-01', value, change, present_situation, expectations,
     previous_month, previous_value_restated}

    `year` is needed because the page names the month and not the year. It
    defaults to the current one and is inferred backwards across a December
    release read in January -- a release is never in the future, so a month
    ahead of today is last year's.

    Raises rather than returning a partial reading. A consumer confidence
    number with no month attached is not a data point.
    """
    import datetime
    flat = _flatten(text)
    m = HEADLINE.search(flat)
    if not m:
        raise RuntimeError(
            "no Conference Board headline reading on the page. The release "
            "paragraph is prose and the house style can change; refusing to "
            "publish a number read some other way. Body starts: "
            + flat[:200])
    change, level, month_name, restated, prev_name = m.groups()
    if month_name not in MONTHS:
        raise RuntimeError(f"unknown month {month_name!r} in the CCI release")

    today = datetime.date.today()
    year = year or today.year
    month = MONTHS[month_name]
    if month > today.month and year == today.year:
        year -= 1                      # a December release read in January

    delta = float(change) if change else 0.0
    out = {
        "month": f"{year}-{month:02d}-01",
        "value": float(level),
        "change": -delta if _DOWN.search(m.group(0)) else delta,
    }
    for key, pat in SUBINDEX.items():
        sub = pat.search(flat)
        out[key] = float(sub.group(1)) if sub else None
    if restated:
        pm = MONTHS.get(prev_name)
        py = year if pm and pm < month else year - 1
        out["previous_month"] = f"{py}-{pm:02d}-01" if pm else None
        out["previous_value_restated"] = float(restated)
    else:
        out["previous_month"] = out["previous_value_restated"] = None
    return out


def current(text=None):
    return parse(text if text is not None else fetch_text())
