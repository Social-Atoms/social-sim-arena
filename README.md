# Social Simulation Arena

![Simulated societies graded by the real future](assets/teaser.png)

A live benchmark for social simulation. Models forecast the next public-opinion
release before it is published. Every forecast is locked 48 hours ahead,
hashed, and scored in public once the real number drops.

Site: https://social-simulation-arena.com · Docs: https://social-simulation-arena.com/docs.html

## Why

Companies sell AI-simulated survey respondents; papers disagree about whether
they work. Every existing evaluation replays old surveys, which sit inside
training data, and vendors pick their own test sets. Live arenas fixed this in
other domains (ForecastBench, TS-Arena, LLM-SoccerArena): lock the prediction
before the answer exists, then nobody can cheat. Nobody runs one for public
opinion. This is that arena.

## Quickstart

```bash
git clone https://github.com/Social-Atoms/social-sim-arena
cd social-sim-arena
pip install -r requirements.txt
python -m tests.test_scoring     # hand-checked scoring tests
python -m ssa.refresh            # fetch live data, build site/data.json
open site/index.html
```

`ssa.refresh` hits real, keyless endpoints, all same-day or first-party:

- Silver Bulletin poll CSVs (approval + generic ballot, updated same day)
- YouGov tracker download (weekly Economist/YouGov waves, demographic breaks)
- Michigan SCA official release + FRED CSV (consumer sentiment)
- VoteHub polls API (backfill history)

## Repo map

```
questions/    the season: every round, its lock and release time, frozen up front
forecasts/    one file per entrant per round; the PR that adds it is the submission
locks/        sha256 manifests written at lock time; the pre-registration record
resolutions/  the published numbers rounds resolved against, with sources
entrants/     who is competing: one registration file per entrant
ssa/          the pipeline: adapters -> series -> baselines -> harness -> scoring -> refresh
schema/       JSON schemas the CI validator enforces
tools/        validate_submission.py and operator tools
tests/        hand-checked unit tests (python -m tests.test_scoring)
site/         the static site; data.json is the pipeline's only output artifact
backtest/     committed evidence of the model backtest (runs/*.jsonl)
.github/      refresh cron + submission validation + lock audit
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

## How the pipeline fits together

![Pipeline](assets/fig-pipeline.png)

Full protocol figures and the teaser live in `assets/`.

## Status

Prototype, season 0. Live rounds start Aug 11, 2026. The data refresh runs
daily via GitHub Actions. Known gaps and open tasks are listed on the docs
page.
