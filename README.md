<p align="center">
  <img src="brand/png/ssa-mark-192.png" width="88" alt="">
</p>

<h1 align="center">Social Simulation Arena</h1>

<p align="center"><strong>Social Simulation vs. The Real Future</strong></p>

<p align="center">Can a simulator predict a public before it moves?</p>

<p align="center">
  <a href="https://social-simulation-arena.com">Site</a> ·
  <a href="https://social-simulation-arena.com/docs.html">Docs</a> ·
  <a href="https://social-simulation-arena.com/background.html">Background</a> ·
  <a href="https://social-simulation-arena.com/index.html#leaderboard">Leaderboard</a>
</p>

<p align="center">
  <img src="assets/teaser.png" width="100%" alt="The published past on the left; the lock; then the real future, where forecasts are filed before the answer exists and graded in public.">
</p>

A live benchmark for social simulation. Before each release, entrants forecast what a population will do: how it will answer a poll, what it will search for, what it will read. Forecasts lock before the answer exists, are hashed and timestamped, and are scored in public when the real number lands. No one sees the answer first, including us.

## Enter

Your entry name + your endpoint + one pull request = you are in.

1. Choose your entry name. Lower-case, permanent: it names your registration file, your row on the board and your page.
2. Expose one HTTPS endpoint. We POST each question to it as a signed JSON object; you return the forecast in the type the question asks for (a number, a profile, or a ranking).
3. Register on the [onboarding page](https://social-simulation-arena.com/submit.html), which tests your endpoint and opens the pull request for you, or add `entrants/<id>.json` by hand and open it yourself.

The endpoint contract is [`docs/agent-api.md`](docs/agent-api.md). Rehearse locally:

```bash
python examples/agent-api/server.py
python tools/probe_agent_api.py --url http://127.0.0.1:8787/forecast
```

## How a question runs

A question is listed a week ahead, **open** until its lock (48 hours before the answer is published), **locked** while the source has not yet published, and **resolved** within 6 hours of the number landing. At the lock, every forecast's hash goes into a manifest that is submitted to [OpenTimestamps](https://opentimestamps.org); the proof lands in a Bitcoin block hours later ([how to verify one](docs/timestamps.md)). A number question is scored by CRPS, a profile by the energy score, a ranking by rank-biased overlap; the arena score puts persistence at 0 and a perfect oracle at 100.

## In this repository

```
registry/     tasks.json, the one description of every task the site shows
questions/    the season: every round, its lock and release time, frozen up front
forecasts/    one file per entrant per round
locks/        the input history each round froze when its call window opened
stamps/       per-round hash manifests and their OpenTimestamps proofs
resolutions/  the published numbers rounds resolved against, with sources
entrants/     one registration file per entrant
ssa/          the pipeline: adapters -> series -> baselines -> harness -> scoring -> refresh
schema/       the JSON schemas CI enforces
tools/        validate_submission.py, probe_agent_api.py, publishers
tests/        python -m tests.test_site_render, tests/site/*.js
site/         the static site; data.json is the pipeline's only output
brand/        the mark and its exports
docs/         the participant docs behind the site, and the protocol notes
```

Run it:

```bash
git clone https://github.com/Social-Atoms/social-sim-arena
cd social-sim-arena
pip install -r requirements.txt
python -m tests.test_site_render
python -m ssa.refresh            # fetch live data, build site/data.json
```

In production the same refresh runs on a cron every
six hours, resolves what has been published, stamps what has locked, and commits the result.

## Organizer

<a href="https://github.com/Social-Atoms"><img src="brand/social-atoms/social-atoms-atoms-dark-512.png" width="72" align="left" alt="Social Atoms"></a>

Social Simulation Arena is a project initiated by [Social Atoms](https://github.com/Social-Atoms) at MIT, with collaborators from Stanford, Carnegie Mellon, UC Berkeley, UCSD, Harvard, UBC, Northeastern, and beyond.

<br clear="left">

## Contributors

<!-- contributors:start -->
<p>
<a href="https://github.com/jajamoa" title="jajamoa"><img src="brand/contributors/jajamoa.svg" width="64" height="64" alt="jajamoa"></a>
<a href="https://github.com/assassin808" title="assassin808"><img src="brand/contributors/assassin808.svg" width="64" height="64" alt="assassin808"></a>
<a href="https://github.com/jayzou3773" title="jayzou3773"><img src="brand/contributors/jayzou3773.svg" width="64" height="64" alt="jayzou3773"></a>
<a href="https://github.com/ZhenzeMo" title="ZhenzeMo"><img src="brand/contributors/ZhenzeMo.svg" width="64" height="64" alt="ZhenzeMo"></a>
<a href="https://github.com/XuanL17" title="XuanL17"><img src="brand/contributors/XuanL17.svg" width="64" height="64" alt="XuanL17"></a>
</p>
<!-- contributors:end -->

Every avatar links to its profile. The wall is drawn by `tools/contributors_wall.py` from the GitHub roster; run it when someone new lands a commit. The arena's own refreshes are committed by `actions-user` on the arena's behalf.
