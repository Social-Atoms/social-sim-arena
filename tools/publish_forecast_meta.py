#!/usr/bin/env python3
"""Refresh the per-round forecasts and the stamp rows in an existing site/data.json.

refresh.py does this on every run; this does the same for a data.json already on
disk, from the forecasts and stamps trees, so the site can show every filed
forecast with its hash between refreshes.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import refresh, stamps  # noqa: E402


def main(path=os.path.join(ROOT, "site", "data.json")):
    with open(path) as fh:
        data = json.load(fh)
    refresh.count_forecasts(data.get("rounds", []))
    rows = [stamps.status(r["round_id"]) for r in data.get("rounds", [])]
    data["stamps"] = {st["round_id"]: st for st in rows if st.get("manifest")}
    with open(path, "w") as fh:
        json.dump(data, fh, separators=(",", ":"), ensure_ascii=False)
        fh.write("\n")
    n = sum(len(r.get("forecasts") or {}) for r in data.get("rounds", []))
    print(f"{path}: {n} forecasts across {len(data.get('rounds', []))} rounds, {len(data['stamps'])} stamped rounds")


if __name__ == "__main__":
    main(*sys.argv[1:])
