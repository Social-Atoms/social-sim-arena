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
from datetime import date, datetime, timedelta, timezone

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
        try:
            with _call_lock:
                wait = MIN_INTERVAL - (time.monotonic() - _last_call[0])
                if wait > 0:
                    time.sleep(wait)
                r = requests.get(API, params=dict(params, format="json"),
                                 headers={"User-Agent": UA}, timeout=TIMEOUT)
                _last_call[0] = time.monotonic()
        except requests.RequestException:
            # The failure a long pass actually hits. Over a thousand requests,
            # 67 days died on a reset connection or a TLS EOF and none on
            # anything Wikipedia meant to say. Retrying costs one request and
            # saves the day; not retrying leaves a hole in the corpus that no
            # later run knows to look for.
            if attempt == MAX_RETRIES:
                raise
            time.sleep(min(2.0 * attempt, 30.0))
            continue
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


def revision_index(title):
    """Every revision of a page, oldest first, or None if the page is missing.

    One request answers every `asof` the page will ever be asked about, which
    is the whole point: a Current Events page is edited for two to six days
    after its date and then never again, so fourteen different locks resolve to
    a handful of distinct revisions. Asking per lock pays for the same answer
    fourteen times.
    """
    revs, cont = [], None
    while True:
        params = {"action": "query", "prop": "revisions", "titles": title,
                  "rvprop": "ids|timestamp", "rvlimit": 500, "rvdir": "newer"}
        if cont:
            params["rvcontinue"] = cont
        data = _get(params)
        for page in data.get("query", {}).get("pages", {}).values():
            if "missing" in page:
                return None
            revs.extend({"revid": r["revid"], "timestamp": r["timestamp"]}
                        for r in page.get("revisions") or [])
        cont = (data.get("continue") or {}).get("rvcontinue")
        if not cont:
            return revs


def wikitext(revid):
    data = _get({"action": "parse", "oldid": revid, "prop": "wikitext"})
    return data["parse"]["wikitext"]["*"]


# Fifty is the API's own ceiling for an anonymous client. A day's window never
# resolves to more than a handful of revisions, so in practice this is one
# request per page instead of one per revision -- which, at the ~2.8s a round
# trip actually costs, is the difference between a three-hour pass over the
# archive and a one-hour one.
REVIDS_PER_REQUEST = 50


def wikitext_many(revids):
    """{revid: wikitext} for revisions of one page, in as few requests as the
    API allows. Revisions the API declines to return are simply absent, which
    the caller must treat as "not archived" rather than "empty"."""
    out = {}
    revids = list(revids)
    for i in range(0, len(revids), REVIDS_PER_REQUEST):
        chunk = revids[i:i + REVIDS_PER_REQUEST]
        data = _get({"action": "query", "prop": "revisions",
                     "revids": "|".join(str(x) for x in chunk),
                     "rvprop": "ids|content", "rvslots": "main"})
        for page in data.get("query", {}).get("pages", {}).values():
            for rev in page.get("revisions") or []:
                main = (rev.get("slots") or {}).get("main") or {}
                text = main.get("*")
                if text is not None:
                    out[rev["revid"]] = text
    return out


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

    `categories=None` keeps every heading. That is what the revision archive
    stores, so that changing CATEGORIES costs a re-parse rather than an hour of
    Wikipedia's time: re-reading the archive is free, refetching it is not.
    """
    wanted = None if categories is None else set(categories)
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
        if not line.startswith("*") or current is None:
            continue
        if wanted is not None and current not in wanted:
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

# The cache is keyed by (page date, asof), not by page date alone. The same
# day's page has a different revision at each lock, so a date-only key would
# let one round's corpus overwrite another's and the archive would stop being
# a record of what was actually sent. Cheap to store; impossible to reconstruct
# afterwards if it is wrong.
PAGES = os.path.join(ARCHIVE, "pages")
ROUNDS = os.path.join(ARCHIVE, "rounds")

# The corpus itself, one file per calendar day, keyed by *revision* rather than
# by lock. Measured on five sample days: a page carries 40-82 revisions in
# total, but the fourteen locks that ever read it resolve to only 2-5 of them,
# and the page stops changing two to six days after its date. Storing per lock
# therefore keeps up to fourteen copies of the same text and, worse, is useless
# to a different set of locks -- adding a series would refetch the same days
# again. Storing the revision index plus the text of each distinct revision
# answers every lock, including locks that do not exist yet.
DAYS = os.path.join(ARCHIVE, "days")

# Which asofs a page must be able to answer offline: a page dated D is read by
# any lock in D+1 .. D+`digest days`, at the lock's own hour. The grid below
# fixes 14:00Z because that is the hour `model_backtest.pseudo_round` gives
# every historical lock; a live lock at another hour still resolves against the
# stored index, and pays one request for that revision's text if it is one the
# grid did not already reach.
ARCHIVE_WINDOW_DAYS = 16       # two days of headroom over DEFAULT_WINDOW_DAYS
ARCHIVE_HOUR = "T14:00:00Z"


def _window_closed(asof, now=None):
    """True once `asof` is in the past, i.e. the corpus for it is final."""
    return (now or datetime.now(timezone.utc)) >= datetime.strptime(
        asof, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def archive_path(d, asof):
    return os.path.join(PAGES, f"{d.isoformat()}.asof-{asof[:10]}.json")


def load_archived(d, asof):
    p = archive_path(d, asof)
    if not os.path.exists(p):
        return None
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def save_archived(d, asof, body):
    os.makedirs(PAGES, exist_ok=True)
    with open(archive_path(d, asof), "w") as f:
        json.dump(body, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")


def day_path(d):
    return os.path.join(DAYS, f"{d.isoformat()}.json")


def load_day(d):
    p = day_path(d)
    if not os.path.exists(p):
        return None
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def save_day(d, body):
    os.makedirs(DAYS, exist_ok=True)
    p = day_path(d)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(body, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, p)


def resolve_index(index, asof):
    """Newest revision at or before `asof`, from a stored index. None if the
    page had no revision yet -- the same answer `revision_as_of` gives, without
    the request. Timestamps are ISO Z, so ordering is string ordering."""
    chosen = None
    for r in index or ():
        if r["timestamp"] <= asof:
            chosen = r
        else:
            break
    return chosen


def _utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def archive_asofs(d, window=ARCHIVE_WINDOW_DAYS):
    return [(d + timedelta(days=k)).isoformat() + ARCHIVE_HOUR
            for k in range(1, window + 1)]


def store_revision(body, revid, items):
    """Add one revision's items to the day's shared pool, by index.

    Successive revisions of a page are near-identical -- the later one adds a
    few events to the earlier one -- so storing each in full keeps five copies
    of the same text. Measured over three sample days, pooling the distinct
    items and referring to them by index cuts the archive to a third with
    nothing lost: each revision's exact content is still recoverable, item for
    item, in order.
    """
    pool = body.setdefault("items", [])
    where = {(it["category"], it["text"]): i for i, it in enumerate(pool)}
    out = []
    for c, t in items:
        i = where.get((c, t))
        if i is None:
            i = len(pool)
            where[(c, t)] = i
            pool.append({"category": c, "text": t})
        out.append(i)
    body.setdefault("revisions", {})[str(revid)] = out
    return out


def revision_items(body, revid):
    """One revision's items, rebuilt from the day's pool, or None if unstored."""
    idxs = (body.get("revisions") or {}).get(str(revid))
    if idxs is None:
        return None
    pool = body.get("items") or []
    return [pool[i] for i in idxs]


def archive_day(d, window=ARCHIVE_WINDOW_DAYS, refresh=False):
    """Store one page's revision index and the text of the revisions it needs.

    Returns `(body, requests_made)`. Re-runnable and resumable: an index is
    refetched only when it cannot yet answer the whole window, which is exactly
    the case for a page whose window is still open, and a revision's text is
    fetched once ever.

    An index fetched at time T answers any asof <= T and no asof after it, so
    `fetched_at` is the archive's own honesty check -- a day near the present
    is served from the network until its window closes, then never again.
    """
    body = None if refresh else load_day(d)
    if body is None:
        body = {"date": d.isoformat(), "title": page_title(d), "index": [],
                "items": [], "revisions": {}, "fetched_at": None,
                "missing": False}
    asofs = archive_asofs(d, window)
    if refresh or not body.get("fetched_at") or body["fetched_at"] < asofs[-1]:
        at = _utcnow()
        idx = revision_index(body["title"])
        body["index"] = idx or []
        body["missing"] = idx is None
        body["fetched_at"] = at
        calls = 1
    else:
        calls = 0
    want = []
    for asof in asofs:
        if body["fetched_at"] < asof:
            break                      # not knowable yet; a later run gets it
        rev = resolve_index(body["index"], asof)
        if rev is None or str(rev["revid"]) in body.get("revisions", {}):
            continue
        if rev["revid"] not in want:
            want.append(rev["revid"])
    if want:
        texts = wikitext_many(want)
        for revid in want:
            if revid in texts:
                store_revision(body, revid, parse_items(texts[revid], None))
        calls += (len(want) + REVIDS_PER_REQUEST - 1) // REVIDS_PER_REQUEST
    if calls:
        save_day(d, body)
    return body, calls


def from_day_archive(d, asof, categories=CATEGORIES):
    """One day's items for `asof`, served entirely from the revision archive,
    or None if the archive cannot answer honestly. Fetches the one revision's
    text if the index covers the asof but the grid never reached that hour."""
    body = load_day(d)
    if not body or not body.get("fetched_at") or body["fetched_at"] < asof:
        return None
    rev = resolve_index(body["index"], asof)
    if rev is None:
        return {"date": d.isoformat(), "asof": asof, "revid": None,
                "revision_timestamp": None, "items": [],
                "note": "no revision existed at asof"}
    items = revision_items(body, rev["revid"])
    if items is None:
        store_revision(body, rev["revid"], parse_items(wikitext(rev["revid"]), None))
        items = revision_items(body, rev["revid"])
        save_day(d, body)
    wanted = set(categories)
    return {"date": d.isoformat(), "asof": asof, "revid": rev["revid"],
            "revision_timestamp": rev["timestamp"],
            "items": [it for it in items if it["category"] in wanted]}


def day(d, asof, categories=CATEGORIES, use_archive=True):
    """One day's items, from the revision current at `asof`.

    Archived on first fetch and read from the archive after, so a prompt is
    reproducible from the repository and a rerun costs no requests. The archive
    records the revision id it came from, which is what makes the claim
    auditable rather than merely asserted.
    """
    if use_archive:
        got = load_archived(d, asof)
        if got and got.get("asof") == asof:
            return got
        got = from_day_archive(d, asof, categories)
        if got is not None:
            if _window_closed(asof):
                save_archived(d, asof, got)
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
    # Same rule as for_round: only persist once the asof is in the past. A
    # page fetched now for a lock ten days out records today's revision, not
    # the one that will exist at the lock, and caching it would freeze the
    # wrong answer into the archive for good.
    if use_archive and _window_closed(asof):
        save_archived(d, asof, body)
    return body


# One corpus per lock is read once per *entrant* in the live season and once
# per (series, release, entrant) in the backtest -- tens of thousands of
# rebuilds of the same fourteen files. The digest is a pure function of its
# arguments, so remembering it inside a run costs a few tens of MB and turns
# `model_backtest.plan` from minutes of file reads into seconds. Bounded, since
# a long run touches hundreds of distinct locks.
DIGEST_MEMO_MAX = 1024
_digest_memo = {}
_digest_lock = threading.Lock()


def digest(asof, days=DEFAULT_WINDOW_DAYS, max_items=DEFAULT_MAX_ITEMS,
           categories=CATEGORIES, use_archive=True):
    """The corpus every entrant sees for a round, as plain text.

    `asof` is the round's lock time (ISO Z). Days run up to, and not including,
    the lock day: a page for the lock day itself would be mid-write and would
    differ between an entrant filed at 09:00 and one filed at 13:00, which
    would make the condition unfair in a way nobody could see afterwards.
    """
    key = (asof, days, max_items, tuple(categories), use_archive)
    with _digest_lock:
        got = _digest_memo.get(key)
    if got is not None:
        return dict(got)
    out = _digest(asof, days, max_items, categories, use_archive)
    with _digest_lock:
        if len(_digest_memo) >= DIGEST_MEMO_MAX:
            _digest_memo.clear()
        _digest_memo[key] = out
    return dict(out)


def _digest(asof, days, max_items, categories, use_archive):
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


def for_round(round_id, asof, days=DEFAULT_WINDOW_DAYS, now=None, **kw):
    """The digest for one round, archived as the record of what was sent.

    The per-day files are a fetch cache; this is the audit record. One file per
    round holding the exact text every entrant in that round saw, plus the
    revision id behind each day, so a reader can reconstruct any prompt without
    trusting either Wikipedia's current state or ours.

    **A digest is only archived once its window has closed.** The window is the
    fourteen days before the lock, so before the lock most of it has not
    happened: pre-fetching a round that locks in ten days produced six days of
    news out of fourteen, and archiving that would have been worse than not
    caching at all -- `for_round` serves the archive whenever the asof matches,
    so the round would have used the truncated copy at lock time instead of the
    corpus that actually existed by then. Partial digests are therefore
    computed and returned but never written, and a stored record that is
    somehow incomplete is ignored and refetched.
    """
    os.makedirs(ROUNDS, exist_ok=True)
    path = os.path.join(ROUNDS, f"{round_id}.json")
    closed = _window_closed(asof, now)
    if os.path.exists(path):
        try:
            with open(path) as f:
                got = json.load(f)
            if got.get("asof") == asof and got.get("window_closed"):
                return got
        except (OSError, json.JSONDecodeError):
            pass
    out = digest(asof, days=days, **kw)
    out["window_closed"] = closed
    end = date.fromisoformat(asof[:10])
    sources = []
    for i in range(days, 0, -1):
        d = end - timedelta(days=i)
        body = load_archived(d, asof) or {}
        sources.append({"date": d.isoformat(), "revid": body.get("revid"),
                        "revision_timestamp": body.get("revision_timestamp")})
    out["round_id"] = round_id
    out["sources"] = sources
    if closed:
        with open(path, "w") as f:
            json.dump(out, f, indent=2, ensure_ascii=False, sort_keys=True)
            f.write("\n")
    return out
