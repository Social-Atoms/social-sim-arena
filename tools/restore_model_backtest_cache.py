"""Restore committed model-backtest run evidence into the working cache.

The cache is deliberately gitignored because one file per provider call is
useful locally and noisy in a repository.  The equivalent consolidated JSONL
files under backtest/runs/ are committed.  This command recreates the cache so
a fresh clone can resume or rescore without re-billing existing successful
calls.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa import model_backtest  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs-dir", default=model_backtest.RUNS_DIR)
    ap.add_argument("--cache-dir", default=model_backtest.CACHE_DIR)
    ap.add_argument(
        "--overwrite-existing", action="store_true",
        help="replace local cache entries with the latest committed record",
    )
    args = ap.parse_args()

    counts = model_backtest.restore_runs(
        runs_dir=args.runs_dir,
        cache_dir=args.cache_dir,
        overwrite_existing=args.overwrite_existing,
    )
    print(
        "restored model-backtest cache: "
        f"{counts['restored']} written, "
        f"{counts['skipped_existing']} existing preserved; "
        f"{counts['successful']} successful, {counts['failures']} failures; "
        f"{counts['unique']} unique records from "
        f"{counts['lines']} lines in {counts['files']} files"
    )
    if counts["superseded"]:
        print(f"later committed records superseded {counts['superseded']} entries")


if __name__ == "__main__":
    main()
