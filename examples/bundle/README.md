# Bundle submission starter

A complete Route B rehearsal, standard library only, that never touches the
season. Read [`docs/bundle-submission.md`](../../docs/bundle-submission.md) for
the contract; this is the runnable version of it.

Maintainers can run the generated bundle through intake, resolution, scoring
and status in one command:

```bash
PYTHONPATH=. python3 tools/run_sandbox_cycle.py
```

```bash
# 1. answer the sandbox batch
python examples/bundle/entrant.py \
    --bundle examples/bundle/sandbox-batch.json \
    --entrant demo_bundle_entrant \
    --anchor examples/bundle/sandbox-anchors.json \
    --out /tmp/response.json

# 2. check it the way the arena will
python tools/validate_bundle.py examples/bundle/sandbox-batch.json /tmp/response.json

# 3. see the forecast records it normalises into
python tools/accept_bundle.py /tmp/response.json \
    --bundle examples/bundle/sandbox-batch.json \
    --sandbox --write --out /tmp/sandbox-forecasts
```

## What is here

| file | |
|---|---|
| `sandbox-rounds.json` | the three source round definitions; never part of the season |
| `sandbox-batch.json` | their deterministic generated bundle: one scalar, one profile, one ranking |
| `sandbox-anchors.json` | what the example entrant is told to centre on |
| `sandbox-source-observations.json` | adapter-shaped scalar/profile series and ranking observation resolved by production functions |
| `sandbox-response.json` | the answer bundle steps 1–2 produce, committed so the format can be read without running anything |
| `entrant.py` | the client: bundle in, response bundle out |

The sandbox rounds are not in `questions/season0.json` and its deadline is
years out. Both are deliberate: a rehearsal that could enter the season is not
a rehearsal, and a rehearsal that quietly expires becomes a demonstration of a
late submission on the day a new team first runs it.

## `entrant.py` is not a forecaster

It answers every question with a deliberately wide prior centred on whatever
anchor it is given, and the numbers carry no information about the world. It
exists to settle the format questions — how the three answer shapes sit in one
payload, where the entrant id goes, what a distribution has to contain — so
that the only thing left to write is `answer_for`. Replace that one function
and the rest of the file is already a working submission client.

Nothing in it is invented silently. A question it cannot honestly answer raises
rather than filing something plausible-looking: a free-choice ranking round
asks for ten article titles out of all of Wikipedia, and a default answer to
that would be ten titles nobody chose, scored as though somebody had. Use
`--anchor` to say what your model actually believes, or `--skip-unanswerable`
to leave those rounds unanswered — an unanswered round simply scores nothing
and is listed on the receipt.

`--quantiles` emits the quantile form instead of `mean`/`sd`. The arena scores
the two with the same CRPS, so the choice is about which one you can state
honestly, not which one scores better; use quantiles when your belief is skewed
or fat-tailed and a normal would misstate it.

## A real week

```bash
python tools/make_bundle.py --list
python tools/make_bundle.py --batch batch-2026-09-14 --out /tmp/real.json
```

`batch-2026-09-14` is 17 questions — 15 scalar, one 16-cell profile, and one
ranking — with horizons from 0.1 to 6.1 days and a single deadline of
2026-09-14T12:00:00Z. The example entrant answers it unchanged, given anchors.
