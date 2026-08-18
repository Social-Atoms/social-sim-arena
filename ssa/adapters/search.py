"""One search index for every entrant, and a record of everything it returned.

  POST https://api.tavily.com/search

**Why the arena runs its own search instead of the vendors' hosted tools.**

Every frontier vendor now ships a server-side search tool, and using them is
the obvious move: no scraper, no key, three lines per protocol. It is also a
confounded experiment. Anthropic's tool searches Anthropic's index, OpenAI's
searches Bing-derived results, Gemini's searches Google. When `claude-opus-web`
beats `gpt-5.6-sol-web`, nothing in the design says whether that is the model
or the index behind it -- and the arena exists to compare models.

Worse, hosted search is a *vendor* capability and not a property of the wire
protocol. Six of fifteen entered models speak OpenAI-compatible chat
completions and serve no search tool at all, so the hosted design could only
ever cover 9 of 15. One index covers all 15 and the comparison means what it
says.

**What is fixed and what the model chooses.** The index, the parameters, the
number of rounds, the number of results and how they are rendered into text are
identical for every entrant. The *queries* are the model's own, because knowing
what to look for is the capability being measured -- that is the whole
difference between this and the `news` condition, where we choose the corpus
and everyone reads the same words.

**Every query and every result is archived, and the archive is the cache.**

    search/cache/<sha256(query+params)>.json   the raw reply, shared across
                                               entrants: overlapping keywords
                                               cost one request, not fifteen
    search/rounds/<round>/<entrant>.json       what this entrant asked, when,
                                               and what came back

One file doing three jobs, the same trick `cache/model_backtest/` already
pulls: it is the cost control, the audit trail, and the only reason a `web`
forecast can be re-derived at all. Search results change minute to minute, so
without the archive this arm would be the single non-reproducible part of a
repository whose headline claim is reproducibility.

**The search happens at lock time and is frozen there.** Not at scoring time,
not lazily -- the corpus is part of what the entrant was shown, and it is
committed alongside the forecast for the same reason `locks/` exists.

**This arm cannot be backtested and the code refuses to try.** A search run
today over a 2025 release retrieves the published answer.
`harness.assert_prospective` raises on the `web` context; nothing here weakens
that.
"""
import hashlib
import json
import os
import time
from datetime import datetime, timezone

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARCHIVE = os.path.join(ROOT, "search")
CACHE = os.path.join(ARCHIVE, "cache")
ROUNDS = os.path.join(ARCHIVE, "rounds")

ENDPOINT = "https://api.tavily.com/search"
ENV = "TAVILY_API_KEY"
TIMEOUT = 45

# --- the knobs, all in one place -------------------------------------------
#
# These values are NOT settled. They are the shape of the experiment -- how
# many queries a model may issue, how many rounds of refinement it gets, how
# much text comes back -- and picking them by feel is how an arm ends up
# measuring the budget instead of the capability. They are gathered here, and
# only here, so the decision is one diff.
#
# See the open issue on search parameters before changing any of them; the
# current values are deliberately conservative placeholders chosen to keep the
# corpus comparable to the `news` condition (~5,000 tokens), not because they
# are known to be right.
MAX_QUERIES = 4          # per round
MAX_ROUNDS = 1           # 1 = one shot; 2 = one refinement pass
RESULTS_PER_QUERY = 3
SNIPPET_CHARS = 700      # per result, after which the body is cut
SEARCH_DEPTH = "basic"   # tavily: "basic" or "advanced"
TOPIC = "news"
DAYS = 14                # recency window, matched to the news corpus

# A cached reply older than this is a miss. The cache exists so that fifteen
# models issuing overlapping keywords inside one round's window cost one
# request, not fifteen -- it must not also serve last week's snippets to next
# week's round just because two models phrased the same query. Matched to
# SSA_FILE_WINDOW_DAYS: within one round's buying window a repeat is a hit,
# across rounds it is not. Overwriting an aged entry loses nothing, because
# every round's own corpus is stored in full under search/rounds/.
CACHE_MAX_AGE_DAYS = float(os.environ.get("SSA_FILE_WINDOW_DAYS") or "3")


def params_signature():
    """Everything about the retrieval that is not the query text.

    Part of the cache key, so changing the depth or the result count correctly
    misses rather than serving a corpus gathered under different rules -- the
    same reason `harness.call_identity` carries the base URL.
    """
    return (f"tavily|{SEARCH_DEPTH}|{TOPIC}|{RESULTS_PER_QUERY}|{DAYS}"
            f"|{SNIPPET_CHARS}")


def cache_key(query):
    return hashlib.sha256(
        (params_signature() + "\n" + query.strip().lower()).encode()
    ).hexdigest()


def _cache_path(query):
    return os.path.join(CACHE, cache_key(query) + ".json")


def cached(query):
    path = _cache_path(query)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            rec = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    try:
        fetched = datetime.fromisoformat(
            (rec.get("fetched_at") or "").replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - fetched).total_seconds()
    except ValueError:
        return None            # unstampable is unserveable; refetch restamps
    if age > CACHE_MAX_AGE_DAYS * 86400:
        return None
    return rec


def has_key():
    return bool(os.environ.get(ENV))


def search(query, now=None):
    """One query, from the archive when it is there and from Tavily otherwise.

    The archived reply is returned verbatim. A query already asked this season
    costs nothing, which is what makes fifteen models issuing overlapping
    keywords affordable.
    """
    hit = cached(query)
    if hit is not None:
        return hit
    key = os.environ.get(ENV)
    if not key:
        raise RuntimeError(
            f"no {ENV} in the environment and '{query}' is not in the archive. "
            "The search arm bills per query; refusing to file a forecast whose "
            "corpus we cannot produce, rather than one silently missing it.")
    r = requests.post(ENDPOINT, timeout=TIMEOUT, json={
        "api_key": key, "query": query, "topic": TOPIC,
        "search_depth": SEARCH_DEPTH, "max_results": RESULTS_PER_QUERY,
        "days": DAYS, "include_answer": False, "include_raw_content": False})
    if r.status_code >= 400:
        detail = " ".join((r.text or "").split())[:300]
        raise RuntimeError(f"Tavily HTTP {r.status_code}: {detail}")
    body = r.json()
    record = {
        "query": query,
        "params": params_signature(),
        "fetched_at": now or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "results": [{"title": x.get("title"), "url": x.get("url"),
                     "published": x.get("published_date"),
                     "content": (x.get("content") or "")[:SNIPPET_CHARS]}
                    for x in (body.get("results") or [])],
    }
    os.makedirs(CACHE, exist_ok=True)
    with open(_cache_path(query), "w") as f:
        json.dump(record, f, indent=1, sort_keys=True, ensure_ascii=False)
    return record


def gather(queries, now=None):
    """Run a model's queries in order, dropping duplicates.

    Duplicates are dropped rather than counted because a model that asks the
    same thing twice should not get a larger corpus than one that asks two
    different things -- the budget is on distinct information, not on calls.
    """
    seen, out = set(), []
    for q in queries[:MAX_QUERIES]:
        q = (q or "").strip()
        if not q or q.lower() in seen:
            continue
        seen.add(q.lower())
        out.append(search(q, now=now))
    return out


def render(records):
    """The retrieved corpus, as the text an entrant actually sees.

    Rendering is fixed and shared, so two entrants that retrieved the same
    documents read the same words. A per-model formatter would put a second
    uncontrolled variable in an arm that exists to isolate one.
    """
    if not records:
        return "  (no results)"
    lines = []
    for rec in records:
        lines.append(f'  Query: "{rec["query"]}"')
        if not rec["results"]:
            lines.append("    (no results)")
        for x in rec["results"]:
            when = f" ({x['published'][:10]})" if x.get("published") else ""
            lines.append(f"    - {x.get('title') or 'untitled'}{when}")
            body = " ".join((x.get("content") or "").split())
            if body:
                lines.append(f"      {body}")
    return "\n".join(lines)


def round_path(round_id, entrant):
    return os.path.join(ROUNDS, round_id, entrant + ".json")


def record_round(round_id, entrant, queries, records, now=None):
    """What this entrant asked and received, frozen next to its forecast.

    Written once and never rewritten: the corpus is part of what the entrant
    was shown at lock time, and a file that moves afterwards proves nothing.
    """
    path = round_path(round_id, entrant)
    if os.path.exists(path):
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({
            "round_id": round_id, "entrant": entrant,
            "asked_at": now or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "params": params_signature(),
            "queries": queries,
            "results": records,
        }, f, indent=1, sort_keys=True, ensure_ascii=False)
    return path


def for_round(round_id, entrant):
    """The frozen corpus for one (round, entrant), or None."""
    path = round_path(round_id, entrant)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
