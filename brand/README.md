# The Social Simulation Arena mark

A rising line whose vertices are atoms, and one green atom where the line is heading: the released numbers so far, and the next one, not yet published. It is a member of the Social Atoms family (three paper discs on ink, seams in the tile colour), with the arena's green as its one accent.

## Files

| File | Use |
| --- | --- |
| `ssa-mark-512-dark.svg` | master, paper on ink; avatars, social cards |
| `ssa-mark-512-light.svg` | master, ink on paper; light backgrounds, print |
| `ssa-mark-64-dark.svg` | the site header and favicon; identical to `site/logo.svg` (a test keeps them equal) |
| `ssa-mark-64-light.svg` | the same cut on paper |
| `png/ssa-mark-{1024,512,192,32}.png` | raster exports of the dark master (GitHub avatar, app icons, favicon fallback) |
| `png/ssa-mark-512-light.png` | raster export of the light master |

## Rules

Square tile in the masters; platforms round the corners. The 64 cut carries a 14-unit radius because the site header does not round it. Paper `#efece4`, ink `#141416`, green `#22c55e` on ink and `#15803d` on paper. Nothing else: no gradients, no text inside the mark, no second accent. Do not redraw the geometry by hand; the drafts and the generator live in the Social Atoms brand folder (`ssa/drafts`, `h1c-pearls`).

Rasters were exported with macOS QuickLook (`qlmanage -t -s <size>`); re-export after any change to the masters.

## Other marks kept here

| Folder | What | Source |
| --- | --- | --- |
| `social-atoms/` | the Social Atoms atoms mark (three discs on ink), the organizer's mark in the README | a copy of the master in the Social Atoms brand folder; do not edit here |
| `contributors/` | one round avatar per contributor (`<login>.svg`), the wall in the README, each linked to its profile | drawn by `python -m tools.contributors_wall` from the GitHub roster, avatars embedded, bots left out; the tool also rewrites the README block |
