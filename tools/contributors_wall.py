"""Draw the contributors wall for the README: round avatars, each one a link to its profile.

GitHub renders an SVG in a README as one inert image, so a single wall could not link
anywhere. Each contributor therefore gets an SVG of their own (the avatar clipped to a
circle, embedded as base64 because the README image proxy blocks external references
inside an SVG), and the README wraps each in a link. The block between the markers in
README.md is rewritten; bots are left out; people are ordered by commit count.

    python -m tools.contributors_wall
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import urllib.request

REPO = "Social-Atoms/social-sim-arena"
ROOT = os.path.join(os.path.dirname(__file__), "..")
OUT = os.path.join(ROOT, "brand", "contributors")
README = os.path.join(ROOT, "README.md")
BOTS = {"actions-user", "github-actions[bot]", "dependabot[bot]"}
SIZE = 64
START, END = "<!-- contributors:start -->", "<!-- contributors:end -->"


def contributors():
    raw = subprocess.check_output(["gh", "api", f"repos/{REPO}/contributors", "--paginate"])
    rows = [r for r in json.loads(raw) if r["login"] not in BOTS and r.get("type") != "Bot"]
    return sorted(rows, key=lambda r: -r["contributions"])


def avatar_png(url: str) -> bytes:
    req = urllib.request.Request(url.split("?")[0] + "?s=128&v=4", headers={"User-Agent": "ssa-readme"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def round_avatar(login: str, png: bytes) -> str:
    data = base64.b64encode(png).decode()
    r = SIZE / 2
    return (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'width="{SIZE}" height="{SIZE}" viewBox="0 0 {SIZE} {SIZE}" role="img" aria-label="{login}">'
            f'<clipPath id="c"><circle cx="{r}" cy="{r}" r="{r}"/></clipPath>'
            f'<image width="{SIZE}" height="{SIZE}" clip-path="url(#c)" xlink:href="data:image/png;base64,{data}"/>'
            f'</svg>\n')


def readme_block(rows) -> str:
    links = [f'<a href="{r["html_url"]}" title="{r["login"]}"><img src="brand/contributors/{r["login"]}.svg" '
             f'width="{SIZE}" height="{SIZE}" alt="{r["login"]}"></a>' for r in rows]
    return START + "\n<p>\n" + "\n".join(links) + "\n</p>\n" + END


def main():
    rows = contributors()
    os.makedirs(OUT, exist_ok=True)
    keep = {f"{r['login']}.svg" for r in rows}
    for name in os.listdir(OUT):
        if name.endswith(".svg") and name not in keep:
            os.remove(os.path.join(OUT, name))
    for r in rows:
        with open(os.path.join(OUT, f"{r['login']}.svg"), "w") as f:
            f.write(round_avatar(r["login"], avatar_png(r["avatar_url"])))
    with open(README) as f:
        text = f.read()
    if START not in text or END not in text:
        sys.exit(f"README.md needs the markers {START} and {END}")
    text = re.sub(re.escape(START) + r".*?" + re.escape(END), lambda _: readme_block(rows), text, flags=re.S)
    with open(README, "w") as f:
        f.write(text)
    print(f"{len(rows)} contributors drawn into {OUT} and README.md")
    for r in rows:
        print(f"  {r['login']:<16} {r['contributions']:>4} commits")


if __name__ == "__main__":
    sys.exit(main())
