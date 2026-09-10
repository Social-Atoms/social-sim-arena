#!/usr/bin/env python3
"""Write the task registry into an existing site/data.json without a full refresh.

refresh.py publishes data.tasks on every run; this does the same for a data.json
already on disk, so a registry edit reaches the site between refreshes.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import task_registry  # noqa: E402


def main(path=os.path.join(ROOT, "site", "data.json")):
    rows = task_registry.publish_or_raise()
    with open(path) as fh:
        data = json.load(fh)
    data["tasks"] = rows
    with open(path, "w") as fh:
        json.dump(data, fh, separators=(",", ":"), ensure_ascii=False)
        fh.write("\n")
    print(f"{path}: {len(rows)} tasks published")


if __name__ == "__main__":
    main(*sys.argv[1:])
