#!/usr/bin/env python3
"""Snapshot model metadata for the Season 0 report.

One row per SSA model family (the entrant id without harness suffixes):
display name and training cutoff from SSA's own method card, release date
and Artificial Analysis indexes from OpenRouter's public catalog, and 7-day
token volume from the OpenRouter model page. Add a family to FAMILIES to
include it in later reports; no other edit is needed.

    python fetch_models.py [data.json]      # writes models.json
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
UA = {"User-Agent": "ssa-season-report (research snapshot)"}

# SSA family stem -> OpenRouter id. None: the method is not an OpenRouter model.
FAMILIES = {
    "claude-sonnet": "anthropic/claude-sonnet-5",
    "claude-opus-5": "anthropic/claude-opus-5",
    "deepseek-pro": "deepseek/deepseek-v4-pro",
    "deepseek-flash": "deepseek/deepseek-v4-flash",
    "gemini-pro": "google/gemini-3.1-pro-preview",
    "gemini-flash": "google/gemini-3.6-flash",
    "glm": "z-ai/glm-5.2",
    "gpt-5.6-luna": "openai/gpt-5.6-luna",
    "gpt-5.6-terra": "openai/gpt-5.6-terra",
    "grok": "x-ai/grok-4.5",
    "kimi": "moonshotai/kimi-k3",
    "qwen-3.8": "qwen/qwen3.8-max-0902",
}
CUTOFF = re.compile(r"Training cutoff (\d{4}-\d{2}-\d{2}) \[(\w+)\]")
NAME = re.compile(r"^(.*?) \(")
DATE_TOK = re.compile(
    r'\\"date\\":\\"(\d{4}-\d{2}-\d{2})[^\"]*\\",\\"model_permaslug\\":\\"([^\\]+)\\",'
    r'\\"variant\\":\\"([^\\]+)\\",\\"total_completion_tokens\\":(\d+),'
    r'\\"total_prompt_tokens\\":(\d+)'
)


def get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return r.read()


def tokens_7d(or_id: str, slug: str) -> int | None:
    try:
        html = get("https://openrouter.ai/" + or_id).decode("utf-8", "replace")
    except Exception:
        return None
    by_date: dict[str, int] = {}
    for date, permaslug, _v, comp, prompt in DATE_TOK.findall(html):
        if permaslug.startswith(slug):
            by_date[date] = max(by_date.get(date, 0), int(comp) + int(prompt))
    last = sorted(by_date)[-7:]
    return sum(by_date[d] for d in last) if last else None


def main(data_path: str) -> None:
    data = json.loads(Path(data_path).read_text(encoding="utf-8"))
    cards = {e["entrant_id"]: e for e in data.get("entrants") or []}
    catalog = {m["id"]: m for m in json.loads(get("https://openrouter.ai/api/v1/models"))["data"]}
    rows = {}
    for stem, or_id in FAMILIES.items():
        card = cards.get(stem) or {}
        method = card.get("method") or ""
        cut = CUTOFF.search(method)
        name = NAME.search(card.get("name") or "")
        m = catalog.get(or_id) or {}
        aa = (m.get("benchmarks") or {}).get("artificial_analysis") or {}
        created = m.get("created")
        rows[stem] = {
            "name": name.group(1) if name else stem,
            "or_id": or_id,
            "cutoff": cut.group(1) if cut else m.get("knowledge_cutoff"),
            "cutoff_source": cut.group(2) if cut else ("openrouter" if m.get("knowledge_cutoff") else None),
            "release": datetime.fromtimestamp(created, timezone.utc).date().isoformat() if created else None,
            "aa_intelligence": aa.get("intelligence_index"),
            "aa_coding": aa.get("coding_index"),
            "aa_agentic": aa.get("agentic_index"),
            "or_tokens_7d": tokens_7d(or_id, m.get("canonical_slug") or or_id) if or_id else None,
        }
        print(stem, rows[stem])
    out = {"fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "sources": ["SSA method cards (site/data.json entrants[].method)",
                       "https://openrouter.ai/api/v1/models", "https://openrouter.ai/<model>"],
           "models": rows}
    (HERE / "models.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(HERE.parent.parent / "site" / "data.json"))
