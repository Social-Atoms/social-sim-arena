"""Validate forecast submissions. Used by CI on every pull request and
runnable locally:

  python tools/validate_submission.py forecasts/<round_id>/<entrant>.json

Checks, in order:
1. JSON parses and matches schema/forecast.schema.json.
2. round_id matches the directory, entrant matches the file name.
3. The round exists in questions/season0.json.
4. The round is still open (now < lock_at). CI runs this at merge time, so
   the commit that lands after the lock fails loudly.
5. Prints the canonical sha256, which the leaderboard and the paper cite.

Canonical form: JSON with sorted keys and separators (',', ':'), UTF-8.
"""
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
        for key in ("round_id", "entrant", "topline"):
            if key not in fc:
                fail(f"{rel}: missing required field '{key}'")
        sd = fc["topline"].get("sd") if isinstance(fc["topline"], dict) else None
        if isinstance(sd, bool) or not isinstance(sd, (int, float)) or sd <= 0:
            fail(f"{rel}: topline.sd must be a positive number")
    except Exception as e:
        fail(f"{rel}: schema violation: {e}")

    if fc["round_id"] != round_dir:
        fail(f"{rel}: round_id '{fc['round_id']}' does not match directory '{round_dir}'")
    if fc["entrant"] + ".json" != fname:
        fail(f"{rel}: entrant '{fc['entrant']}' does not match file name '{fname}'")

    with open(os.path.join(ROOT, "questions", "season0.json")) as f:
        season = json.load(f)
    rounds = {r["round_id"]: r for r in season["rounds"]}
    if fc["round_id"] not in rounds:
        fail(f"{rel}: unknown round '{fc['round_id']}'")
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
