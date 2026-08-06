# Social Sim Arena

A live benchmark for social simulation. Models forecast the next public-opinion
release before it is published. Every forecast is locked 48 hours ahead,
hashed, and scored in public once the real number drops.

Site: https://cjj.li/social-sim-arena/ · Docs: https://cjj.li/social-sim-arena/docs.html

## Why

Companies sell AI-simulated survey respondents; papers disagree about whether
they work. Every existing evaluation replays old surveys, which sit inside
training data, and vendors pick their own test sets. Live arenas fixed this in
other domains (ForecastBench, TS-Arena, LLM-SoccerArena): lock the prediction
before the answer exists, then nobody can cheat. Nobody runs one for public
opinion. This is that arena.

## Quickstart

```bash
git clone https://github.com/jajamoa/social-sim-arena
cd social-sim-arena
pip install -r requirements.txt
python -m tests.test_scoring     # hand-checked scoring tests
python -m ssa.refresh            # fetch live data, build site/data.json
open site/index.html
```

`ssa.refresh` hits two real, keyless endpoints:

- VoteHub polls API: `https://api.votehub.com/polls?poll_type=approval`
- FRED CSV (Michigan sentiment): `https://fred.stlouisfed.org/graph/fredgraph.csv?id=UMCSENT`

## Repo layout

```
questions/    season round definitions (round_id, lock_at, release_at, resolve rule)
ssa/          pipeline: adapters, averaging, baselines, scoring, refresh
forecasts/    submissions: forecasts/<round_id>/<entrant>.json
resolutions/  resolved ground truth per round
schema/       forecast submission JSON schema
tools/        submission validator (also run by CI)
tests/        hand-checked scoring tests
site/         static entry page + data.json (generated)
.github/      daily data refresh cron + PR validation
```

## Submitting a forecast

1. Read the open rounds: `questions/season0.json` (machine readable, also
   rendered on the site).
2. Add one file: `forecasts/<round_id>/<entrant>.json` matching
   `schema/forecast.schema.json`. Distributions, not points: every target
   needs a mean and an sd.
3. Open a pull request before the round's `lock_at`. CI validates the schema
   and the deadline, and prints the canonical sha256 your entry is cited by.

Baselines (persistence, trend, poll-average snapshot, human panel) run in
every round. The headline metric is skill: `1 - CRPS(you) / CRPS(persistence)`.

## Status

Prototype, season 0. Live rounds start Aug 11, 2026. The data refresh runs
daily via GitHub Actions. Known gaps and open tasks are listed on the docs
page.
