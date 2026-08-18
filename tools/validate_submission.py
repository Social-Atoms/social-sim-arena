"""Validate forecast submissions. Used by CI on every pull request and
runnable locally:

  python tools/validate_submission.py forecasts/<round_id>/<entrant>.json

Checks, in order:
1. JSON parses and matches schema/forecast.schema.json.
2. round_id matches the directory, entrant matches the file name.
3. The round exists in questions/season0.json.
4. The answer is the shape the round asked for: a scalar round takes a
   `topline`, a profile round takes a `profile` carrying exactly the cells the
   round names. Neither substitutes for the other.
5. The round is still open (now < lock_at). CI runs this at merge time, so
   the commit that lands after the lock fails loudly.
6. Prints the canonical sha256, which the leaderboard and the paper cite.

Canonical form: JSON with sorted keys and separators (',', ':'), UTF-8.

This file imports nothing from `ssa/`, deliberately: CI runs it on a bare
checkout and it degrades to hand-rolled checks when even jsonschema is absent.
That is why the profile discriminator below is spelled out here rather than
imported from `ssa/profile_round.py`, which owns it.
"""
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Mirrors ssa.profile_round.TARGET_TYPE. A round without this is scalar.
PROFILE_TARGET_TYPE = "profile_energy"


def fail(msg):
    print("FAIL:", msg)
    sys.exit(1)


def canonical_sha256(obj):
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def validate_entrant(path):
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
        for key in ("entrant_id", "name", "type", "method"):
            if key not in e:
                fail(f"{rel}: missing required field '{key}'")
    except Exception as err:
        fail(f"{rel}: schema violation: {err}")
    if e["entrant_id"] + ".json" != os.path.basename(path):
        fail(f"{rel}: entrant_id '{e['entrant_id']}' does not match file name")
    print(f"OK: {rel}")


def answer_blocks(fc):
    """[(label, distribution), ...] -- every distribution in a submission.

    A topline and a profile cell are the same object scored two ways, so every
    rule below applies to both and is written once. The label is what a failure
    message names, so an entrant with one bad cell out of sixteen is told which.
    """
    if isinstance(fc.get("profile"), dict):
        return [(f"profile cell '{k}'", v)
                for k, v in sorted(fc["profile"].items())]
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


def check_answer_matches_round(rel, fc, rnd):
    """The answer is the shape this round asked for. Neither substitutes.

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
    is_profile = rnd.get("target_type") == PROFILE_TARGET_TYPE
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


def validate(path, now=None):
    now = now or datetime.now(timezone.utc)
    rel = os.path.relpath(os.path.abspath(path), ROOT)
    parts = rel.split(os.sep)
    if len(parts) == 2 and parts[0] == "entrants":
        validate_entrant(path)
        return
    if len(parts) != 3 or parts[0] != "forecasts":
        fail(f"{rel}: forecasts live at forecasts/<round_id>/<entrant>.json, registrations at entrants/<entrant_id>.json")
    round_dir, fname = parts[1], parts[2]
    if round_dir.startswith("_"):
        print(f"OK (example dir, skipped lock check): {rel}")
        return

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
        for key in ("round_id", "entrant"):
            if key not in fc:
                fail(f"{rel}: missing required field '{key}'")
        has = [k for k in ("topline", "profile") if k in fc]
        if len(has) != 1:
            fail(f"{rel}: a submission carries exactly one of `topline` or "
                 f"`profile`, found {has or 'neither'}")
        if "profile" in fc and not isinstance(fc["profile"], dict):
            fail(f"{rel}: `profile` must be an object of cells")
        for label, t in answer_blocks(fc):
            check_shape(rel, label, t)
    except Exception as e:
        fail(f"{rel}: schema violation: {e}")

    # semantic checks beyond the JSON schema, applied to every distribution in
    # the submission -- one topline, or each of a profile's cells
    for label, t in answer_blocks(fc):
        check_quantiles(rel, label, t)

    if fc["round_id"] != round_dir:
        fail(f"{rel}: round_id '{fc['round_id']}' does not match directory '{round_dir}'")
    if fc["entrant"] + ".json" != fname:
        fail(f"{rel}: entrant '{fc['entrant']}' does not match file name '{fname}'")

    with open(os.path.join(ROOT, "questions", "season0.json")) as f:
        season = json.load(f)
    rounds = {r["round_id"]: r for r in season["rounds"]}
    if fc["round_id"] not in rounds:
        fail(f"{rel}: unknown round '{fc['round_id']}'")
    check_answer_matches_round(rel, fc, rounds[fc["round_id"]])
    lock_at = datetime.strptime(rounds[fc["round_id"]]["lock_at"],
                                "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    if now >= lock_at:
        fail(f"{rel}: round locked at {rounds[fc['round_id']]['lock_at']}, submission is late")

    print(f"OK: {rel}")
    print(f"    sha256: {canonical_sha256(fc)}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        fail("usage: python tools/validate_submission.py <file> [<file> ...]")
    for p in sys.argv[1:]:
        validate(p)
