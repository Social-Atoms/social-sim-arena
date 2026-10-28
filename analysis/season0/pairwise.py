"""Python port of site/pairwise.js so report numbers match the live board."""
from __future__ import annotations

import math

EXTERNAL = [
    {"id": "apodex-futureflow", "name": "Apodex-FutureFlow"},
    {"id": "h2oai-superagent", "name": "H2O.ai SuperAgent"},
    {"id": "vac-combined", "name": "Vac", "members": [
        "vac-public-opinion", "vac-sentiment-expectations",
        "vac-population-profiles", "vac-collective-attention"]},
    {"id": "yulan-onesim", "name": "YuLan-OneSim"},
    {"id": "zhengzheng-agent", "name": "ZhengZheng Forecaster"},
    {"id": "anonymous-combined", "name": "Anonymous", "scope": "all", "duplicate": "priority",
     "members": ["anon-a1", "anon-a2", "anon-a3", "anon-a4", "anon-a5", "anon-a6"]},
]
EXCLUDED = {"test-jay", "just4test", "ssa-testing-team"}
SEASON0_METHODS = [
    "apodex-futureflow", "h2oai-superagent", "h2oai-marina",
    "vac-combined", "worldvac-base", "yulan-onesim", "zhengzheng-agent",
    "claude-sonnet-web", "claude-sonnet-web-superfc",
    "deepseek-pro-web", "deepseek-pro-web-superfc",
    "gemini-pro-web", "gemini-pro-web-superfc",
    "glm-web", "glm-web-superfc",
    "gpt-5.6-luna-web", "gpt-5.6-luna-web-superfc",
    "grok-web", "grok-web-superfc",
    "persistence", "trend", "ewma", "climatology", "crowd",
]
S0 = set(SEASON0_METHODS)


def loss_of(score):
    if not score:
        return None
    keys = [k for k in ("crps", "energy", "loss") if k in score]
    if len(keys) != 1:
        return None
    v = score[keys[0]]
    return v if isinstance(v, (int, float)) and math.isfinite(v) and v >= 0 else None


def roster_of(data, field="mixed", scope="all"):
    registered = {e["entrant_id"]: e for e in data.get("entrants") or []}
    answered = set()
    for r in data.get("rounds") or []:
        if r.get("status") and r["status"] != "resolved":
            continue
        for i, s in (r.get("scores") or {}).items():
            if loss_of(s) is not None:
                answered.add(i)
    retired = data.get("retired") or {}

    def active(i):
        if i in EXCLUDED or retired.get(i):
            return False
        e = registered.get(i)
        return not e or e.get("status") not in ("retired", "revoked")

    def eligible(i):
        return scope == "all" or i in S0

    roster, consumed = [], set()
    for m in EXTERNAL:
        members = m.get("members") or [m["id"]]
        consumed.update(members)
        if not eligible(m["id"]):
            continue
        if m.get("scope") and m["scope"] != scope:
            continue
        if any(i in registered and i in answered and active(i) for i in members):
            if m.get("duplicate") == "priority":
                m = {**m, "members": [i for i in members if i in registered and active(i)]}
            roster.append(m)
    bases = sorted((e for e in registered.values() if e.get("type") == "llm"),
                   key=lambda e: -len(e["entrant_id"]))
    for i in sorted(answered):
        if i in consumed or not active(i) or not eligible(i):
            continue
        e = registered.get(i)
        if e and e.get("type") == "participant":
            roster.append({"id": i, "name": e.get("name") or i})
        elif field == "mixed" and ((e and e.get("type") in ("llm", "baseline")) or
                                   (not e and any(i.startswith(b["entrant_id"] + "-") for b in bases))):
            roster.append({"id": i, **({"name": e.get("name") or i} if e and e.get("type") == "baseline" else {})})
    return roster


def date_of(r):
    return str((r.get("resolution") or {}).get("resolved_at") or r.get("release_at") or r.get("lock_at") or "")


def build(data, field="mixed", scope="all", roster=None, until=None):
    roster = roster or roster_of(data, field, scope)
    since = (data.get("season0_main") or {}).get("since") or "2026-09-30T00:00:00Z"
    acc = {m["id"]: dict(answered=0, points=0.0, top3=0.0, skill=0.0, skillN=0, crps=0.0, crpsN=0)
           for m in roster}
    rounds = sorted(data.get("rounds") or [], key=lambda r: (date_of(r), str(r.get("round_id"))))
    round_ids, trajectory, per_round = [], [], []
    for r in rounds:
        if not r.get("scores") or (r.get("status") and r["status"] != "resolved"):
            continue
        if scope == "s0" and (not r.get("lock_at") or r["lock_at"] < since):
            continue
        if until and date_of(r)[:10] > until:
            continue
        scores = {}
        for m in roster:
            avail = [i for i in (m.get("members") or [m["id"]]) if loss_of(r["scores"].get(i)) is not None]
            if avail:
                scores[m["id"]] = r["scores"][avail[0]]
        if len(scores) < 2:
            continue
        if scope == "matched" and len(scores) != len(roster):
            continue
        round_ids.append(r["round_id"])
        ordered = sorted(((i, s, loss_of(s)) for i, s in scores.items()), key=lambda t: t[2])
        n = len(ordered)
        pts_this = {}
        start = 0
        while start < n:
            end = start + 1
            while end < n and ordered[end][2] == ordered[start][2]:
                end += 1
            tied = end - start
            pts = 100 * (n - end + .5 * (tied - 1)) / (n - 1)
            top3 = max(0, min(tied, min(3, n) - start)) / tied
            for k in range(start, end):
                i, s, _ = ordered[k]
                a = acc[i]
                a["answered"] += 1
                a["points"] += pts
                a["top3"] += top3
                pts_this[i] = pts
                if isinstance(s.get("skill"), (int, float)) and math.isfinite(s["skill"]):
                    a["skill"] += s["skill"]; a["skillN"] += 1
                if isinstance(s.get("crps"), (int, float)):
                    a["crps"] += s["crps"]; a["crpsN"] += 1
            start = end
        per_round.append({"round": r, "points": pts_this, "scores": scores})
        cnt = len(round_ids)
        point = {"date": date_of(r)[:10], "n": cnt,
                 "pairwise": {i: a["points"] / cnt for i, a in acc.items()},
                 "skills": {i: a["skill"] / a["skillN"] for i, a in acc.items() if a["skillN"]}}
        if trajectory and trajectory[-1]["date"] == point["date"]:
            trajectory.pop()
        trajectory.append(point)
    n = len(round_ids)
    entries = []
    for m in roster:
        a = acc[m["id"]]
        entries.append({
            "entrant": m["id"], "name": m.get("name") or m["id"], "members": m.get("members") or [m["id"]],
            "rounds": a["answered"], "total_rounds": n,
            "pairwise": a["points"] / n if n else 0.0, "top3": a["top3"],
            "mean_skill": a["skill"] / a["skillN"] if a["skillN"] else None,
            "mean_crps": a["crps"] / a["crpsN"] if a["crpsN"] else None,
        })
    entries.sort(key=lambda e: (-e["pairwise"], e["entrant"]))
    return {"scope": scope, "since": since, "entries": entries, "trajectory": trajectory,
            "scored_rounds": n, "round_ids": round_ids, "per_round": per_round, "methods": len(roster)}
