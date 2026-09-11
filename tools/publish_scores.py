#!/usr/bin/env python3
"""Attach per-round scores and the released ranked lists to an existing site/data.json.

refresh.py does this on every run; this does the same for a data.json already on
disk, from the forecasts tree and the round resolutions it carries, so the site can
draw the season question by question between refreshes.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import refresh  # noqa: E402


def main(path=os.path.join(ROOT, "site", "data.json")):
    with open(path) as fh:
        data = json.load(fh)
    rounds = data.get("rounds", [])
    resolved = {r["round_id"]: r["resolution"] for r in rounds
                if r.get("status") == "resolved" and isinstance(r.get("resolution"), dict)
                and "value" in r["resolution"]}
    filed = refresh.file_crowd_forecasts(rounds, refresh.now_utc(), backfill=True)
    refresh.count_forecasts(rounds)
    board = refresh.build_leaderboard(rounds, resolved)
    data["leaderboard"] = {"resolved_rounds": sum(1 for r in rounds if r.get("status") == "resolved"),
                           "entries": board}
    refresh.attach_round_scores(rounds, data.get("profile"), data.get("ranking"))
    data["lists"] = refresh.published_lists()
    data["retired"] = refresh.retirement(rounds, refresh.load_entrants(), refresh.now_utc())
    data["entrant_status"] = refresh.entrant_status.build(rounds, refresh.load_entrants())
    with open(path, "w") as fh:
        json.dump(data, fh, separators=(",", ":"), ensure_ascii=False)
        fh.write("\n")
    scored = sum(1 for r in rounds if r.get("scores"))
    weeks = len((data["lists"] or {}).get("wiki_top10_en", []))
    print(f"{path}: {scored} rounds carry scores, {len(board)} board rows, {weeks} weekly lists, "
          f"{filed} crowd forecasts filed, {len(data['retired'])} entrants retired")


if __name__ == "__main__":
    main(*sys.argv[1:])
