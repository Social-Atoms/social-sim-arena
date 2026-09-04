# The weekly bundle: question file in, answer file out

Reference for Route B. If you are starting from scratch, read
[`docs/participant-quickstart.md`](participant-quickstart.md) first — it is the
onboarding path and it links back here for the details.

A **bundle is a batch**: one deadline, every question due at it, one reply
carrying every answer. It is not a new kind of forecast. An accepted answer
becomes exactly one `forecasts/<round_id>/<entrant_id>.json` record matching
[`schema/forecast.schema.json`](../schema/forecast.schema.json) — the same file a
pull request would add, read by the same scorer.

## The deadline

**Your deadline is Monday 12:00Z.** It is the same for every question in the
bundle and the same for every entrant.

Each question also carries a `lock_at`. That is the arena's internal clock —
`release − 48h` — and it falls **0 to 7 days after your deadline**. It is
published so you can see how far ahead a question is asking (`horizon_days`),
never as a due date. A submission is judged against the bundle's `deadline`,
which is always the earlier of the two.

Filing early is allowed and costs nothing. The reference forecasts your score
is divided by freeze at the same instant as the deadline, so an entrant who
files on Tuesday is not compared against a baseline that read Sunday's news.
Waiting is not an edge and is no longer scored as one. The rule and the dated
cutover are in [`docs/submission-window.md`](submission-window.md);
`ssa/batches.py` is the implementation.

## The two files

| | |
|---|---|
| questions | [`schema/bundle.schema.json`](../schema/bundle.schema.json) |
| answers | [`schema/bundle_response.schema.json`](../schema/bundle_response.schema.json) |
| version | `schema_version: "1.0.0"` in both; the response must repeat the bundle's |

A question bundle:

```json
{
  "schema_version": "1.0.0",
  "batch_id": "batch-2026-09-14",
  "deadline": "2026-09-14T12:00:00Z",
  "published_at": "2026-09-07T12:00:00Z",
  "questions": [
    {
      "round_id": "civiqs-2026-w38-approval",
      "tracker": "civiqs",
      "series": "civiqs_net_approval",
      "question": "Civiqs daily tracker: Donald Trump's net job approval …",
      "unit": "net points (approve minus disapprove)",
      "target_type": "continuous_normal",
      "release_at": "2026-09-18T14:00:00Z",
      "lock_at": "2026-09-16T14:00:00Z",
      "horizon_days": 2.083,
      "resolve": "dashboard Friday value, from the daily archive"
    }
  ]
}
```

Two fields appear only on the shapes that need them. A `profile_energy`
question carries `cells`, the complete cell roster. A `ranking_list` question
carries `ranking_length`, and either `items` (a fixed basket to permute) or
`exclusions` (the id of the rule saying which titles cannot appear, for a
free-choice round). `unit` and `resolve` are verbatim from the frozen round
definition, because those are what the answer is scored against.

## The three answer shapes, in one payload

A batch mixes them — `batch-2026-09-14` is 15 scalar rounds, one 16-cell
profile, and one ranking round — so one response carries all three. Which shape
a question takes is fixed by its `target_type`; it is not a choice. A single
number is not a weaker answer to a profile round, it is an answer to a different
question, and the intake refuses it rather than reshaping it.

```json
{
  "schema_version": "1.0.0",
  "batch_id": "batch-2026-09-14",
  "entrant_id": "myteam_ensemble",
  "answers": [
    {"round_id": "civiqs-2026-w38-approval",
     "topline": {"mean": -6.5, "sd": 3.2}},

    {"round_id": "civiqs-profile-2026-w38",
     "profile": {"civiqs_net_approval_dem": {"mean": -78.0, "sd": 4.0},
                 "civiqs_net_approval_rep": {"mean": 74.0, "sd": 4.0}}},

    {"round_id": "wiki-top10-2026-09-27",
     "ranking": ["Article_One", "Article_Two"]}
  ],
  "notes": "weekly model run, closed-book"
}
```

Rules the schema enforces:

- **A distribution, not a point.** Either `mean` plus a strictly positive `sd`,
  or a `quantiles` map that includes `"0.5"` and does not decrease as the level
  rises. Both are scored with the same CRPS, so pick the one you can state
  honestly — quantiles when your belief is skewed or fat-tailed. A point guess
  is refused: CRPS on a spike is just absolute error, and a forecaster who never
  states an uncertainty cannot be told apart from one who is always sure.
- **A profile carries every declared cell and only those.** The energy score is
  a norm over the whole vector, so a profile with a hole has no score.
- **A fixed-basket ranking is a permutation.** Exactly `ranking_length` items,
  each once, all from `items`.
- **`notes` is published with the forecast.** Never put an API key, an endpoint
  URL, or anything else you would not put in a pull request into it.

Answering only some rounds is fine. The unanswered ones simply score nothing;
they are listed on the receipt so you can see which they were.

## Check it before you upload

```bash
git clone https://github.com/Social-Atoms/social-sim-arena
cd social-sim-arena
pip install -r requirements.txt

python tools/validate_bundle.py BUNDLE.json                  # the questions
python tools/validate_bundle.py BUNDLE.json RESPONSE.json    # your answers
```

Nothing there touches the network. It runs the same code the arena runs, so the
verdict you get locally is the verdict you will get — a local checker that is
merely *similar* to the real one teaches you to trust something that does not
bind. Exit status is 0 only when every answer was accepted.

`--now 2026-09-13T09:00:00Z` shows what a given arrival time would produce. Run
without it, the deadline check uses your clock, which is a preview; the arena
uses the moment your upload arrives, which is the ruling.

## What comes back

Per round, `accepted` or `rejected` with a reason you can act on:

| reason | what happened |
|---|---|
| `late` | it arrived at or after the batch deadline |
| `wrong_shape` | a topline for a profile round, or the reverse |
| `invalid_answer` | schema or round-rule violation — missing cell, bad `sd`, item outside the basket |
| `unknown_round` | the round is not in this bundle |
| `duplicate_round` | the same round answered twice; which one is the forecast is not the arena's decision |

And one receipt for the file:

```json
{
  "schema_version": "1.0.0",
  "batch_id": "batch-2026-09-14",
  "entrant_id": "myteam_ensemble",
  "deadline": "2026-09-14T12:00:00Z",
  "received_at": "2026-09-12T08:41:07Z",
  "bundle_sha256": "4a70000f820ee8af…",
  "response_sha256": "683de9ae13f567…",
  "accepted": 12, "rejected": 1, "unanswered": []
}
```

`received_at` is the **arena's** clock. There is no field for a client
timestamp and adding one is a schema violation, because a deadline a submitter
can set is not a deadline. `bundle_sha256` pins which questions were asked, so
a later dispute cannot turn on a manifest that moved; `response_sha256` pins
the exact bytes you sent, which is also what makes a retried upload after a
dropped connection identifiable as a replay rather than a second, conflicting
submission.

Each filed record's `notes` ends with `[bundle=<batch_id> ans=<12 hex>]`, the
hash of that one answer. Per answer rather than per file on purpose: hashing the
whole upload would mean a one-line correction rewrote every record's notes, and
with them every canonical hash in the batch.

Re-uploading before the deadline replaces what you filed. Re-uploading after it
does not: a late revision is rejected round by round and never overwrites what
landed on time.

## Try the whole path without entering the season

For the maintainer's generated-batch → intake → resolution → scoring → status
check, run:

```bash
PYTHONPATH=. python3 tools/run_sandbox_cycle.py
```

This generates the committed three-shape sandbox bundle from
`examples/bundle/sandbox-rounds.json`, requires byte identity, sends that exact
bundle through the current #47 CLI intake, resolves via the production
shape-specific resolution functions, and scores the resulting records against
the committed sandbox source artifact. Every write is temporary. See
[`docs/weekly-pipeline.md`](weekly-pipeline.md) for the operator evidence.

To inspect the participant-facing pieces individually:

```bash
python examples/bundle/entrant.py \
    --bundle examples/bundle/sandbox-batch.json \
    --entrant demo_bundle_entrant \
    --anchor examples/bundle/sandbox-anchors.json \
    --out /tmp/response.json

python tools/validate_bundle.py examples/bundle/sandbox-batch.json /tmp/response.json

python tools/accept_bundle.py /tmp/response.json \
    --bundle examples/bundle/sandbox-batch.json \
    --sandbox --write --out /tmp/sandbox-forecasts
```

`examples/bundle/sandbox-batch.json` is a generated three-question rehearsal
batch — one of each shape — whose rounds are not in the season and whose
deadline is deliberately years out, so a rehearsal never turns into a
demonstration of a late submission. `--sandbox` refuses to write into
`forecasts/`: a rehearsal that can accidentally enter the season is not a
rehearsal.

To see a real week instead:

```bash
python tools/make_bundle.py --list
python tools/make_bundle.py --batch batch-2026-09-14 --out /tmp/real.json
```

`make_bundle.py` reads `questions/season0.json` and never writes it. It refuses
a batch from before the cutover: those rounds each carried their own deadline,
so there is no single moment to put in the `deadline` field.

## Handing the file over

`POST /api/v1/bundle-submissions` accepts the response and answers with the
receipt and the same per-round verdicts `tools/validate_bundle.py` shows you.
Authenticate with the upload token the maintainers install for your entrant id:

```bash
curl -X POST https://social-simulation-arena.com/api/v1/bundle-submissions \
  -H "Authorization: Bearer $SSA_UPLOAD_TOKEN" \
  -H "Content-Type: application/json" \
  --data-binary @RESPONSE.json
```

Uploading the same bytes twice returns the first receipt rather than filing
twice. A `401` means the token or the entrant id is wrong, and says no more
than that. A `403` means your registration is revoked, `404` that no bundle is
published for that batch, and `422` names the field. A `503` is ours, not
yours, and it is what you get today: production has no private store connected
yet. An upload after the deadline still receipts, with nothing accepted and
every answer marked `late`, so you can see what happened.

The endpoint stores your response for review; it does not file the records.
That step stays a pull request, which `tools/accept_bundle.py --write` produces
for you:

```bash
python tools/accept_bundle.py RESPONSE.json --bundle BUNDLE.json --write
python tools/validate_submission.py forecasts/<round_id>/<entrant_id>.json
```

Nothing is written without `--write`; the common use of this command is asking
what *would* happen, and a read-only-looking invocation that files a whole
batch is one that files forecasts by accident.

Confirm the route with the maintainers before a scored entry. Registration
acceptance is separate from technical validity: a payload can be perfect and
still not be admitted.

## Seeing the result

A round's outcome, the rule it resolved under, and every entrant's answer
sorted by error are on the
[rounds table](https://social-simulation-arena.com/leaderboard.html#rounds) as
soon as it resolves — no waiting for the end of the season, and no need to take
a rank on trust. `docs/participant-quickstart.md` lists the four boards and
what each one measures.

## Privacy and retention

Public: your entrant id, name, method, forecast files, their canonical hashes,
lock records, and post-resolution scores.

Not public: contact details, endpoint URLs, credentials, and private review
records. An accepted upload is kept whole, `notes` included, in a private
repository, one file per upload, because a receipt nobody can check against
the bytes it receipted is not a receipt. Credentials are never in Git, never
in an issue, never in an email, and never in a forecast's `notes`. They are
deleted on revocation and can be rotated without changing your entrant id.
The fuller retention design is in
[`docs/submission-design.md`](submission-design.md).

Setting `"status": "revoked"` in `entrants/<entrant_id>.json` stops both routes
at once: the bundle intake refuses an upload before issuing a receipt, and
`tools/probe_agent_api.py` refuses to call the endpoint.

## Getting help

Open an issue with your route, entrant id, batch id, the validator output, and
the response sha256 from your receipt. Never paste a credential into an issue,
a pull request, a forecast's `notes`, or a chat transcript.
