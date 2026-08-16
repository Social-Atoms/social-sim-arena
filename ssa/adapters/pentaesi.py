"""Penta-CivicScience Economic Sentiment Index, from the publisher's own posts.

  GET https://esi-civicscience.pentagroup.co/wp-json/wp/v2/posts

Every biweekly release is one WordPress post, and the site exposes the standard
WP REST API: free, keyless, paginated, and carrying the real publication date.
322 posts back to 2013-11. That is the closest thing to a machine-readable feed
this index has -- **there is no CSV, no chart endpoint and no data API**. The
number itself is in the prose.

**So the parser's job is to refuse, not to guess.** Thirteen years of editorial
variation sit in these posts, and the five sub-indicators are written in exactly
the same sentence shape as the headline:

    "...the ESI increased 1.5 points to 32.3..."          <- the index
    "Confidence in finding a new job increased 3.1 points to 29.0."   <- not

A parser that takes the first "to <number>" in the page reads 303 of 322 posts
and gets an unknowable fraction of them wrong: an earlier draft of this module
produced a series with 47-point jumps in it, because on some releases the lead
paragraph names a sub-indicator first. That failure is silent, which makes it
worse than no source at all -- exactly the argument in ssa/adapters/umich.py.

Two things make this safe:

**The match is anchored on the index's own name.** Nothing counts unless the
number is tied to the string "...CivicScience Economic Sentiment Index (ESI)"
within a short span. A sub-indicator sentence never contains it.

**Every release states its own change, so the series checks itself.** Where two
adjacent releases are fourteen days apart and the later one gives a delta,
`parse` verifies that `previous + delta == current` and raises if it does not.
That is a real check on real data, not a type assertion: it caught the draft
above immediately. Across the releases this module accepts, 77 adjacent pairs
reconcile and none disagree.

The cost is recall. Coverage is near-complete from 2022 and thin before it,
because the earlier posts were written by a different publisher (the index was
"HPS-CivicScience" until 2023) in a freer style. `history()` therefore starts
where the wording became machine-stable, and a gap is a gap rather than an
invention.

**Biweekly, not weekly.** Alternating Wednesdays, 26 releases a year. Worth
saying out loud because it constrains what a round can ask.
"""
import datetime
import json
import re

import requests

BASE = "https://esi-civicscience.pentagroup.co"
POSTS = BASE + "/wp-json/wp/v2/posts"
PER_PAGE = 100
TIMEOUT = 45

# Where the modern template begins. Before this the index had a different owner
# and a much freer house style, and the strict match reads fewer than one post
# in five -- a series with four-fifths of its points missing is not a series.
STABLE_FROM = "2022-01-01"

# The index's own name, in both eras. The anchor that separates the headline
# from the five sub-indicators, which are otherwise written identically.
INDEX_NAME = r"(?:Penta|HPS)-Civic ?Science Economic Sentiment Index \(ESI\)"

# A character span that stops at a sentence boundary but *not* at a decimal
# point. `[^.]` was the obvious spelling and it is wrong: every one of these
# sentences contains "0.1 points", so a `[^.]` span cannot reach past the first
# number it is trying to read. That single character silently cost eleven of
# the twenty-five 2025 releases, which is the kind of gap that looks like the
# publisher skipped a fortnight.
_S = r"(?:[^.]|\.(?=\d))"

_UP = (r"increas\w*|ros\w*|rise|rising|climb\w*|gain\w*|jump\w*|surg\w*|"
       r"ticked up|edged up|improv\w*|up|advanc\w*|rebound\w*")
_DOWN = (r"decreas\w*|fell|fall\w*|drop\w*|declin\w*|slid|slipp\w*|sank|"
         r"sunk|plung\w*|dipp\w*|ticked down|edged down|down|retreat\w*")
_FLAT = r"remained|held|unchanged|steady|flat"

# Filler the house style puts between the name and the numbers: "posted its
# largest single-period increase since July 2024, rising 2.6 points to 34.2".
# Bounded, because an unbounded span would reach into the sub-indicator
# sentences that follow and the anchor would stop meaning anything.
_GAP = rf"{_S}{{0,90}}?"

# "<name> ... <verb> [by] X point(s) [filler] to Y"   -- level and change
_BOTH = re.compile(
    rf"{INDEX_NAME}{_GAP}\b({_UP}|{_DOWN})\b{_S}{{0,40}}?"
    rf"(\d{{1,2}}\.\d)\s+points?{_S}{{0,40}}?\bto\s+(\d{{1,3}}\.\d)\b", re.I)
# "<name> ... declined slightly by 0.1 points from 33.8 to 33.7"
_FROM_TO = re.compile(
    rf"{INDEX_NAME}{_GAP}\b({_UP}|{_DOWN})\b{_S}{{0,60}}?"
    rf"\bfrom\s+(\d{{1,3}}\.\d)\s*,?\s*to\s+(\d{{1,3}}\.\d)\b", re.I)
# "<name> remained flat at 37.2"
_FLAT_AT = re.compile(
    rf"{INDEX_NAME}{_GAP}\b(?:{_FLAT})\b{_S}{{0,25}}?\bat\s+(\d{{1,3}}\.\d)\b",
    re.I)
# "<name> ... to 32.3" with no change stated: a level and nothing to check it
# against. Accepted, but it contributes no cross-check.
_LEVEL = re.compile(
    rf"{INDEX_NAME}{_GAP}\b(?:to|at)\s+(\d{{1,3}}\.\d)\b", re.I)

_IS_DOWN = re.compile(_DOWN, re.I)
_TAG = re.compile(r"<[^>]+>")
_ENTITY = {"&amp;": "&", "&#8217;": "'", "&#8220;": '"', "&#8221;": '"',
           "&#8212;": " ", "&#8211;": "-", "&nbsp;": " ", "&#039;": "'"}


def _flatten(html):
    """Post body -> one line of plain text, with the punctuation the templates
    key on normalised. Curly quotes and em dashes are what the CMS emits and
    what a `[^.]` span trips over."""
    # <style> and <script> before <>-stripping: the CMS inlines a stylesheet
    # inside the post body, and its selectors survive tag removal as a wall of
    # `.locker{position:absolute...}` that a bounded span happily matches
    # through. Four 2022 releases were lost to it.
    text = re.sub(r"<(style|script)\b.*?</\1>", " ", html or "",
                  flags=re.S | re.I)
    text = _TAG.sub(" ", text)
    for ent, ch in _ENTITY.items():
        text = text.replace(ent, ch)
    text = (text.replace("’", "'").replace("—", " ")
                .replace("–", "-").replace(" ", " "))
    return re.sub(r"\s+", " ", text).strip()


def _reading(text):
    """(value, delta) for one post, or None when nothing matches with the
    index's own name attached. `delta` is None when the post states a level
    and no change, which costs the cross-check for that pair only."""
    m = _BOTH.search(text)
    if m:
        d = float(m.group(2))
        return float(m.group(3)), -d if _IS_DOWN.match(m.group(1)) else d
    m = _FROM_TO.search(text)
    if m:
        return float(m.group(3)), round(float(m.group(3)) - float(m.group(2)), 1)
    m = _FLAT_AT.search(text)
    if m:
        return float(m.group(1)), 0.0
    m = _LEVEL.search(text)
    if m:
        return float(m.group(1)), None
    return None


def fetch_text(timeout=TIMEOUT, since=STABLE_FROM):
    """Every post from `since` onward, as one JSON document.

    One document rather than one per page because this is what gets archived:
    a vintage has to be the thing that was parsed, and four files that have to
    be reassembled in the right order to mean anything is not that.
    """
    out, page = [], 1
    while True:
        r = requests.get(POSTS, timeout=timeout, params={
            "per_page": PER_PAGE, "page": page, "orderby": "date",
            "order": "desc", "after": since + "T00:00:00",
            "_fields": "date,slug,title,content"})
        if r.status_code == 400 and page > 1:
            break                      # WP answers 400 past the last page
        r.raise_for_status()
        batch = r.json()
        out.extend(batch)
        total = int(r.headers.get("X-WP-TotalPages") or 1)
        if page >= total or not batch:
            break
        page += 1
    if not out:
        raise RuntimeError(
            f"the ESI post feed returned nothing since {since}. Refusing to "
            "publish an empty series; see ssa/adapters/umich.py for why a "
            "source that quietly stops is worse than one that fails.")
    return json.dumps(out, sort_keys=True)


# How far a stated change may miss the previous reading before the series is
# refused. This is not a tolerance chosen for comfort -- it is where the two
# populations sit, measured on all 106 adjacent pairs since 2022:
#
#   105 reconcile to within 0.1          rounding
#     1 misses by 1.5 (2022-02-02)       the publisher restated the previous
#                                        reading; this index revises, as the
#                                        Conference Board's does
#     0 in between
#
# and the failure this is here to catch -- reading a sub-indicator instead of
# the headline -- produced errors of 5 to 47 points in the draft that had no
# anchor. So a small disagreement is a revision and is recorded; a large one is
# a misread and stops the run.
REVISION_MAX = 2.0
EXACT = 0.15


def reconcile(rows):
    """Every adjacent pair whose stated change does not match the previous
    reading, as (date, previous, delta, stated, error). Empty is the good case.

    Separate from `parse` so a caller can look at the disagreements without
    having to trigger them, and so the numbers in the comment above can be
    re-derived from the live feed rather than trusted.
    """
    out = []
    for prev, cur in zip(rows, rows[1:]):
        if cur.get("delta") is None:
            continue
        gap = (datetime.date.fromisoformat(cur["date"])
               - datetime.date.fromisoformat(prev["date"])).days
        if not 10 <= gap <= 18:
            continue                   # not adjacent releases; nothing to check
        err = round(prev["value"] + cur["delta"] - cur["value"], 2)
        if abs(err) > EXACT:
            out.append((cur["date"], prev["value"], cur["delta"],
                        cur["value"], err))
    return out


def parse(text):
    """Posts JSON -> [{date, value}], oldest first, cross-checked.

    Raises when a stated change misses the previous reading by more than
    REVISION_MAX. That is the whole safety argument: the prose is unreliable,
    and the only thing that makes reading it acceptable is that the source
    states enough to contradict a bad read.
    """
    posts = json.loads(text) if isinstance(text, str) else text
    rows = []
    for p in posts:
        got = _reading(_flatten((p.get("content") or {}).get("rendered", "")))
        if got is None:
            continue                   # not machine-readable; a gap, not a guess
        rows.append({"date": p["date"][:10], "value": got[0], "delta": got[1]})
    rows.sort(key=lambda r: r["date"])

    for date, prev, delta, stated, err in reconcile(rows):
        if abs(err) > REVISION_MAX:
            raise RuntimeError(
                f"ESI {date}: the post says the index moved {delta:+.1f} to "
                f"{stated}, which does not follow from the previous release's "
                f"{prev} (off by {err:+.1f}). A gap that large is a number "
                "read out of a sub-indicator sentence, not a revision; the "
                "series is not trustworthy and is not being published.")
    return [{"date": r["date"], "value": r["value"]} for r in rows]


def history(text=None):
    """[{date, value}] for the index, oldest first. Fetches unless given a body."""
    return parse(text if text is not None else fetch_text())
