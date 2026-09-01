"""Route B intake: turn an uploaded answer bundle into scored-form forecasts.

  python tools/accept_bundle.py RESPONSE.json --bundle BUNDLE.json
  python tools/accept_bundle.py RESPONSE.json --bundle BUNDLE.json --write
  python tools/accept_bundle.py RESPONSE.json --bundle BUNDLE.json --sandbox --out /tmp/try

Dry by default. Nothing is written unless `--write` is passed, because the
common case for this command is a maintainer or a participant asking *what
would happen*, and a tool whose read-only-looking invocation files a whole
batch is a tool that files forecasts by accident.

What it produces is not a new record format. Each accepted answer becomes
exactly one `forecasts/<round_id>/<entrant_id>.json` object matching
`schema/forecast.schema.json` -- the same file a pull request would add and the
same file the scorer already reads. Route B is a different way to hand over a
forecast, not a different kind of forecast, so nothing downstream of the intake
learns that bundles exist.

`--sandbox` is the non-scored rehearsal. It requires `--out` to be somewhere
other than `forecasts/` and stamps `scored: false` on the receipt, so a new
team can run the whole path end to end -- schema, deadline, normalisation,
receipt, per-round verdicts -- without a single byte landing where the
leaderboard reads. A rehearsal that could accidentally enter the season is not
a rehearsal.

The receipt is printed, never stored here: this command is the normalisation
step, and where a receipt is persisted is the hosting question that
`docs/bundle-submission.md` covers. It carries the server's clock and both
hashes; see `ssa/bundle.py` for why those three fields and not a client
timestamp.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import bundle as bundle_lib                          # noqa: E402
from tools.validate_bundle import parse_now, read             # noqa: E402


def _show(path):
    """Repo-relative when it is in the repo, absolute when it is not.

    A sandbox writes outside the checkout, and `os.path.relpath` renders that
    as a stack of `../`, which reads as though something escaped the tree.
    """
    inside = os.path.abspath(path).startswith(ROOT + os.sep)
    return os.path.relpath(path, ROOT) if inside else os.path.abspath(path)


def load_entrant(entrant_id):
    """The registration, if this checkout has one.

    Absent is not an error here: a sandbox rehearsal happens before
    registration is accepted, which is the point of doing it first. It is the
    real intake that must refuse an unregistered or revoked entrant, and it
    does -- `--require-registration` is how this command says so out loud.
    """
    path = os.path.join(ROOT, "entrants", entrant_id + ".json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("response", help="the uploaded answer bundle")
    ap.add_argument("--bundle", required=True,
                    help="the question bundle it answers")
    ap.add_argument("--now", help="server receipt time (default: now, UTC)")
    ap.add_argument("--write", action="store_true",
                    help="write the accepted records (default: dry run)")
    ap.add_argument("--out", help="where to write (default: forecasts/)")
    ap.add_argument("--sandbox", action="store_true",
                    help="non-scored rehearsal; refuses to write into forecasts/")
    ap.add_argument("--require-registration", action="store_true",
                    help="refuse an entrant with no entrants/<id>.json")
    args = ap.parse_args(argv)

    questions = read(args.bundle)
    problems = bundle_lib.check_bundle(questions)
    if problems:
        print(f"FAIL: {args.bundle}: not a valid question bundle", file=sys.stderr)
        for line in problems:
            print("   ", line, file=sys.stderr)
        return 1

    answers = read(args.response)
    entrant = None
    if isinstance(answers, dict) and isinstance(answers.get("entrant_id"), str):
        entrant = load_entrant(answers["entrant_id"])
        if entrant is None and args.require_registration:
            print(f"FAIL: [not_registered] no entrants/{answers['entrant_id']}"
                  ".json in this checkout", file=sys.stderr)
            return 1
    try:
        outcome = bundle_lib.normalise(answers, questions,
                                       now=parse_now(args.now), entrant=entrant)
    except bundle_lib.BundleError as err:
        print(f"FAIL: [{err.code}] {err}", file=sys.stderr)
        return 1

    receipt = dict(outcome["receipt"])
    receipt["scored"] = not args.sandbox
    for result in outcome["results"]:
        if result["status"] == "accepted":
            print(f"accepted  {result['round_id']:<28} sha256 {result['sha256']}")
        else:
            print(f"rejected  {result['round_id']:<28} [{result['reason']}]")
            for line in result["messages"]:
                print("    ", line)

    out_dir = args.out or os.path.join(ROOT, "forecasts")
    if args.sandbox:
        if os.path.abspath(out_dir) == os.path.join(ROOT, "forecasts"):
            print("FAIL: --sandbox needs --out somewhere other than forecasts/; "
                  "a rehearsal that can enter the season is not a rehearsal",
                  file=sys.stderr)
            return 1
    if args.write and outcome["records"]:
        for path, state in bundle_lib.file_records(outcome["records"], out_dir):
            print(f"{state:<10} {_show(path)}")
    elif args.write:
        print("nothing accepted, nothing written")
    else:
        print(f"dry run: {len(outcome['records'])} record(s) would be written "
              f"under {os.path.relpath(out_dir, ROOT)}; pass --write to file them")

    print("\nreceipt:")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["rejected"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
