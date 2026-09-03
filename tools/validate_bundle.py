"""Check a question bundle, or an answer bundle against it, offline.

  python tools/validate_bundle.py BUNDLE.json                   # the questions
  python tools/validate_bundle.py BUNDLE.json RESPONSE.json     # your answers
  python tools/validate_bundle.py BUNDLE.json RESPONSE.json --now 2026-09-13T09:00:00Z

Nothing here reaches the network, and nothing here writes. It exists so a
participant finds out what is wrong on their own machine, in seconds, instead
of finding out from a rejected upload -- and so the answer they get is the same
answer the arena will give, because it runs the same code the arena runs
(`ssa/bundle.py`, which in turn runs `tools/validate_submission.py`). A local
checker that is merely *similar* to the real one is worse than none: it teaches
a participant to trust a verdict that does not bind.

`--now` exists for the same reason the deadline is checked at all. Run without
it, the deadline check uses your clock, which is a preview; the arena uses the
moment your upload arrives, which is the ruling. Pass `--now` to see what a
given arrival time would produce.

Exit status is 0 only when every answer in the payload was accepted, so this is
usable in a participant's own CI.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import bundle as bundle_lib                          # noqa: E402


def read(path):
    with open(path, encoding="utf-8") as fh:
        try:
            return json.load(fh)
        except json.JSONDecodeError as err:
            print(f"FAIL: {path}: not valid JSON: {err}")
            raise SystemExit(1)


def parse_now(text):
    if not text:
        return None
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(
        timezone.utc)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bundle", help="the question bundle you were given")
    ap.add_argument("response", nargs="?", help="your answer bundle")
    ap.add_argument("--now", help="pretend the upload arrives at this UTC time")
    args = ap.parse_args(argv)

    questions = read(args.bundle)
    problems = bundle_lib.check_bundle(questions)
    if problems:
        print(f"FAIL: {args.bundle}: not a valid question bundle")
        for line in problems:
            print("   ", line)
        return 1
    print(f"OK: {args.bundle}")
    print(f"    {questions['batch_id']}: {len(questions['questions'])} questions, "
          f"all due {questions['deadline']}")
    print(f"    sha256: {bundle_lib.sha256_of(questions)}")
    for q in questions["questions"]:
        print(f"      {q['round_id']:<28} {q['target_type']:<18} "
              f"locks {q['lock_at']}  +{q['horizon_days']}d")
    if not args.response:
        return 0

    answers = read(args.response)
    try:
        outcome = bundle_lib.normalise(answers, questions,
                                       now=parse_now(args.now))
    except bundle_lib.BundleError as err:
        print(f"FAIL: {args.response}: [{err.code}] {err}")
        return 1

    print()
    for result in outcome["results"]:
        if result["status"] == "accepted":
            print(f"OK: {result['round_id']}  sha256 {result['sha256']}")
        else:
            print(f"FAIL: {result['round_id']}  [{result['reason']}]")
            for line in result["messages"]:
                print("   ", line)
    if outcome["receipt"]["unanswered"]:
        print(f"\nNot answered ({len(outcome['receipt']['unanswered'])}): "
              + ", ".join(outcome["receipt"]["unanswered"]))
        print("    Unanswered rounds simply score nothing; they are not an error.")
    print("\nreceipt (what an upload of these exact bytes would return):")
    print(json.dumps(outcome["receipt"], indent=2, sort_keys=True))
    return 0 if outcome["receipt"]["rejected"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
