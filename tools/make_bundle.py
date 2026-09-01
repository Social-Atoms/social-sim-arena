"""Write out the question bundle for one weekly batch. Reads the season, never writes it.

  python tools/make_bundle.py                          # the next open batch
  python tools/make_bundle.py --batch batch-2026-09-14
  python tools/make_bundle.py --list                   # which batches exist
  python tools/make_bundle.py --batch batch-2026-09-14 --out /tmp/bundle.json

The bundle is a projection of `questions/season0.json`, not a second copy of
it: nothing here can add, edit or reorder a round, and `--out` refuses to
write anywhere inside `questions/`. The frozen season file stays the one place
a round comes into existence, which is what makes it worth trusting.

A batch that predates the cutover in `ssa/batches.py` is refused rather than
bundled. Those rounds each carried their own deadline, so there is no single
moment to put in the `deadline` field, and a bundle whose header states a due
date the validator does not enforce is a promise the arena cannot keep.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import batches                                        # noqa: E402
from ssa import bundle as bundle_lib                           # noqa: E402
from ssa import season as season_lib                           # noqa: E402

SEASON = os.path.join(ROOT, "questions", "season0.json")


def load_rounds(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return season_lib.require_valid(data)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rounds", default=SEASON,
                    help="round definitions to read (default: the frozen season)")
    ap.add_argument("--batch", help="batch id, e.g. batch-2026-09-14")
    ap.add_argument("--list", action="store_true",
                    help="list the batches in the round file and stop")
    ap.add_argument("--out", help="write here instead of stdout")
    args = ap.parse_args(argv)

    try:
        rounds = load_rounds(args.rounds)
    except (OSError, json.JSONDecodeError, ValueError) as err:
        print(f"FAIL: reviewed round manifest is not publishable: {err}",
              file=sys.stderr)
        return 1
    print(f"OK reviewed manifest: {len(rounds)} semantically valid rounds",
          file=sys.stderr)
    if args.list:
        for batch_id in bundle_lib.batch_ids(rounds):
            members = [r for r in rounds
                       if batches.batch_of(r["lock_at"]) == batch_id]
            shapes = {}
            for r in members:
                key = r.get("target_type") or "continuous_normal"
                shapes[key] = shapes.get(key, 0) + 1
            rule = ("batch deadline"
                    if batches.governed_by_batch(members[0]["lock_at"])
                    else "pre-cutover, per-round lock")
            print(f"{batch_id}  {len(members):>3} rounds  "
                  f"{', '.join(f'{v} {k}' for k, v in sorted(shapes.items()))}"
                  f"  ({rule})")
        return 0

    try:
        out = bundle_lib.build_bundle(rounds, args.batch)
        repeated = bundle_lib.build_bundle(rounds, args.batch)
    except bundle_lib.BundleError as err:
        print(f"FAIL: [{err.code}] {err}", file=sys.stderr)
        return 1
    if bundle_lib.canonical(out) != bundle_lib.canonical(repeated):
        print("FAIL: building the same reviewed batch twice produced different "
              "bytes", file=sys.stderr)
        return 1
    problems = bundle_lib.check_bundle(out)
    if problems:
        print("FAIL: the generated bundle does not satisfy its own schema:",
              file=sys.stderr)
        for line in problems:
            print("   ", line, file=sys.stderr)
        return 1
    print(f"OK deterministic bundle: {out['batch_id']} "
          f"sha256 {bundle_lib.sha256_of(out)}", file=sys.stderr)

    text = json.dumps(out, indent=2, sort_keys=True) + "\n"
    if not args.out:
        sys.stdout.write(text)
        return 0
    target = os.path.abspath(args.out)
    if target.startswith(os.path.join(ROOT, "questions") + os.sep):
        print("FAIL: questions/ holds the frozen season and is written by a "
              "human, never by this tool", file=sys.stderr)
        return 1
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"wrote {args.out}: {out['batch_id']}, "
          f"{len(out['questions'])} questions, due {out['deadline']}")
    print(f"sha256: {bundle_lib.sha256_of(out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
