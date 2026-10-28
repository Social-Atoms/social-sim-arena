#!/usr/bin/env python3
"""Season 0 report numbers.

    python report.py [data.json] [models.json] [out.json]

Reads the site's data.json (main carries the freshest copy) and the
models.json snapshot from fetch_models.py, recomputes the leaderboard with
the port of site/pairwise.js, and writes the frozen file site/blog.html
renders. Prints a summary.
"""
from __future__ import annotations

import json
import random
import statistics
import sys
from collections import defaultdict
from datetime import date, timedelta
from itertools import combinations
from pathlib import Path

from pairwise import EXTERNAL, build, date_of, loss_of, roster_of

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
PRESEASON = ("2026-08-28", "2026-09-29")


def rnd(x, k=2):
    return None if x is None else round(x, k)


def kind_of(i, ent):
    if any(m["id"] == i for m in EXTERNAL):
        return "agent"
    t = (ent.get(i) or {}).get("type")
    if t == "participant":
        return "agent"
    if t == "baseline":
        return "baseline"
    return "model"


def family_of(i):
    for suf in ("-web-superfc", "-web"):
        if i.endswith(suf):
            return i[: -len(suf)]
    return i


def harness_of(i):
    return "Web + superforecaster" if i.endswith("-web-superfc") else "Web" if i.endswith("-web") else None


def ranks(values: dict) -> dict:
    order = sorted(values, key=lambda k: -values[k])
    out, i = {}, 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in order[i: j + 1]:
            out[k] = (i + j) / 2 + 1
        i = j + 1
    return out


def spearman(a: dict, b: dict):
    keys = [k for k in a if k in b and a[k] is not None and b[k] is not None]
    if len(keys) < 3:
        return None, len(keys)
    ra, rb = ranks({k: a[k] for k in keys}), ranks({k: b[k] for k in keys})
    ma, mb = statistics.mean(ra.values()), statistics.mean(rb.values())
    num = sum((ra[k] - ma) * (rb[k] - mb) for k in keys)
    den = (sum((ra[k] - ma) ** 2 for k in keys) * sum((rb[k] - mb) ** 2 for k in keys)) ** 0.5
    return (num / den if den else None), len(keys)


def name_of(i, ent, models):
    e = ent.get(i) or {}
    ext = next((m for m in EXTERNAL if m["id"] == i), None)
    if ext:
        return ext["name"]
    if e.get("type") == "baseline":
        return (e.get("name") or i).split(" (")[0]
    if e.get("type") == "participant":
        return e.get("name") or i
    fam = family_of(i)
    base = (models.get(fam) or {}).get("name") or fam
    return base + (" · SF" if i.endswith("-superfc") else "")


def board_rows(board, ent, models):
    rows = []
    for e in board["entries"]:
        i = e["entrant"]
        rows.append({
            "id": i, "name": name_of(i, ent, models), "kind": kind_of(i, ent),
            "family": family_of(i) if kind_of(i, ent) == "model" else None,
            "harness": harness_of(i), "pairwise": rnd(e["pairwise"]),
            "skill": rnd(None if e["mean_skill"] is None else 100 * e["mean_skill"], 1),
            "answered": e["rounds"], "total": e["total_rounds"],
        })
    return rows


def trajectory_rows(board):
    ids = [e["entrant"] for e in board["entries"]]
    return {"dates": [p["date"] for p in board["trajectory"]],
            "n": [p["n"] for p in board["trajectory"]],
            "pairwise": {i: [rnd(p["pairwise"].get(i), 1) for p in board["trajectory"]] for i in ids},
            "skill": {i: [rnd(None if p["skills"].get(i) is None else 100 * p["skills"][i], 1)
                          for p in board["trajectory"]] for i in ids}}


def model_section(data, ent, models, s0):
    """Family scores in two windows, joined with AA, OpenRouter and cutoffs."""
    fam_s0 = defaultdict(list)
    for e in s0["entries"]:
        if kind_of(e["entrant"], ent) == "model":
            fam_s0[family_of(e["entrant"])].append(e)
    s0_score = {f: statistics.mean(x["pairwise"] for x in v) for f, v in fam_s0.items()}
    s0_skill = {f: statistics.mean(100 * x["mean_skill"] for x in v) for f, v in fam_s0.items()}

    roster = [{"id": f + suf} for f in models for suf in ("-web", "-web-superfc")]
    seen = {i for r in data["rounds"] for i in (r.get("scores") or {})}
    roster = [m for m in roster if m["id"] in seen]
    pre_rounds = [r for r in data["rounds"] if PRESEASON[0] <= date_of(r)[:10] <= PRESEASON[1]]
    pre = build({**data, "rounds": pre_rounds}, roster=roster)
    fam_pre = defaultdict(list)
    for e in pre["entries"]:
        fam_pre[family_of(e["entrant"])].append(e["pairwise"])
    pre_score = {f: statistics.mean(v) for f, v in fam_pre.items()}

    def cutoff_ok(m):
        return m.get("cutoff") and m.get("release") and m["cutoff"] < m["release"]

    def days(d):
        return (date.fromisoformat(d) - date(2025, 1, 1)).days if d else None

    feats = {
        "aa_intelligence": {f: m.get("aa_intelligence") for f, m in models.items()},
        "or_tokens_7d": {f: m.get("or_tokens_7d") for f, m in models.items()},
        "cutoff": {f: days(m["cutoff"]) if cutoff_ok(m) else None for f, m in models.items()},
        "release": {f: days(m.get("release")) for f, m in models.items()},
    }
    corr = {}
    for win, sc in (("s0", s0_score), ("preseason", pre_score)):
        corr[win] = {}
        for k, v in feats.items():
            rho, n = spearman(sc, v)
            corr[win][k] = {"rho": rnd(rho), "n": n}

    table = []
    for f, m in models.items():
        if f not in pre_score and f not in s0_score:
            continue
        table.append({"family": f, "name": m["name"], "s0_pairwise": rnd(s0_score.get(f)),
                      "s0_skill": rnd(s0_skill.get(f), 1), "pre_pairwise": rnd(pre_score.get(f)),
                      "aa_intelligence": m.get("aa_intelligence"), "or_tokens_7d": m.get("or_tokens_7d"),
                      "cutoff": m.get("cutoff"), "cutoff_source": m.get("cutoff_source"),
                      "cutoff_valid": bool(cutoff_ok(m)), "release": m.get("release"),
                      "active": f in s0_score})
    table.sort(key=lambda r: -(r["s0_pairwise"] if r["s0_pairwise"] is not None else -1))

    harness = []
    for f, v in fam_s0.items():
        d = {("sfc" if x["entrant"].endswith("-superfc") else "web"): x for x in v}
        if len(d) == 2:
            harness.append({"family": f, "web": rnd(d["web"]["pairwise"]), "sfc": rnd(d["sfc"]["pairwise"]),
                            "web_skill": rnd(100 * d["web"]["mean_skill"], 1),
                            "sfc_skill": rnd(100 * d["sfc"]["mean_skill"], 1)})
    return {"table": table, "corr": corr, "harness": harness,
            "preseason": {"from": PRESEASON[0], "to": PRESEASON[1], "questions": pre["scored_rounds"],
                          "methods": pre["methods"]},
            "same_family": same_family_pairs(models, pre_score)}


def same_family_pairs(models, score):
    """Pairs from one vendor with different valid cutoffs: does the fresher one score higher?
    Vendors mix tiers today (Sonnet vs Opus), so read this as a template for later seasons."""
    out = []
    by_vendor = defaultdict(list)
    for f, m in models.items():
        if f in score and m.get("cutoff") and m.get("release") and m["cutoff"] < m["release"]:
            by_vendor[m["or_id"].split("/")[0]].append(f)
    for vendor, fams in by_vendor.items():
        for a, b in combinations(sorted(fams), 2):
            ca, cb = models[a]["cutoff"], models[b]["cutoff"]
            if ca == cb:
                continue
            new, old = (a, b) if ca > cb else (b, a)
            out.append({"vendor": vendor, "newer": new, "older": old,
                        "gap_days": (date.fromisoformat(models[new]["cutoff"]) - date.fromisoformat(models[old]["cutoff"])).days,
                        "delta_pairwise": rnd(score[new] - score[old])})
    return out


def agent_section(s0, ent, models):
    traj = s0["trajectory"]
    kinds = {e["entrant"]: kind_of(e["entrant"], ent) for e in s0["entries"]}
    top3_days = defaultdict(int)
    agent_top3_days = defaultdict(int)
    leaders = []
    for p in traj:
        order = sorted(p["pairwise"], key=lambda i: (-p["pairwise"][i], i))
        for i in order[:3]:
            top3_days[i] += 1
        agents = [i for i in order if kinds[i] == "agent"]
        for i in agents[:3]:
            agent_top3_days[i] += 1
        leaders.append({"date": p["date"], "n": p["n"], "leader": order[0], "top3": order[:3]})

    weekly = defaultdict(lambda: defaultdict(list))
    ids = [e["entrant"] for e in s0["entries"]]
    for pr in s0["per_round"]:
        d = date.fromisoformat(date_of(pr["round"])[:10])
        wk = (d - timedelta(days=d.weekday())).isoformat()
        for i in ids:
            weekly[wk][i].append(pr["points"].get(i, 0.0))
    weeks = sorted(weekly)
    weekly_rows = {"weeks": weeks, "n": [len(next(iter(weekly[w].values()))) for w in weeks],
                   "pairwise": {i: [rnd(statistics.mean(weekly[w][i]), 1) for w in weeks] for i in ids}}

    agents = [i for i in ids if kinds[i] == "agent"]
    mods = [i for i in ids if kinds[i] == "model"]
    h2h = {}
    for a in agents:
        wins = tot = 0
        beat_all = 0
        shared_q = 0
        for pr in s0["per_round"]:
            sa = pr["scores"].get(a)
            if sa is None:
                continue
            la = loss_of(sa)
            ms = [loss_of(pr["scores"][m]) for m in mods if m in pr["scores"]]
            if not ms:
                continue
            shared_q += 1
            beat_all += la < min(ms)
            for lm in ms:
                tot += 1
                wins += 1 if la < lm else .5 if la == lm else 0
        h2h[a] = {"vs_models": rnd(100 * wins / tot if tot else None, 1),
                  "beats_every_model": rnd(100 * beat_all / shared_q if shared_q else None, 1),
                  "questions": shared_q}
    best_model = max((e for e in s0["entries"] if kinds[e["entrant"]] == "model"), key=lambda e: e["pairwise"])
    above = [e["entrant"] for e in s0["entries"] if kinds[e["entrant"]] == "agent" and e["pairwise"] > best_model["pairwise"]]
    first, second = s0["entries"][0]["entrant"], s0["entries"][1]["entrant"]
    diffs = [pr["points"].get(first, 0) - pr["points"].get(second, 0) for pr in s0["per_round"]]
    rng = random.Random(0)
    boot = sorted(statistics.mean(rng.choice(diffs) for _ in diffs) for _ in range(5000))
    lead = {"first": first, "second": second, "gap": rnd(statistics.mean(diffs)),
            "ci95": [rnd(boot[124]), rnd(boot[-125])]}
    return {"top3_days": dict(top3_days), "agent_top3_days": dict(agent_top3_days), "days": len(traj),
            "leaders": leaders, "weekly": weekly_rows, "h2h": h2h, "lead": lead,
            "best_model": best_model["entrant"], "agents_above_best_model": above, "agents": agents}


def short_question(r):
    q = r.get("question") or ""
    return q.split(". ")[0][:220]


def dataset_section(s0, ent, models, data):
    kinds = {e["entrant"]: kind_of(e["entrant"], ent) for e in s0["entries"]}
    rows = []
    for pr in s0["per_round"]:
        r, sc, pts = pr["round"], pr["scores"], pr["points"]
        by = defaultdict(list)
        for i, s in sc.items():
            by[kinds[i]].append((i, loss_of(s), s.get("skill"), pts[i]))
        if not by["agent"] or not by["model"]:
            continue
        best = {k: min(v, key=lambda t: t[1]) for k, v in by.items()}
        best_base = min((t for t in by["baseline"]), key=lambda t: t[1]) if by["baseline"] else None
        contenders = by["agent"] + by["model"]
        skills = [t[2] for t in contenders if t[2] is not None]
        value = (r.get("resolution") or {}).get("value")
        last = (r.get("forecasts") or {}).get("persistence") or {}
        moved = value - last["mean"] if isinstance(value, (int, float)) and isinstance(last, dict) \
            and isinstance(last.get("mean"), (int, float)) else None
        rows.append({
            "round": r["round_id"], "tracker": r["tracker"], "domain": r["domain"], "type": r["target_type"],
            "question": short_question(r), "resolved": date_of(r)[:10],
            "agent_pts": rnd(statistics.mean(t[3] for t in by["agent"]), 1),
            "model_pts": rnd(statistics.mean(t[3] for t in by["model"]), 1),
            "best_agent": best["agent"][0], "best_model": best["model"][0],
            "best_baseline": best_base[0] if best_base else None,
            "baseline_wins": bool(best_base and best_base[1] < min(t[1] for t in contenders)),
            "share_beat_persistence": rnd(100 * sum(s > 0 for s in skills) / len(skills), 1) if skills else None,
            "median_skill": rnd(100 * statistics.median(skills), 1) if skills else None,
            "winner": min(sc.items(), key=lambda kv: loss_of(kv[1]))[0],
            "moved": rnd(moved, 1),
        })
    for x in rows:
        x["gap"] = rnd(x["model_pts"] - x["agent_pts"], 1)
    groups = defaultdict(list)
    for x in rows:
        groups[(x["tracker"], x["type"])].append(x)
    by_group = []
    for (tr, ty), v in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        by_group.append({"tracker": tr, "type": ty, "n": len(v),
                         "agent_pts": rnd(statistics.mean(x["agent_pts"] for x in v), 1),
                         "model_pts": rnd(statistics.mean(x["model_pts"] for x in v), 1),
                         "baseline_wins": sum(x["baseline_wins"] for x in v),
                         "median_skill": rnd(statistics.median(x["median_skill"] for x in v), 1)})
    return {"questions": rows, "by_group": by_group}


def early_section(data, s0, ent):
    """Standings after the first k days of resolutions vs the latest standings."""
    final = {e["entrant"]: e["pairwise"] for e in s0["entries"]}
    final_skill = {e["entrant"]: e["mean_skill"] for e in s0["entries"]}
    kinds = {e["entrant"]: kind_of(e["entrant"], ent) for e in s0["entries"]}
    first = date.fromisoformat(s0["trajectory"][0]["date"])
    out = []
    for k in (1, 2, 7, 14):
        cut = (first + timedelta(days=k - 1)).isoformat()
        b = build(data, scope="s0", until=cut)
        early = {e["entrant"]: e["pairwise"] for e in b["entries"]}
        early_sk = {e["entrant"]: e["mean_skill"] for e in b["entries"]}
        rho, _ = spearman(early, final)
        rho_sk, _ = spearman(early_sk, final_skill)
        ag_e = {i: v for i, v in early.items() if kinds[i] == "agent"}
        ag_f = {i: v for i, v in final.items() if kinds[i] == "agent"}
        rho_ag, _ = spearman(ag_e, ag_f)
        top = lambda d: sorted(d, key=lambda i: (-d[i], i))[:3]
        out.append({"days": k, "through": cut, "questions": b["scored_rounds"],
                    "rho_all": rnd(rho), "rho_skill": rnd(rho_sk), "rho_agents": rnd(rho_ag),
                    "leader": top(early)[0], "top3": top(early),
                    "top3_overlap": len(set(top(early)) & set(top(final)))})
    return out


def main(data_path, models_path, out_path):
    data = json.loads(Path(data_path).read_text(encoding="utf-8"))
    models = json.loads(Path(models_path).read_text(encoding="utf-8"))["models"]
    ent = {e["entrant_id"]: e for e in data["entrants"]}
    boards = {s: build(data, scope=s) for s in ("all", "s0", "matched")}
    s0 = boards["s0"]
    out = {
        "generated_at": data.get("generated_at"),
        "since": s0["since"],
        "boards": {s: {"questions": b["scored_rounds"], "methods": b["methods"],
                       "rows": board_rows(b, ent, models), "trajectory": trajectory_rows(b)}
                   for s, b in boards.items()},
        "models": model_section(data, ent, models, s0),
        "agents": agent_section(s0, ent, models),
        "dataset": dataset_section(s0, ent, models, data),
        "early": early_section(data, s0, ent),
        "model_meta": models,
    }
    Path(out_path).write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    summary = {k: v for k, v in out.items() if k in ("models", "early")}
    summary["agents"] = {k: v for k, v in out["agents"].items() if k not in ("leaders", "weekly")}
    summary["dataset_groups"] = out["dataset"]["by_group"]
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0] if a else str(ROOT / "site" / "data.json"),
         a[1] if len(a) > 1 else str(HERE / "models.json"),
         a[2] if len(a) > 2 else str(ROOT / "site" / "blog" / "season-0.json"))
