"""Validate the reviewed season (or a candidate manifest) before publication.

  PYTHONPATH=. python3 tools/validate_season.py
  PYTHONPATH=. python3 tools/validate_season.py questions/candidates/batch-X.json

The command is offline and read-only.  Exit status is non-zero for every
problem, so the same gate can be used by a maintainer and by CI.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import season                                             # noqa: E402

DEFAULT = os.path.join(ROOT, "questions", "season0.json")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("path", nargs="?", default=DEFAULT)
    args = ap.parse_args(argv)
    try:
        with open(args.path, encoding="utf-8") as fh:
            document = json.load(fh)
    except (OSError, json.JSONDecodeError) as err:
        print(f"FAIL read: {args.path}: {err}", file=sys.stderr)
        return 1
    problems = season.validate_document(document)
    # A candidate can be coherent by itself and still duplicate a reviewed
    # target under a new id.  Candidate validation therefore runs against the
    # frozen season by default; this is the same merge a human promotion would
    # create, without writing either file.
    if os.path.abspath(args.path) != os.path.abspath(DEFAULT):
        try:
            with open(DEFAULT, encoding="utf-8") as fh:
                reviewed = json.load(fh)
            combined = {"rounds": (season.rounds_from(reviewed)
                                    + season.rounds_from(document))}
            combined_problems = season.validate_document(combined)
            for problem in combined_problems:
                if problem not in problems:
                    problems.append(problem)
        except (OSError, json.JSONDecodeError, ValueError) as err:
            problems.append(f"cannot compare candidate to reviewed season: {err}")
    if problems:
        print(f"FAIL semantic validation: {args.path}", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print("ACTION: fix the reviewed manifest; no bundle was published.",
              file=sys.stderr)
        return 1
    rounds = season.rounds_from(document)
    shapes = {kind: sum(r.get("target_type") == kind for r in rounds)
              for kind in sorted(season.TARGET_TYPES)}
    print(f"OK semantic validation: {args.path}")
    print(f"  {len(rounds)} rounds; "
          + ", ".join(f"{n} {kind}" for kind, n in shapes.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
