"""Draw the contributors wall for the README: round avatars in a grid, as one SVG.

GitHub's README renderer loads images through a proxy that blocks external references inside an
SVG, so every avatar is embedded (base64) rather than linked. Bots are left out; people are ordered
by their commit count on GitHub. Run it when the roster changes:

    python -m tools.contributors_wall            # writes brand/contributors/wall.svg
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import urllib.request

REPO = "Social-Atoms/social-sim-arena"
OUT = os.path.join(os.path.dirname(__file__), "..", "brand", "contributors", "wall.svg")
BOTS = {"actions-user", "github-actions[bot]", "dependabot[bot]"}
SIZE, GAP, COLUMNS = 64, 10, 10


def contributors():
    raw = subprocess.check_output(["gh", "api", f"repos/{REPO}/contributors", "--paginate"])
    rows = [r for r in json.loads(raw) if r["login"] not in BOTS and r.get("type") != "Bot"]
    return sorted(rows, key=lambda r: -r["contributions"])


def avatar_png(url: str) -> bytes:
    req = urllib.request.Request(url.split("?")[0] + "?s=128&v=4", headers={"User-Agent": "ssa-readme"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def wall(rows) -> str:
    cols = min(COLUMNS, max(1, len(rows)))
    lines = (len(rows) + cols - 1) // cols
    w = cols * SIZE + (cols - 1) * GAP
    h = lines * SIZE + (lines - 1) * GAP
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
             f'width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="Contributors">',
             "<defs>"]
    for i in range(len(rows)):
        parts.append(f'<clipPath id="c{i}"><circle cx="{SIZE / 2}" cy="{SIZE / 2}" r="{SIZE / 2}"/></clipPath>')
    parts.append("</defs>")
    for i, r in enumerate(rows):
        x = (i % cols) * (SIZE + GAP)
        y = (i // cols) * (SIZE + GAP)
        data = base64.b64encode(avatar_png(r["avatar_url"])).decode()
        parts.append(f'<a xlink:href="{r["html_url"]}" target="_blank"><title>{r["login"]}</title>'
                     f'<g transform="translate({x} {y})" clip-path="url(#c{i})">'
                     f'<image width="{SIZE}" height="{SIZE}" xlink:href="data:image/png;base64,{data}"/></g></a>')
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main():
    rows = contributors()
    svg = wall(rows)
    with open(OUT, "w") as f:
        f.write(svg)
    print(f"{OUT}: {len(rows)} contributors, {len(svg) // 1024} KB")
    for r in rows:
        print(f"  {r['login']:<16} {r['contributions']:>4} commits")


if __name__ == "__main__":
    sys.exit(main())
