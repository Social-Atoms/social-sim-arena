"""A fixed news corpus, identical for every entrant, frozen at each lock.

The third harness axis asks what *real-world information* is worth, separately
from the series history. The obvious way to run it is to let each model search
the web, and that is the wrong way: every model retrieves something different,
nobody can audit afterwards what any of them read, and in a backtest the
outcome has been published for months so search reads the answer. What the axis
actually needs is one corpus, the same for everyone, provably fixed before the
lock.

**Why Wikipedia's Current Events Portal.** It is the only free source that
satisfies all four requirements at once:

- **Dated by construction.** One page per calendar day
  (`Portal:Current events/2026 August 10`), so "what was known by date D" is a
  page range rather than a query with a date filter to be trusted.
- **Revision-addressable.** Every page carries full MediaWiki history, so the
  digest is built from the revision that existed *at the lock*, not from the
  page as it reads today. This is the same discipline as the lock snapshots in
  `refresh`, and for the same reason: a page edited afterwards with hindsight
  would leak the outcome backwards into a prompt that is supposed to predate it.
- **Curated and structured.** Human-edited, one line per event, grouped under
  stable headings ("Politics and elections", "Business and economy"), each with
  a source link. It is a news *summary*, not a scrape.
- **Free, keyless, and archivable**, so the corpus can be committed and a
  reader can regenerate any prompt we ever sent.

**Contamination.** Fetching by revision timestamp is what makes this safe in the
backtest, and it is not optional: without `asof`, a 2025 page would be read in
its 2026 state. `digest()` therefore requires an `asof` and refuses to guess.

**What this does not solve.** Wikipedia's coverage is broad rather than
US-opinion-specific, and its editors decide what is notable. That is a bias, but
it is a *fixed, inspectable, identical-for-everyone* bias, which is the property
the axis needs. An open web search has the same bias plus irreproducibility.
"""
import json
import os
import re
import threading
import time
from datetime import date, timedelta

import requests

API = "https://en.wikipedia.org/w/api.php"
UA = "social-simulation-arena/1.0 (research benchmark; contact via repository)"
TIMEOUT = 60

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARCHIVE = os.path.join(ROOT, "news")

# Headings kept, in the order they are rendered. Public opinion on a president
# moves on politics, the economy and the visible parts of law and disaster; the
# sports and arts sections are dropped because they add tokens without moving
# any tracker in this arena.
CATEGORIES = (
    "Politics and elections",
    "Business and economy",
    "Law and crime",
    "Armed conflicts and attacks",
    "Disasters and accidents",
    "Health and environment",
    "International relations",
)

DEFAULT_WINDOW_DAYS = 14
DEFAULT_MAX_ITEMS = 8          # per day, per category, after filtering


def page_title(d):
    return f"Portal:Current events/{d.year} {d:%B} {d.day}"


# Wikipedia is a free service being asked for a favour, and it says so with a
# 429. One request in flight at a time, spaced, with backoff that honours
# Retry-After. A full fourteen-day digest is ~28 requests, so the spacing costs
# seconds once and nothing at all afterwards -- the archive answers every rerun.
MIN_INTERVAL = 0.35
MAX_RETRIES = 5
_last_call = [0.0]
_call_lock = threading.Lock()


def _get(params):
    for attempt in range(1, MAX_RETRIES + 1):
        with _call_lock:
            wait = MIN_INTERVAL - (time.monotonic() - _last_call[0])
            if wait > 0:
                time.sleep(wait)
            r = requests.get(API, params=dict(params, format="json"),
                             headers={"User-Agent": UA}, timeout=TIMEOUT)
            _last_call[0] = time.monotonic()
        if r.status_code == 429 and attempt < MAX_RETRIES:
            delay = float(r.headers.get("Retry-After") or 0) or 2.0 * attempt
            time.sleep(min(delay, 30.0))
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return r.json()


def revision_as_of(title, asof):
    """Newest revision id at or before `asof` (ISO Z string), or None.

    None means the page did not exist yet at that moment, which for a
    Current Events page means the day had not happened or had not been written
    up. That is information, not an error: the caller records the gap.
    """
    data = _get({"action": "query", "prop": "revisions", "titles": title,
                 "rvprop": "ids|timestamp", "rvlimit": 1, "rvdir": "older",
                 "rvstart": asof})
    for page in data.get("query", {}).get("pages", {}).values():
        if "missing" in page:
            return None
        revs = page.get("revisions") or []
        if revs:
            return {"revid": revs[0]["revid"], "timestamp": revs[0]["timestamp"]}
    return None


def wikitext(revid):
    data = _get({"action": "parse", "oldid": revid, "prop": "wikitext"})
    return data["parse"]["wikitext"]["*"]


# --- turning wikitext into something a model should read --------------------

_ILL = re.compile(r"\{\{ill\|([^|}]+)[^}]*\}\}")          # {{ill|Name|ar|...}}
_TPL = re.compile(r"\{\{[^{}]*\}\}")
_PIPED = re.compile(r"\[\[[^\]|]*\|([^\]]+)\]\]")          # [[Target|shown]]
_LINK = re.compile(r"\[\[([^\]]+)\]\]")                    # [[Target]]
_EXT = re.compile(r"\[https?://\S+?\s+([^\]]+)\]")         # [url (Source)]
_BARE = re.compile(r"\[https?://\S+?\]")
_BOLD = re.compile(r"'''?")
_REF = re.compile(r"<ref[^>]*>.*?</ref>|<ref[^>]*/>", re.S)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def strip_markup(s):
    s = _REF.sub("", s)
    s = _ILL.sub(r"\1", s)
    s = _TPL.sub("", s)
    s = _PIPED.sub(r"\1", s)
    s = _LINK.sub(r"\1", s)
    s = _EXT.sub(r"(\1)", s)
    s = _BARE.sub("", s)
    s = _BOLD.sub("", s)
    s = _TAG.sub("", s)
    return _WS.sub(" ", s).strip()


def parse_items(text, categories=CATEGORIES):
    """Wikitext -> [(category, item)], keeping only the listed categories.

    The page nests bullets to show topic hierarchy, and only the *leaf* of each
    chain is an event -- the lines above it are bare topic links ("2026 Iran
    war") that describe where the item sits, not what happened. A line is a leaf
    when the line after it is not deeper than it is. Emitting the parents too
    would fill the digest with headlines that have no news in them.
    """
    wanted = set(categories)
    rows = []                       # (category, depth, text)
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        head = re.fullmatch(r"'''(.+?)'''", line)
        if head:
            current = strip_markup(head.group(1))
            continue
        if not line.startswith("*") or current not in wanted:
            continue
        depth = len(line) - len(line.lstrip("*"))
        body = strip_markup(line.lstrip("*").strip())
        if body:
            rows.append((current, depth, body))

    out, seen = [], set()
    for i, (cat, depth, body) in enumerate(rows):
        deeper_next = (i + 1 < len(rows) and rows[i + 1][0] == cat
                       and rows[i + 1][1] > depth)
        if deeper_next:
            continue                # a parent topic, not an event
        if body in seen:
            continue
        seen.add(body)
        out.append((cat, body))
    return out


# --- archive ----------------------------------------------------------------

def archive_path(d):
    return os.path.join(ARCHIVE, f"{d.isoformat()}.json")


def load_archived(d):
    p = archive_path(d)
    if not os.path.exists(p):
        return None
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def save_archived(d, body):
    os.makedirs(ARCHIVE, exist_ok=True)
    with open(archive_path(d), "w") as f:
        json.dump(body, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")


def day(d, asof, categories=CATEGORIES, use_archive=True):
    """One day's items, from the revision current at `asof`.

    Archived on first fetch and read from the archive after, so a prompt is
    reproducible from the repository and a rerun costs no requests. The archive
    records the revision id it came from, which is what makes the claim
    auditable rather than merely asserted.
    """
    if use_archive:
        got = load_archived(d)
        if got and got.get("asof") == asof:
            return got
    rev = revision_as_of(page_title(d), asof)
    if rev is None:
        body = {"date": d.isoformat(), "asof": asof, "revid": None,
                "revision_timestamp": None, "items": [],
                "note": "no revision existed at asof"}
    else:
        items = parse_items(wikitext(rev["revid"]), categories)
        body = {"date": d.isoformat(), "asof": asof, "revid": rev["revid"],
                "revision_timestamp": rev["timestamp"],
                "items": [{"category": c, "text": t} for c, t in items]}
    if use_archive:
        save_archived(d, body)
    return body


def digest(asof, days=DEFAULT_WINDOW_DAYS, max_items=DEFAULT_MAX_ITEMS,
           categories=CATEGORIES, use_archive=True):
    """The corpus every entrant sees for a round, as plain text.

    `asof` is the round's lock time (ISO Z). Days run up to, and not including,
    the lock day: a page for the lock day itself would be mid-write and would
    differ between an entrant filed at 09:00 and one filed at 13:00, which
    would make the condition unfair in a way nobody could see afterwards.
    """
    if not asof:
        raise ValueError("digest requires an asof timestamp; without one the "
                         "pages would be read in their present state and the "
                         "backtest would leak the outcome")
    end = date.fromisoformat(asof[:10])
    out, missing = [], 0
    for i in range(days, 0, -1):
        d = end - timedelta(days=i)
        body = day(d, asof, categories, use_archive)
        if not body["items"]:
            missing += 1
            continue
        by_cat = {}
        for it in body["items"]:
            by_cat.setdefault(it["category"], []).append(it["text"])
        lines = [f"{d.isoformat()}:"]
        for cat in categories:
            for text in by_cat.get(cat, [])[:max_items]:
                lines.append(f"  [{cat}] {text}")
        if len(lines) > 1:
            out.append("\n".join(lines))
    return {"text": "\n".join(out), "days": days, "asof": asof,
            "days_missing": missing}
