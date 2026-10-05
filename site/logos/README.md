# Vendor marks

White marks on the vendor's tile colour (`VCOLOR` in `site/index.html`). Each is the vendor's current
mark, checked 10 Sep 2026.

| File | Source |
| --- | --- |
| `claude.svg`, `deepseek.svg`, `gemini.svg`, `openai.svg`, `qwen.svg`, `kimi.svg` | Simple Icons (`cdn.simpleicons.org/<slug>`), recoloured white |
| `grok.svg` | the ring-and-slash glyph from grok.com's own favicon (`/images/favicon.svg`), background and highlight removed |
| `zhipu.svg` | the Z.ai mark, the brand Zhipu moved to in 2025 |
| `crowd.svg` | ours: three circles in a cluster, the pool of everyone's answers, on the site's green |

## Team marks

External entrants wear their team's published mark. `LOGO_FILES` in `site/index.html` names the file
per vendor key, so a team without a vector mark can wear a PNG; `TILE` gives the tile the ground the
mark was published on where that differs from the team's line colour (`VCOLOR`).

| File | Team (entrant ids) | Source |
| --- | --- | --- |
| `astraculum.svg` | Astraculum (`vac-*`, `worldvac-base`) | the single-colour favicon at astraculum.com (`/YongThick1-favicon.svg`), recoloured white; tile ink |
| `h2oai.svg` | H2O.ai (`h2oai-*`) | the square H2O.ai mark from h2o.ai's site header, viewBox tightened to the word so it reads at 27 px; tile the mark's own yellow |
| `apodex.png` | Apodex (`apodex-futureflow`) | the site favicon at apodex.com (navy ground, white mark; no vector mark is published); tile the same navy |
| `yulan.png` | Renmin University of China (`yulan-onesim`) | the magnolia from the YuLan-OneSim repository logo (`assets/onesim.png`, github.com/RUC-GSAI/YuLan-OneSim), cropped to the flower on its own crimson; tile the same crimson |
| `uiuc.svg` | UIUC (`zhengzheng-agent`) | the Illinois Block I as published (orange on navy); tile Illinois navy |

Marks identify the team on the board and nothing more; each belongs to its owner. Checked 5 Oct 2026.

To re-check: fetch the vendor's current favicon or press mark and compare shapes; a mark that
changed gets replaced here, and `LOGO_VENDORS` lists which vendors have one.
