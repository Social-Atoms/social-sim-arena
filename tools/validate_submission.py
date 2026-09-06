"""Validate forecast submissions. Used by CI on every pull request and
runnable locally:

  python tools/validate_submission.py forecasts/<round_id>/<entrant>.json

Checks, in order:
1. JSON parses and matches schema/forecast.schema.json.
2. round_id matches the directory, entrant matches the file name.
3. The round exists in questions/season0.json.
4. The answer is the shape the round asked for: a scalar round takes a
   `topline`, a profile round takes a `profile` carrying exactly the cells the
   round names, a ranking round takes a `ranking` list of exactly the length it
   names (and, for a fixed-basket round, exactly its items). None substitutes
   for another.
5. The round is still open (now < lock_at). CI runs this at merge time, so
   the commit that lands after the lock fails loudly.
6. Prints the canonical sha256, which the leaderboard and the paper cite.

A `forecasts/_*/` directory holds examples, not submissions. Two of the checks
above cannot apply to one: the directory half of 2, because `_` is not legal in
a round_id, and 5, because an example names a round that has already locked.
Every other check runs. An example is the file a new entrant copies.

Canonical form: JSON with sorted keys and separators (',', ':'), UTF-8.

This file imports nothing from `ssa/`, deliberately: CI runs it on a bare
checkout and it degrades to hand-rolled checks when even jsonschema is absent.
That is why the round-type discriminators below are spelled out here rather than
imported from `ssa/profile_round.py` and `ssa/ranking_round.py`, which own them.
"""
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Mirrors ssa.profile_round.TARGET_TYPE. A round without this is scalar.
PROFILE_TARGET_TYPE = "profile_energy"

# Mirrors ssa.ranking_round.TARGET_TYPE.
RANKING_TARGET_TYPE = "ranking_list"

# Mirrors ssa.adapters.wikipedia.EXCLUSION_RULE_ID and its two constants. The
# rule *is* part of the question -- a title it drops cannot be part of any
# answer -- so it is checked here, at pull-request time, rather than being
# discovered at scoring time when the entrant can no longer fix it. The
# duplication is the price of this file importing nothing; the copy is small,
# frozen, and versioned, and a round naming a rule id this copy does not know
# fails loudly rather than being waved through.
WIKI_EXCLUSION_RULE_ID = "main_page_and_namespaces_v1"
WIKI_EXCLUDED_TITLES = ("Main_Page",)
WIKI_NAMESPACE_PREFIXES = (
    "Special:", "Wikipedia:", "Portal:", "Help:", "File:", "Template:",
    "Category:", "Draft:", "User:", "Talk:",
    "Wikipedia_talk:", "Portal_talk:", "Help_talk:", "File_talk:",
    "Template_talk:", "Category_talk:", "Draft_talk:", "User_talk:",
)


# Mirrors ssa/batches.py, which owns the weekly submission calendar and the
# reasoning behind it. Same duplication trade as the Wikipedia rule above: this
# file imports nothing, so the copy is spelled out, kept tiny, and pinned by
# `tests/test_batches.py`, which walks a year of hourly locks and fails if the
# two ever disagree by a second.
BATCH_WEEKDAY = 0                                          # Monday
BATCH_HOUR_UTC = 12
BATCH_FIRST_DEADLINE = datetime(2026, 9, 14, BATCH_HOUR_UTC, tzinfo=timezone.utc)


def batch_deadline(lock_at):
    """The last Monday 12:00Z strictly before `lock_at`."""
    back = (lock_at.weekday() - BATCH_WEEKDAY) % 7
    candidate = (lock_at - timedelta(days=back)).replace(
        hour=BATCH_HOUR_UTC, minute=0, second=0, microsecond=0)
    if candidate >= lock_at:
        candidate -= timedelta(days=7)
    return candidate


def effective_deadline(lock_at):
    """When a submission for this round must be in: the round's own lock.

    Mirrors `ssa.batches.effective_deadline`, which carries the reasoning. The
    weekly batch deadline that briefly sat here made the horizon a property of
    the calendar rather than of the question -- two rounds on one board
    forecast a day and a month ahead of their answers.
    """
    return lock_at


def fail(msg):
    print("FAIL:", msg)
    sys.exit(1)


# Set when `jsonschema` could not be imported, so the schema was never applied.
# Both call sites below fall back to a handful of required-key checks, which
# cannot see a closed schema's `additionalProperties: false`, a wrong type, or
# a pattern. That is a reasonable convenience -- a participant on a laptop with
# no virtualenv should get an answer rather than a traceback -- but it must
# never print a bare `OK`. A false pass hours before a deadline is worse than
# the traceback it was avoiding: CI runs the real schema and will reject the
# file, and by then the batch may have closed.
DEGRADED = False


def note_schema_unchecked():
    global DEGRADED
    if not DEGRADED:
        DEGRADED = True
        print("WARNING: `jsonschema` is not installed, so the schema itself "
              "was NOT checked.", file=sys.stderr)
        print("         Only a few required keys were. Unknown fields, wrong "
              "types and bad patterns", file=sys.stderr)
        print("         will pass here and still be rejected by CI. Run "
              "`pip install -r requirements.txt`", file=sys.stderr)
        print("         and validate again before you rely on this result.",
              file=sys.stderr)


def ok(rel, extra=""):
    """The one place a success line is printed, so it cannot claim too much."""
    if DEGRADED:
        extra = (extra + ", " if extra else "") + "SCHEMA NOT CHECKED"
    print(f"OK{f' ({extra})' if extra else ''}: {rel}")


def canonical_sha256(obj):
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def validate_entrant(path, author=None, base_ref=None):
    rel = os.path.relpath(os.path.abspath(path), ROOT)
    with open(path) as f:
        try:
            e = json.load(f)
        except json.JSONDecodeError as err:
            fail(f"{rel}: not valid JSON: {err}")
    try:
        import jsonschema
        with open(os.path.join(ROOT, "schema", "entrant.schema.json")) as f:
            schema = json.load(f)
        jsonschema.validate(e, schema)
    except ImportError:
        note_schema_unchecked()
        for key in ("entrant_id", "name", "type", "method"):
            if key not in e:
                fail(f"{rel}: missing required field '{key}'")
    except Exception as err:
        fail(f"{rel}: schema violation: {err}")
    if e["entrant_id"] + ".json" != os.path.basename(path):
        fail(f"{rel}: entrant_id '{e['entrant_id']}' does not match file name")
    check_entrant_owner(rel, e, author, base_ref)
    ok(rel, f"owner @{e['github']}" if e.get("github") else "")


# --- who may change what -----------------------------------------------------
#
# A registration or a forecast arrives as a pull request from anyone with a
# GitHub account. The file's `github` field names the account that owns the
# entrant; CI passes the pull request's author with `--author` and the base
# branch with `--base`, and these checks refuse an edit by anyone else. The
# owner is read from the *base* version of the registration, so a pull request
# cannot rewrite `github` to its own author in the same change. Maintainers may
# edit anything. Without `--author` (a local run) ownership is not checked.

MAINTAINERS = frozenset({"jajamoa", "assassin808", "zhenzemo"})

# Active endpoints one GitHub account may register. Every registration is a
# seat the cron calls every week, so an account that could register a hundred
# could make the run spend a hundred timeouts on it. Three is room for a team
# to field variants; more is a conversation with a maintainer.
MAX_ROUTES_PER_LOGIN = 3


def _base_file(base_ref, rel):
    """The JSON at `rel` on the base branch, or None when it does not exist."""
    if not base_ref:
        return None
    proc = subprocess.run(["git", "show", f"{base_ref}:{rel}"], cwd=ROOT,
                          capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def _same_login(a, b):
    return bool(a) and bool(b) and a.strip().lower() == b.strip().lower()


def _active_routes_of(login, base_ref):
    """How many registrations on the base branch already give this account
    an endpoint the cron calls (a route, not revoked)."""
    proc = subprocess.run(["git", "ls-tree", "--name-only", base_ref, "entrants/"],
                          cwd=ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        return 0
    n = 0
    for name in proc.stdout.split():
        doc = _base_file(base_ref, name)
        if doc and doc.get("route") and doc.get("status") != "revoked" \
                and _same_login(doc.get("github"), login):
            n += 1
    return n


def check_entrant_owner(rel, new_doc, author, base_ref):
    if not author or author.lower() in MAINTAINERS:
        return
    old = _base_file(base_ref, rel)
    if old is None:
        # A new registration must name its own author, or nobody could ever
        # edit it again except a maintainer.
        if not _same_login(new_doc.get("github"), author):
            fail(f"{rel}: a new registration must carry \"github\": "
                 f"\"{author}\" (the pull request's author); got "
                 f"{new_doc.get('github')!r}")
        if new_doc.get("route") and \
                _active_routes_of(author, base_ref) >= MAX_ROUTES_PER_LOGIN:
            fail(f"{rel}: @{author} already has {MAX_ROUTES_PER_LOGIN} active "
                 "endpoint registrations; revoke one or ask a maintainer")
        return
    owner = old.get("github")
    if not owner:
        fail(f"{rel}: this registration has no github owner on record; only a "
             "maintainer can change it")
    if not _same_login(owner, author):
        fail(f"{rel}: registered to @{owner}; @{author} may not change it")
    if not _same_login(new_doc.get("github"), owner):
        fail(f"{rel}: the github owner cannot be changed by its own pull "
             "request; ask a maintainer")


def check_forecast_owner(rel, entrant_id, author, base_ref):
    if not author or author.lower() in MAINTAINERS:
        return
    reg = _base_file(base_ref, f"entrants/{entrant_id}.json")
    if reg is None:
        # Registered in the same pull request: the working-tree file is the
        # one CI validated a moment ago, with its own ownership check.
        path = os.path.join(ROOT, "entrants", entrant_id + ".json")
        if os.path.isfile(path):
            with open(path) as f:
                reg = json.load(f)
    if not reg:
        fail(f"{rel}: no registration entrants/{entrant_id}.json; register "
             "first (same pull request is fine)")
    if not _same_login(reg.get("github"), author):
        fail(f"{rel}: forecasts for '{entrant_id}' may only be filed by "
             f"@{reg.get('github') or 'a maintainer'}; the pull request is by "
             f"@{author}")


def answer_blocks(fc):
    """[(label, distribution), ...] -- every distribution in a submission.

    A topline and a profile cell are the same object scored two ways, so every
    rule below applies to both and is written once. The label is what a failure
    message names, so an entrant with one bad cell out of sixteen is told which.
    """
    if isinstance(fc.get("profile"), dict):
        return [(f"profile cell '{k}'", v)
                for k, v in sorted(fc["profile"].items())]
    if "ranking" in fc:
        # A ranking answer holds no distributions at all: it is a list, scored
        # by a metric on lists. Returning a fabricated empty topline here would
        # make every ranking submission fail the shape check below for lacking
        # an sd it was never asked for.
        return []
    return [("topline", fc.get("topline") or {})]


def check_shape(rel, label, t):
    """mean+sd (sd > 0) or quantiles. The no-jsonschema fallback only."""
    if not isinstance(t, dict):
        fail(f"{rel}: {label} is not an object")
    if "quantiles" not in t:
        sd = t.get("sd")
        if isinstance(sd, bool) or not isinstance(sd, (int, float)) or sd <= 0:
            fail(f"{rel}: {label} needs mean+sd (sd > 0) or quantiles")


def check_quantiles(rel, label, t):
    """Semantic rules the JSON schema cannot express: the median is present,
    levels are strictly inside (0, 1), and values do not decrease."""
    q = (t or {}).get("quantiles")
    if not q:
        return
    try:
        items = sorted((float(k), float(v)) for k, v in q.items())
    except (TypeError, ValueError):
        fail(f"{rel}: {label} quantiles must map numeric levels to numbers")
    if not any(abs(l - 0.5) < 1e-9 for l, _ in items):
        fail(f"{rel}: {label} quantiles must include the median ('0.5')")
    if any(l <= 0 or l >= 1 for l, _ in items):
        fail(f"{rel}: {label} quantile levels must be strictly between 0 and 1")
    vals = [v for _, v in items]
    if any(b < a for a, b in zip(vals, vals[1:])):
        fail(f"{rel}: {label} quantile values must be non-decreasing in level")


def wiki_canonical_title(raw):
    """Mirrors ssa.adapters.wikipedia.canonical_title.

    MediaWiki's own two rules: spaces and underscores are one character, and a
    main-namespace title's first letter is stored capitalized. Applied here so
    that a submission is checked for duplicates and exclusions in the same form
    it will be scored in -- `Roblox` and `roblox` are one article, and a
    submission listing both is a nine-item answer wearing ten.
    """
    t = "_".join(str(raw).split()).strip("_")
    return t[0].upper() + t[1:] if t else t


def wiki_is_excluded(title, rule):
    if rule != WIKI_EXCLUSION_RULE_ID:
        fail(f"round names Wikipedia exclusion rule '{rule}', which this "
             f"checkout does not implement (it has "
             f"'{WIKI_EXCLUSION_RULE_ID}'). Update tools/validate_submission.py "
             "and ssa/adapters/wikipedia.py together, or the round cannot be "
             "validated.")
    return title in WIKI_EXCLUDED_TITLES or title.startswith(WIKI_NAMESPACE_PREFIXES)


def check_ranking(rel, fc, rnd):
    """A ranking round's answer, against the round's own frozen `ranking` block.

    The rules come from the round definition rather than from any registry, for
    the reason the profile check gives: what a submission is held to is the
    frozen question, not a list that could move under it after the lock.
    """
    rid = rnd["round_id"]
    if "ranking" not in fc:
        fail(f"{rel}: round '{rid}' is a ranking round and takes a `ranking` "
             "list of its items in predicted order; this submission has "
             f"{'a `profile`' if 'profile' in fc else 'a `topline`'}. A single "
             "number is not an answer to this question.")
    spec = rnd.get("ranking")
    if not isinstance(spec, dict) or not spec.get("length"):
        fail(f"{rel}: ranking round '{rid}' does not name its `ranking.length`; "
             "the round definition is unusable")
    got = fc["ranking"]
    if not isinstance(got, list) or not all(isinstance(x, str) for x in got):
        fail(f"{rel}: `ranking` must be an array of strings")
    n = spec["length"]
    if len(got) != n:
        fail(f"{rel}: round '{rid}' asks for exactly {n} items in order, this "
             f"submission has {len(got)}")

    basket = spec.get("items")
    if basket:
        canon = {q.strip().casefold(): q for q in basket}
        items, unknown = [], []
        for x in got:
            hit = canon.get(x.strip().casefold())
            (items.append(hit) if hit else unknown.append(x))
        if unknown:
            fail(f"{rel}: {', '.join(repr(u) for u in unknown[:3])} "
                 f"{'is' if len(unknown) == 1 else 'are'} not in round "
                 f"'{rid}''s basket ({', '.join(basket)}). This round is a "
                 "permutation of a fixed basket, not a free choice of items.")
    else:
        items = [wiki_canonical_title(x) for x in got]
        rule = spec.get("exclusions") or WIKI_EXCLUSION_RULE_ID
        bad = [x for x in items if wiki_is_excluded(x, rule)]
        if bad:
            fail(f"{rel}: {', '.join(bad[:3])} "
                 f"{'is' if len(bad) == 1 else 'are'} excluded from round "
                 f"'{rid}' by rule {rule} and cannot be ranked")

    dupes = sorted({x for x in items if items.count(x) > 1})
    if dupes:
        fail(f"{rel}: a ranking lists each item once; repeated: "
             f"{', '.join(dupes[:3])}")


def check_answer_matches_round(rel, fc, rnd):
    """The answer is the shape this round asked for. None substitutes.

    A profile round's whole point is that a single number cannot answer it, so
    a scalar submission is not a weaker entry -- it is an answer to a different
    question, and scoring it as if it were an entry would put a forecaster who
    never modelled the population on the same board as one who did. The reverse
    is refused for the mirror reason: a sixteen-cell answer to a topline round
    has no defined score.

    The cell roster comes from the round definition rather than from the series
    registry, so what a submission is checked against is the frozen question --
    not a list that could change under it after the round locked.
    """
    target = rnd.get("target_type")
    if target == RANKING_TARGET_TYPE:
        check_ranking(rel, fc, rnd)
        return
    is_profile = target == PROFILE_TARGET_TYPE
    if "ranking" in fc:
        fail(f"{rel}: round '{rnd['round_id']}' is not a ranking round; this "
             "submission has a `ranking` list")
    if not is_profile:
        if "profile" in fc:
            fail(f"{rel}: round '{rnd['round_id']}' is a scalar round and takes "
                 "a `topline`; this submission has a `profile`")
        return
    if "profile" not in fc:
        fail(f"{rel}: round '{rnd['round_id']}' is a profile round and takes a "
             "`profile` of all its cells; this submission has a `topline`. A "
             "single number is not an answer to this question.")
    wanted = rnd.get("cells")
    if not wanted:
        fail(f"{rel}: profile round '{rnd['round_id']}' does not name its "
             "`cells`; the round definition is unusable")
    got = set(fc["profile"])
    missing, extra = sorted(set(wanted) - got), sorted(got - set(wanted))
    if missing:
        fail(f"{rel}: profile is missing {len(missing)} of {len(wanted)} cells: "
             f"{', '.join(missing[:5])}{' ...' if len(missing) > 5 else ''}. "
             "All cells or none: the energy score is over the whole vector.")
    if extra:
        fail(f"{rel}: profile has {len(extra)} cell(s) this round did not ask "
             f"for: {', '.join(extra[:5])}{' ...' if len(extra) > 5 else ''}")


def validate(path, now=None, author=None, base_ref=None):
    now = now or datetime.now(timezone.utc)
    rel = os.path.relpath(os.path.abspath(path), ROOT)
    parts = rel.split(os.sep)
    if len(parts) == 2 and parts[0] == "entrants":
        validate_entrant(path, author, base_ref)
        return
    if len(parts) != 3 or parts[0] != "forecasts":
        fail(f"{rel}: forecasts live at forecasts/<round_id>/<entrant>.json, registrations at entrants/<entrant_id>.json")
    round_dir, fname = parts[1], parts[2]
    # An example directory is a template, not a submission. Two checks cannot
    # apply to it: `_example` is not a legal round_id, so the directory can
    # never match, and the round it names has already locked, so it is always
    # past its deadline. Every other check applies, because an example is the
    # file a new entrant copies.
    is_example = round_dir.startswith("_")

    with open(path) as f:
        try:
            fc = json.load(f)
        except json.JSONDecodeError as e:
            fail(f"{rel}: not valid JSON: {e}")

    try:
        import jsonschema
        with open(os.path.join(ROOT, "schema", "forecast.schema.json")) as f:
            schema = json.load(f)
        jsonschema.validate(fc, schema)
    except ImportError:
        # minimal fallback when jsonschema is absent
        note_schema_unchecked()
        for key in ("round_id", "entrant"):
            if key not in fc:
                fail(f"{rel}: missing required field '{key}'")
        has = [k for k in ("topline", "profile", "ranking") if k in fc]
        if len(has) != 1:
            fail(f"{rel}: a submission carries exactly one of `topline`, "
                 f"`profile` or `ranking`, found {has or 'neither'}")
        if "profile" in fc and not isinstance(fc["profile"], dict):
            fail(f"{rel}: `profile` must be an object of cells")
        if "ranking" in fc and not isinstance(fc["ranking"], list):
            fail(f"{rel}: `ranking` must be an array of items in order")
        for label, t in answer_blocks(fc):
            check_shape(rel, label, t)
    except Exception as e:
        fail(f"{rel}: schema violation: {e}")

    # semantic checks beyond the JSON schema, applied to every distribution in
    # the submission -- one topline, or each of a profile's cells
    for label, t in answer_blocks(fc):
        check_quantiles(rel, label, t)

    if not is_example and fc["round_id"] != round_dir:
        fail(f"{rel}: round_id '{fc['round_id']}' does not match directory '{round_dir}'")
    if fc["entrant"] + ".json" != fname:
        fail(f"{rel}: entrant '{fc['entrant']}' does not match file name '{fname}'")

    with open(os.path.join(ROOT, "questions", "season0.json")) as f:
        season = json.load(f)
    rounds = {r["round_id"]: r for r in season["rounds"]}
    if fc["round_id"] not in rounds:
        fail(f"{rel}: unknown round '{fc['round_id']}'")
    check_answer_matches_round(rel, fc, rounds[fc["round_id"]])
    if not is_example:
        check_forecast_owner(rel, fc["entrant"], author, base_ref)
        lock_at = datetime.strptime(rounds[fc["round_id"]]["lock_at"],
                                    "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        # The deadline is the batch's, not the round's own lock. Season 0's
        # rounds lock on six different weekdays, so a per-round deadline meant
        # six deadlines to track and, worse, entrants answering the same
        # question from up to seven days apart. `ssa.batches` holds the
        # calendar and the dated cutover; rounds that predate it still
        # validate against their own lock.
        due = effective_deadline(lock_at)
        if now >= due:
            if due < lock_at:
                fail(f"{rel}: batch batch-{due:%Y-%m-%d} closed at "
                     f"{due:%Y-%m-%dT%H:%M:%SZ} (round locks "
                     f"{rounds[fc['round_id']]['lock_at']}), submission is late")
            fail(f"{rel}: round locked at {rounds[fc['round_id']]['lock_at']}, submission is late")

    ok(rel, "example, deadline not checked" if is_example else "")
    print(f"    sha256: {canonical_sha256(fc)}")


def parse_args(argv):
    """Positional files plus three CI options. No argparse: a participant
    reads `--now`, `--author`, `--base` and the file list and nothing else."""
    files, opts = [], {"now": None, "author": None, "base": None}
    it = iter(argv)
    for arg in it:
        if arg.startswith("--") and arg[2:] in opts:
            opts[arg[2:]] = next(it, None)
        elif arg.startswith("--"):
            fail(f"unknown option {arg}; usage: python tools/validate_submission.py "
                 "[--now ISO8601] [--author LOGIN --base REF] <file> [<file> ...]")
        else:
            files.append(arg)
    now = None
    if opts["now"]:
        # The receipt time to judge lateness by (the moment the pull request
        # reached GitHub), when CI runs later than that moment.
        now = datetime.fromisoformat(opts["now"].replace("Z", "+00:00"))
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)
    return files, now, opts["author"], opts["base"]


if __name__ == "__main__":
    files, now, author, base = parse_args(sys.argv[1:])
    if not files:
        fail("usage: python tools/validate_submission.py [--now ISO8601] "
             "[--author LOGIN --base REF] <file> [<file> ...]")
    for p in files:
        validate(p, now=now, author=author, base_ref=base)
