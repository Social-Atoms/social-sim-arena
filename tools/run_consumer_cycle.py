"""Run admitted consumer rounds through the arena's production scorers.

Collection uses official APIs or the manual import command in consumer_data.py.
Run this on the machine holding SSA_CONSUMER_ARCHIVE; all cycle output is private.
This does not call models, schedule rounds, push data, or change source rights.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ssa import consumer, consumer_round, ranking_round, refresh, resolve, season


def cycle(document, now):
    problems = season.validate_document(document)
    if problems:
        raise ValueError("Season admission failed:\n" + "\n".join(problems))
    selected = {"rounds": [r for r in season.rounds_from(document) if "consumer" in r]}
    observations = {r["round_id"]: consumer_round.ranking_observations(r, now=now)
                    for r in selected["rounds"] if ranking_round.is_ranking(r)}
    rounds, _ = refresh.build_rounds(selected, {}, {}, now, observations)
    saved = consumer.DEFAULT_ARCHIVE / "resolutions.json"
    existing = json.loads(saved.read_text()) if saved.exists() else {}
    new, skipped = resolve.resolve_all(selected, {}, existing, now)
    for definition in selected["rounds"]:
        rid = definition["round_id"]
        if not ranking_round.is_ranking(definition) or rid in existing:
            continue
        if now < consumer_round.timestamp(definition["release_at"]):
            continue
        try:
            new[rid] = consumer_round.resolution(definition, now)
        except ValueError as error:
            skipped.append((rid, str(error)))
    resolved = {**new, **existing}
    consumer.write_json(saved, resolved)
    scalar_board = refresh.build_leaderboard(rounds, resolved)
    ranking_board = refresh.build_ranking_leaderboard(rounds, resolved, observations)
    refresh.attach_round_scores(rounds, {}, ranking_board)
    public = []
    for row in rounds:
        proof = resolved.get(row["round_id"], {})
        if proof:
            row["resolution"] = proof
            row["status"] = "resolved"
        public.append({"round_id": row["round_id"], "domain": row["domain"],
                       "attribution": row["attribution"], "scores": row.get("scores", {}),
                       "evidence": {k: proof[k] for k in (
                           "raw_sha256", "manifest_sha256", "previous_raw_sha256",
                           "source_url", "captured_at") if k in proof}})
    return {"rounds": rounds, "scalar": scalar_board, "ranking": ranking_board,
            "main": refresh.build_main_board(rounds),
            "public_results": public,
            "skipped": [(rid, why) for rid, why in skipped if not resolve.handled_elsewhere(why)]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=Path, default=ROOT / "questions/season0.json")
    parser.add_argument("--archive", type=Path, default=consumer.DEFAULT_ARCHIVE)
    parser.add_argument("--forecasts", type=Path, default=ROOT / "forecasts")
    args = parser.parse_args(argv)
    archive = args.archive.resolve()
    if archive.is_relative_to(ROOT) and not archive.is_relative_to(ROOT / ".local"):
        raise ValueError("Consumer archives inside the repository must be under ignored .local")
    consumer.DEFAULT_ARCHIVE = archive
    refresh.FORECASTS = str(args.forecasts)
    result = cycle(json.loads(args.season.read_text()), datetime.now(timezone.utc))
    consumer.write_json(archive / "cycle.json", result)
    print(f"Consumer cycle: {len(result['rounds'])} rounds; {len(result['skipped'])} awaiting evidence")
    return 1 if result["skipped"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
