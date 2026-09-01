"""Run one generated scalar/profile/ranking batch through the #47 CLI route.

  PYTHONPATH=. python3 tools/run_sandbox_cycle.py
  PYTHONPATH=. python3 tools/run_sandbox_cycle.py --json

The source round definitions, source outcomes and example entrant are committed
offline fixtures.  The question bundle is generated from the round definitions
and must match the committed bundle byte for byte.  That exact generated file
then passes through ``tools/accept_bundle.py --sandbox``; the records it writes
are resolved, scored with the production shape scorers, and reported through
the production round-status function.  Every write is in a temporary directory.
"""
import argparse
import importlib.util
import json
import math
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import bundle, profile_round, ranking_round, refresh, resolve, scoring  # noqa: E402

EXAMPLE = os.path.join(ROOT, "examples", "bundle")
ROUNDS = os.path.join(EXAMPLE, "sandbox-rounds.json")
BUNDLE = os.path.join(EXAMPLE, "sandbox-batch.json")
ANCHORS = os.path.join(EXAMPLE, "sandbox-anchors.json")
SOURCE_OBSERVATIONS = os.path.join(EXAMPLE, "sandbox-source-observations.json")
EXPECTED_RESPONSE = os.path.join(EXAMPLE, "sandbox-response.json")
RECEIVED_AT = "2028-01-03T11:00:00Z"


class StageError(RuntimeError):
    def __init__(self, stage, message, action):
        super().__init__(message)
        self.stage = stage
        self.action = action


def read(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def pretty_bytes(document):
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def generated_bundle(rounds):
    first = bundle.build_bundle(rounds, "batch-2028-01-03")
    second = bundle.build_bundle(rounds, "batch-2028-01-03")
    if bundle.canonical(first) != bundle.canonical(second):
        raise StageError("bundle", "two builds differ",
                         "inspect non-deterministic bundle fields")
    problems = bundle.check_bundle(first)
    if problems:
        raise StageError("bundle", "; ".join(problems),
                         "fix the generated question contract")
    with open(BUNDLE, "rb") as fh:
        committed_bytes = fh.read()
    if pretty_bytes(first) != committed_bytes:
        raise StageError("bundle", "committed sandbox-batch.json is stale",
                         "review the round change, then regenerate the fixture")
    return first


def response_for(question_bundle, anchors):
    spec = importlib.util.spec_from_file_location(
        "_sandbox_entrant", os.path.join(EXAMPLE, "entrant.py"))
    entrant = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entrant)
    response = entrant.build_response(
        question_bundle, "demo_bundle_entrant", anchors, False, False)
    with open(EXPECTED_RESPONSE, "rb") as fh:
        committed_bytes = fh.read()
    if pretty_bytes(response) != committed_bytes:
        raise StageError("response", "sandbox-response.json is stale",
                         "regenerate it with examples/bundle/entrant.py")
    return response


def production_resolution(round_data, series, ranking_obs, now):
    """Resolve through the same functions the live refresh calls."""
    target = round_data["target_type"]
    if target == "continuous_normal":
        result, reason = resolve.resolve_round(round_data, series, now)
        if result is None:
            raise ValueError(reason)
        return result
    if target == "profile_energy":
        return profile_round.resolution(
            round_data, series, cells=list(round_data["cells"]))
    spec = ranking_round.spec_for(round_data)
    return ranking_round.resolution(
        round_data, obs=ranking_obs[round_data["round_id"]], spec=spec)


def score_record(round_data, record, resolution):
    target = round_data["target_type"]
    if target == "continuous_normal":
        return {"crps": scoring.crps_forecast(record["topline"],
                                               resolution["value"])}
    if target == "profile_energy":
        cells = list(round_data["cells"])
        outcome = profile_round.outcome_vector(resolution, cells)
        return profile_round.score_submission(record, outcome, cells)
    spec = ranking_round.spec_for(round_data)
    outcome = ranking_round.outcome_items(resolution, spec)
    return ranking_round.score_submission(record, outcome, spec)


def run_cycle():
    source_document = read(ROUNDS)
    rounds = source_document["rounds"]
    question_bundle = generated_bundle(rounds)
    anchors = read(ANCHORS)
    response = response_for(question_bundle, anchors)
    source_artifact = read(SOURCE_OBSERVATIONS)
    source_series = source_artifact.get("series")
    ranking_obs = source_artifact.get("ranking")
    if not isinstance(source_series, dict) or not isinstance(ranking_obs, dict):
        raise StageError(
            "resolution", "sandbox source artifact has the wrong envelope",
            "restore its adapter-shaped `series` and `ranking` maps")

    with tempfile.TemporaryDirectory(prefix="ssa-sandbox-cycle-") as scratch:
        bundle_path = os.path.join(scratch, "bundle.json")
        response_path = os.path.join(scratch, "response.json")
        records_dir = os.path.join(scratch, "scored-form-records")
        input_bytes = {}
        for path, document in ((bundle_path, question_bundle),
                               (response_path, response)):
            input_bytes[path] = pretty_bytes(document)
            with open(path, "wb") as fh:
                fh.write(input_bytes[path])
        command = [
            sys.executable, os.path.join(ROOT, "tools", "accept_bundle.py"),
            response_path, "--bundle", bundle_path, "--now", RECEIVED_AT,
            "--sandbox", "--write", "--out", records_dir,
        ]
        accepted = subprocess.run(command, cwd=ROOT, text=True,
                                  capture_output=True)
        if accepted.returncode:
            evidence = (accepted.stdout + "\n" + accepted.stderr).strip()
            raise StageError("#47-cli-intake", evidence,
                             "fix the rejected answer named by the CLI receipt")
        for path, before in input_bytes.items():
            with open(path, "rb") as fh:
                if fh.read() != before:
                    raise StageError(
                        "#47-cli-intake", f"CLI changed its input {path}",
                        "keep question and response inputs immutable")

        resolved = {}
        scores = {}
        statuses = {}
        answer_by_round = {answer["round_id"]: answer
                           for answer in response["answers"]}
        after_release = datetime(2028, 1, 18, tzinfo=timezone.utc)
        for round_data in rounds:
            rid = round_data["round_id"]
            path = os.path.join(records_dir, rid, "demo_bundle_entrant.json")
            if not os.path.exists(path):
                raise StageError("#47-cli-intake", f"missing {path}",
                                 "inspect the per-round CLI verdict")
            record = read(path)
            answer = answer_by_round[rid]
            answer_key = bundle.ANSWER_KEY[round_data["target_type"]]
            if record.get(answer_key) != answer.get(answer_key):
                raise StageError(
                    "#47-cli-intake",
                    f"{rid}: scored-form record changed the accepted forecast",
                    "fix intake normalization; forecast content must be preserved")
            resolved[rid] = production_resolution(
                round_data, source_series, ranking_obs, after_release)
            scores[rid] = score_record(round_data, record, resolved[rid])
            if not all(math.isfinite(float(value)) for value in scores[rid].values()
                       if isinstance(value, (int, float))):
                raise StageError("scoring", f"{rid} produced a non-finite score",
                                 "inspect the shape-specific scorer")
            statuses[rid] = refresh.round_status(round_data, resolved, after_release)
            if statuses[rid] != "resolved":
                raise StageError("status", f"{rid}: {statuses[rid]}",
                                 "inspect resolution identity and release time")

    return {
        "bundle": {"state": "ok", "batch_id": question_bundle["batch_id"],
                   "sha256": bundle.sha256_of(question_bundle),
                   "questions": len(question_bundle["questions"]),
                   "shapes": sorted(q["target_type"]
                                    for q in question_bundle["questions"])},
        "intake": {"state": "ok", "route": "tools/accept_bundle.py --sandbox",
                   "accepted": len(rounds), "rejected": 0,
                   "forecast_blocks_preserved": len(rounds)},
        "resolution": {
            "state": "ok", "resolved": len(resolved),
            "methods": {rid: detail["method"] for rid, detail in resolved.items()},
        },
        "scoring": {"state": "ok", "records": scores},
        "status": {"state": "ok", "rounds": statuses},
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        summary = run_cycle()
    except StageError as err:
        print(f"FAIL {err.stage}: {err}", file=sys.stderr)
        print(f"ACTION: {err.action}", file=sys.stderr)
        return 1
    except Exception as err:  # a named operator failure, never a bare traceback
        print(f"FAIL sandbox-cycle: {type(err).__name__}: {err}", file=sys.stderr)
        print("ACTION: inspect the last successful stage and its committed "
              "fixture; rerun with no network or credentials required.",
              file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print("OK bundle: generated twice and matches committed bytes")
        print("OK #47 CLI intake: 3 accepted, 0 rejected")
        print("OK resolution: scalar/profile/ranking source artifacts resolved")
        print("OK scoring: 3 scored-form records reached production scorers")
        print("OK status: 3 resolved")
        print(f"bundle sha256: {summary['bundle']['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
