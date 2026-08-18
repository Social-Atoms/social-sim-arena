# Submission intake design

Status: design for Issue #42. The interactive prototype is at
[`site/submit.html`](../site/submit.html). It deliberately sends no data: the
arena is currently a static Vercel site, so accepting email addresses, signed
commitments, or API credentials before a private intake service exists would
turn a visual prototype into a security bug.

## What the arena already guarantees

The intake system must end at the existing public forecast contract rather
than create a second scoring path.

1. A maintainer defines a round in `questions/season0.json`: `round_id`,
   tracker/series, question, unit, release time, lock time, and resolution rule.
   Today this is a reviewed repository edit; despite an older note in the file,
   no cron writes new rounds.
2. `ssa.refresh` publishes those definitions and their clock-derived status to
   `site/data.json`. While a round is open it also refreshes the history snapshot
   and the arena-hosted model and baseline forecasts.
3. Every accepted forecast must become exactly one
   `forecasts/<round_id>/<entrant_id>.json` object conforming to
   `schema/forecast.schema.json`. `tools/validate_submission.py` checks the
   schema, path identities, known round, and `now < lock_at`, then prints the
   canonical SHA-256.
4. The last pre-lock history snapshot is frozen. On the first refresh after
   lock, `ssa/stamps.py` writes a manifest containing every forecast's canonical
   hash and submits that manifest to OpenTimestamps.
5. After the published value arrives, `ssa.resolve` records the first eligible
   post-lock observation (or a maintainer records a manual resolution). The
   scoring pipeline applies CRPS and skill against persistence, then rebuilds
   the public leaderboard.

The intake service therefore owns identity, consent, private contact data,
credential custody, review, and timely delivery. It does **not** own question
definitions, lock calculation, canonicalization, resolution, or scoring.

## Two user tracks

### Track A: organizations and researchers

Startups, research groups, institutions, and individual researchers share one
flow. `participant_type` distinguishes them without making four nearly
identical forms.

#### Step 1 — profile

Required fields are participant type, organization/independent-researcher
name, product or agent name, short product description, stable entrant ID,
primary contact name, and contact email. A website is optional.

The submitter explicitly chooses one leaderboard policy:

- `public`: publish the organization and product names.
- `private`: keep identity and contact data private; publish a stable neutral
  label such as `Private entrant · 7F3A`. Scores remain visible so the board
  cannot silently omit an entrant after seeing its result.

The choice is versioned and frozen for each scored season. Changing it later
does not rewrite historical leaderboard labels.

#### Step 2 — choose exactly one delivery method

**Hosted API**

The participant supplies an HTTPS endpoint, agent/version identifier,
authentication mode, integration notes, and an optional credential. Registration
and the non-secret endpoint configuration enter review first. If a credential
is needed, the service returns a short-lived, single-use upload URL only after
approval. The credential is encrypted directly into the secret store; it never
passes through GitHub, an Issue, a PR, analytics, application logs, or the
browser again. A probe verifies the request/response contract before status
becomes `active`.

For every open round the scheduler sends the round definition, unit, permitted
history/context, and response schema. A successful response is normalized into
the existing forecast JSON and submitted before the same lock margin used by
the arena-hosted agents. Retries are idempotent by `(entrant_id, round_id,
input_hash)`.

**Submission file + signed commitment**

The participant selects an open round and uploads one forecast JSON. Client and
server both validate it against `schema/forecast.schema.json`, verify that its
`round_id` and `entrant` match the registration, and reject it at or after
`lock_at`. The submitter then signs the versioned commitment:

> I am authorized to submit this forecast for the named participant and agent.
> The file represents the agent's forecast produced without access to the
> unpublished outcome. I agree that the arena may lock, hash, score, and publish
> the forecast and the participant's chosen public identity under the published
> evaluation protocol.

For the first version, a typed legal name, role, UTC timestamp, terms version,
IP/audit metadata, and explicit checkbox form an electronic signature. Counsel
must approve the exact text and retention period before production. The signed
record stays private; only its SHA-256 and terms version are associated with the
public forecast.

Changing delivery method is allowed only while the intake is `draft` or
`changes_requested`. Once active, a change creates a new reviewed version so an
API entrant cannot silently become a file entrant mid-season.

### Track B: human forecasters

Humans use a short web questionnaire and never need repository access. It asks
for username/display name, contact email, an open round, forecast mean,
uncertainty (`sd`), optional rationale, and explicit consent. Email is private;
individual humans are not leaderboard entrants. The public row remains
`human-crowd`.

Before lock, a person may replace a response by using the signed edit link in
their receipt; the newest accepted version wins. At lock, the worker converts
the set of individual normal distributions into an equal-weight mixture and
publishes fixed quantiles (including the median) as
`forecasts/<round_id>/human-crowd.json`. The notes contain the response count,
terms version, and a batch hash but no username, email, or rationale.

Each accepted response receives a private receipt and canonical hash. At lock,
the sorted receipt hashes form a batch manifest whose public root proves the
aggregate came from pre-lock responses without publishing the people behind
them.

## Data classification

| Field | Class | Public projection |
|---|---|---|
| participant type | public when identity is public | entrant metadata |
| organization and product names | participant choice | names or neutral label |
| product description and website | public when identity is public | entrant metadata |
| entrant ID | public | forecast path and leaderboard key |
| leaderboard policy | private control | resulting label only |
| contact name and email | private | never |
| API endpoint and integration notes | private | never |
| API credential | secret | never; secret-store reference only |
| forecast distribution | public after acceptance | forecast JSON |
| signed commitment | private | SHA-256 + terms version only |
| human username, email, rationale | private | never |
| human aggregate distribution/count | public | `human-crowd` forecast |

Private data is retained for the active season plus the documented dispute
window, then deleted or irreversibly anonymized. Credentials are deleted on
revocation and rotated without copying their value back to an operator.

## Intake service boundary

The static page must not receive production form actions until these endpoints
exist behind TLS, CSRF/origin checks, rate limits, bot protection, audit logging
with field redaction, and encrypted storage:

| Endpoint | Purpose | Response |
|---|---|---|
| `POST /v1/participant-intakes` | profile + one delivery method, never a credential | intake ID, status, receipt |
| `PUT /v1/participant-intakes/{id}/credential` | single-use encrypted credential upload | credential version only |
| `POST /v1/participant-intakes/{id}/forecasts` | file + commitment for one open round | validation result + canonical hash |
| `POST /v1/human-forecasts` | questionnaire response | receipt + private edit link |
| `GET /v1/rounds/open` | server-authoritative open rounds | round IDs, questions, units, locks |

The server reloads the round and lock from the repository or generated data;
it never trusts a client-supplied deadline. File acceptance and the worker that
writes public forecast JSON share the same Python validator/canonicalizer as CI.

Administrative states are `draft`, `pending_review`, `changes_requested`,
`approved`, `active`, `rejected`, and `revoked`. Every transition records actor,
time, reason, and intake version. Review is required before credential upload or
public projection.

## Prototype behavior

`site/submit.html` exercises both responsive flows with real open rounds from
`site/data.json`, validates forecast files, computes the same sorted-key
SHA-256 in the browser, redacts credentials from review, and labels every field
as public, private, or secret. Its final action builds an on-screen review
packet and never transmits or stores the values.

This is intentional. Shipping a static form that appears to accept a secret is
worse than leaving GitHub as the temporary path. Production activation is a
separate change gated on the intake service, approved commitment text, privacy
notice, retention policy, and configured secret store.

## Migration

1. Merge and review this design/prototype while the existing GitHub PR and
   human Issue paths remain the operational fallback.
2. Implement and threat-model the intake service; configure private storage,
   transactional email, secret custody, spam controls, and operator access.
3. Connect the prototype to the versioned endpoints in a preview environment;
   run deadline, replay, credential-redaction, and PII-leak tests.
4. Run both paths for at least one non-scored test round and compare their
   generated forecast bytes and canonical hashes.
5. Enable the new form actions, monitor one live round, then remove the old
   GitHub prefill and human Issue links. Keep repository-native submission as a
   maintainer-only recovery path.
