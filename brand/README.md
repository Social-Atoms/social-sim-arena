# The Social Simulation Arena mark

A single tapered line with one fold, thin where it starts and heavy where it ends, and one green point ahead of it: the record so far, and the next number, not yet published. Paper on ink, in the Social Atoms family, with the arena's green as its one accent.

## Files

| File | Use |
| --- | --- |
| `ssa-mark-512-dark.svg` | master, paper on ink; avatars, social cards |
| `ssa-mark-512-light.svg` | master, ink on paper; light backgrounds, print |
| `ssa-mark-64-dark.svg` | the site header and favicon; identical to `site/logo.svg` (a test keeps them equal) |
| `ssa-mark-64-light.svg` | the same cut on paper |
| `ssa-mark-32-dark.svg`, `ssa-mark-16-dark.svg` | heavier cuts for favicon sizes (the line thickens as the tile shrinks) |
| `png/ssa-mark-{1024,512,192,32}.png` | raster exports of the dark master (GitHub avatar, app icons, favicon fallback) |
| `png/ssa-mark-512-light.png` | raster export of the light master |
| `readme/{ssa,social-atoms}-{dark,light}.svg` | the two marks for the README's `<picture>`: the dark tile under a dark scheme, the paper tile under a light one; corners rounded (112/512) because GitHub does not round them |

## Rules

Square tile in the masters; platforms round the corners. The 64 cut carries a 14-unit radius because the site header does not round it. Paper `#efece4`, ink `#141416`, green `#22c55e` on ink and `#15803d` on paper. Nothing else: no gradients, no text inside the mark, no second accent. Each size is its own cut (512, 64, 32, 16): the line gets thicker and the point larger as the tile shrinks, so the mark stays readable as a favicon. Do not redraw the geometry by hand; the drafts and the generator live in the Social Atoms brand folder (`ssa/drafts/r9`, family `q-deep`).

Rasters were exported with macOS QuickLook (`qlmanage -t -s <size>`); re-export after any change to the masters.

## Other marks kept here

| Folder | What | Source |
| --- | --- | --- |
| `social-atoms/` | the Social Atoms atoms mark (three discs on ink, and its paper twin), the organizer's mark in the README | copies of the masters in the Social Atoms brand folder; do not edit here |
| `contributors/` | one round avatar per contributor (`<login>.svg`), the wall in the README, each linked to its profile | drawn by `python -m tools.contributors_wall` from the GitHub roster, avatars embedded, bots left out; the tool also rewrites the README block |
