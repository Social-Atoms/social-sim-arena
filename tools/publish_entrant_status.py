#!/usr/bin/env python3
"""Write entrant coverage and call health into an existing site/data.json.

refresh.py publishes data.entrant_status on every run; this does the same for
a data.json already on disk, from the rounds it holds and the replies tree.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import entrant_status  # noqa: E402


def main(path=os.path.join(ROOT, "site", "data.json")):
    with open(path) as fh:
        data = json.load(fh)
    data["entrant_status"] = entrant_status.build(data.get("rounds", []), data.get("entrants", []))
    with open(path, "w") as fh:
        json.dump(data, fh, separators=(",", ":"), ensure_ascii=False)
        fh.write("\n")
    n = len(data["entrant_status"])
    called = sum(1 for v in data["entrant_status"].values() if v["last_call_at"])
    print(f"{path}: entrant_status for {n} entrants, {called} with a recorded call")


if __name__ == "__main__":
    main(*sys.argv[1:])
