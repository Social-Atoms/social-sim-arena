"""Emit the reviewed weekly bundle for the next batch deadline.

  python tools/emit_bundle.py                       # print it, change nothing
  python tools/emit_bundle.py --write               # freeze it under questions/bundles/
  python tools/emit_bundle.py --batch batch-2026-09-21
  python tools/emit_bundle.py --now 2026-09-01T00:00:00Z

One command, one bundle, from a clean checkout and with no network: everything
it needs is `questions/season0.json` plus the calendar in `ssa/batches.py`.
That matters more than it sounds. The bundle is what a participant answers, so
if producing it needed a live fetch then a source being down on a Monday would
mean nobody could be given a question that week -- and the rounds themselves do
not depend on that fetch at all.

**It reads only reviewed rounds.** `tools/generate_rounds.py` writes proposals
into `questions/candidates/`; a human moves the accepted ones into the season
file; this reads the season file. Nothing here promotes a candidate, and
nothing here writes into `questions/season0.json`.

**The digest is the freeze.** `--write` prints the canonical sha256 of the
payload and writes the same bytes it hashed. Re-running on an unchanged season
file reproduces the digest exactly, so "was this bundle changed after it was
published" is a question anyone can answer from the repository.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import batches, bundle                             # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", default=None,
                    help="batch id, e.g. batch-2026-09-21 (default: the next one)")
    ap.add_argument("--write", action="store_true",
                    help="write questions/bundles/<batch_id>.json")
    ap.add_argument("--now", default=None, help="override the clock, for tests")
    args = ap.parse_args()

    now = (datetime.fromisoformat(args.now.replace("Z", "+00:00"))
           if args.now else datetime.now(timezone.utc))

    if args.batch:
        try:
            deadline = datetime.strptime(
                args.batch, "batch-%Y-%m-%d").replace(
                    hour=batches.BATCH_HOUR_UTC, tzinfo=timezone.utc)
        except ValueError:
            ap.error(f"--batch must look like batch-YYYY-MM-DD, got {args.batch!r}")
        if deadline.weekday() != batches.BATCH_WEEKDAY:
            ap.error(f"{args.batch} is not a batch deadline; deadlines fall on "
                     "Mondays 12:00Z")
        # Refused rather than emitted, because the rounds in such a week were
        # bought and scored under the per-round lock rule. A bundle for them
        # would tell a participant one common deadline for rounds that never
        # had one, and `batches.governed_by_batch` would disagree with the
        # payload they were handed.
        if deadline < batches.FIRST_DEADLINE:
            ap.error(
                f"{args.batch} predates the batch cutover "
                f"({batches.FIRST_DEADLINE:%Y-%m-%dT%H:%M:%SZ}); those rounds "
                "keep the per-round lock rule and have no common deadline")
    else:
        deadline = bundle.next_deadline(now)

    b = bundle.build(bundle.load_season(ROOT), deadline)
    qs = b["questions"]

    shapes = {}
    for q in qs:
        shapes[q["target_type"]] = shapes.get(q["target_type"], 0) + 1
    print(f"{b['batch_id']}  deadline {b['deadline']}  "
          f"published {b['published_at']}")
    print(f"{len(qs)} questions: "
          + (", ".join(f"{n} {k}" for k, n in sorted(shapes.items()))
             or "none")
          + f"\nsha256 {bundle.digest(b)}\n")
    for q in qs:
        extra = ""
        if q["cells"]:
            extra = f"  {len(q['cells'])} cells"
        elif q["items"]:
            extra = f"  {len(q['items'])} items"
        print(f"   {q['round_id']:<34} {q['target_type']:<18} "
              f"h={q['horizon_days']:.1f}d{extra}")

    if not qs:
        # An empty bundle is a real operational state, not a crash: the season
        # file simply has no round locking in that week yet. Say which batch
        # was empty rather than writing a file that looks like a published
        # week with nothing in it.
        print("\nNo reviewed round falls in this batch. Generate candidates "
              "with tools/generate_rounds.py and promote the ones you accept.")

    if args.write:
        out_dir = os.path.join(ROOT, "questions", "bundles")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"{b['batch_id']}.json")
        with open(path, "w") as fh:
            json.dump(b, fh, indent=2, sort_keys=True)
            fh.write("\n")
        print(f"\nwrote {os.path.relpath(path, ROOT)}")


if __name__ == "__main__":
    main()
