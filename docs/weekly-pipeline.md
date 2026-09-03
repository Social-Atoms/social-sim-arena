# The reviewed weekly pipeline

This is the operator path from committed source artifacts to a scored-form
weekly batch. It is deliberately split at one human-owned boundary:
generation proposes candidates; only a reviewed edit to `questions/season0.json`
approves them.

## 1. Generate the review horizon, offline

Use an explicit clock so another reviewer can reproduce the same candidate
files. Season 0's last special releases on 2026-12-01, so this command covers
the part that the ordinary eight-week preview cannot reach:

```bash
PYTHONPATH=. python3 tools/generate_rounds.py \
  --now 2026-09-01T12:00:00Z \
  --through 2026-12-01 \
  --rejects --write
```

The command reads committed archives only. It does not need a key or a network
connection and it does not update an archive. With the 2026-09-01 review clock
it writes candidate manifests through `batch-2026-11-23`; the exact count may
change when the reviewed season gains rounds, because an already-reviewed
target is omitted.

Only a proved release calendar may produce timestamps. Civiqs dates mean the
value displayed on the registry-declared Friday, and the Wikipedia ranking
calendar is rolled from a reviewed round contract. Silver Bulletin series are
reported as `gate: schedule`: their archive dates are poll field midpoints, not
publication dates, so a regular-looking weekday is not permission to invent a
future release.

`--through` is an explicit date bound, not approval to extrapolate forever.
Without it the generator keeps the conservative `MAX_WEEKS_AHEAD = 8` preview.
Neither form writes `questions/season0.json`.

Every refusal printed after `refused:` names its gate and evidence. An operator
should fix an archive or policy decision, not delete the refusal from output.

## 2. Review, then validate before publication

Review each candidate's wording, schedule, unit, source, target shape and
resolution rule. Move only accepted objects into `questions/season0.json` by
hand. Then run:

```bash
PYTHONPATH=. python3 tools/validate_season.py
```

Publication fails for:

- malformed or source-inconsistent scalar/profile/ranking shapes;
- a lock/release schedule that violates the source contract;
- a vague or moving resolution rule instead of an archived first print;
- the same scored target under a second round id;
- a source whose inventory rights are not `approved`.

Thirteen already-published Season 0 rounds predate the rights gate. They are
grandfathered by exact round id in `ssa/season.py`; the exception cannot approve
one more round from the same source.

## 3. Build and validate one reviewed batch

From a clean checkout with `pip install -r requirements.txt`, this one command
validates the reviewed season, builds the batch twice, requires byte-identical
output, validates the mixed-shape bundle contract and writes the artifact:

```bash
PYTHONPATH=. python3 tools/make_bundle.py \
  --batch batch-2026-09-14 \
  --out /tmp/batch-2026-09-14.json
```

Success prints both `OK reviewed manifest` and `OK deterministic bundle`, plus
the canonical SHA-256. Every committed file under `questions/bundles/` is also
rebuilt from the reviewed season in CI and must match canonically.

The output is a projection of the human-reviewed season. The builder refuses
to write inside `questions/`, so it cannot approve its own input.

## 4. Exercise submission, resolution, scoring and status

Run the completely offline three-shape rehearsal:

```bash
PYTHONPATH=. python3 tools/run_sandbox_cycle.py
```

The script:

1. generates `examples/bundle/sandbox-batch.json` from the committed sandbox
   round definitions twice and checks byte identity;
2. feeds that exact generated bundle to the shipped example entrant;
3. invokes the current #47 CLI route, `tools/accept_bundle.py --sandbox`;
4. injects adapter-shaped offline observations into the production scalar,
   profile and ranking resolution functions, without editing the response or
   forecast JSON;
5. passes the CLI-written records through CRPS, energy-score and Kendall
   scorers; and
6. reports every round as `resolved` through `ssa.refresh.round_status`.

All writes go to a temporary directory. Nothing can enter `forecasts/` or the
live season. `--json` emits machine-readable evidence for CI.

## Failure evidence and action

| Stage | Failure says | Operator action |
|---|---|---|
| generation | gate plus source/observations/schedule evidence | correct the committed artifact or make a reviewed policy decision |
| season validation | round id and violated semantic invariant | repair the reviewed round; do not build a bundle |
| bundle build | batch/error code or deterministic mismatch | check the season diff and calendar before publishing |
| #47 CLI intake | per-round rejection and receipt | correct that answer shape; other accepted answers remain intact |
| resolution/scoring/status | exact round and failing shape stage | repair the sandbox contract or scorer before enabling the weekly route |

No step falls back to a different source, rewrites participant JSON, or promotes
a candidate automatically.
