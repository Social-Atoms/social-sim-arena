# Submission intake design

Status: design for Issue #42. The interactive prototype is at
[`site/submit.html`](../site/submit.html). Review packets are local. Its
submitter-initiated API test sends one non-scored request directly to the URL
entered by the submitter, and Human Wisdom drafts may be saved on that device
without the email address. Production intake still requires a private service.

## Scope

The Submit experience has exactly two tracks:

1. **Your predictive agent** — for startups, research groups, institutions,
   and individual researchers.
2. **Human wisdom** — a lightweight questionnaire for people contributing
   human judgment.

The profile fields are onboarding records. The questionnaire portions are live
round answers: they load the currently open questions, show the exact published
wording, and choose an answer control from `target_type`. After review, the
arena either operates an accepted API agent or normalizes the agent
questionnaire into the repository-native forecast workflow. Human Wisdom uses
a separate point-answer contract and separate boards. Both tracks share only
the public questions, server-authoritative locks, and eventual resolutions.

## Your predictive agent

Every participant supplies:

- participant type: startup, research group, institution, or individual
  researcher;
- organization name;
- product / agent name;
- primary contact name;
- contact email; and
- explicit consent to publish the organization name and product / agent name
  if the submission is accepted.

The contact name and email remain private. The two names are public only after
the submitter checks the publication checkbox and the arena accepts the entry.
If the box is left unchecked, the submission can still be reviewed but those
names are not projected publicly.

The participant then chooses exactly one of two routes.

### Route A — OpenAI-compatible API

The participant provides an HTTPS OpenAI-compatible URL and, when the endpoint
requires authentication, an optional API key. The review packet contains only
`credential_supplied: true|false`; it must never contain the key itself. The
participant must pass the non-scored **Test connection** probe before the API
route can produce a review packet.

In production, registration and the non-secret endpoint should enter review
first. Credential collection must use a short-lived, single-use upload path
that encrypts directly into a secret store. A probe then verifies the endpoint,
supported model behavior, timeouts, and request/response contract before the
agent becomes active. The browser probe is preliminary because it may require
CORS; the server-side probe is authoritative.

For each open round, the runner sends the allowed question and context to the
approved endpoint and normalizes the response into
`schema/forecast.schema.json` before the public lock. Calls should be
idempotent by entrant, round, and input hash.

The complete request/response, authentication, timing, timeout, retry, trace,
and crosstab contract is in [`docs/agent-api.md`](agent-api.md). It intentionally
reuses the current runner: calls begin 72–48 hours before lock, use 15-second
connect and 600-second read timeouts, and missing forecasts are retried by the
scheduled refresh until the 30-minute lock margin. A valid in-window forecast
is final. A dependency-free example server, sample request/response, and curl
test live under `examples/agent-api/`.

The optional `reasoning_trace` is archived privately even while unscored. The
optional `crosstabs` object may contain only subgroup dimensions and cells
declared by the round. These fields are in version 1 so adding later scoring
does not break participant endpoints.

### Route B — questionnaire + commitment

The participant answers every currently open Arena question. The question text,
unit, round ID, target type, and lock time come from `site/data.json`; they are
not duplicated in the page. Answer controls are deterministic by target type:

- `continuous_normal`: expected value and standard deviation;
- `profile_energy`: expected value and standard deviation for every declared
  profile cell, submitted as one complete joint profile;
- `ranking_list`: one single-line field per rank, with exact length, uniqueness,
  and any fixed basket enforced;
- `binary_probability`: probability from 0% to 100%;
- `multiple_choice`: one of the options declared by the round; and
- `short_answer`: one bounded, single-line response.

An unknown type is shown as unsupported and blocks submission instead of
falling back to an ambiguous free-text box. Season 0 currently contains scalar,
joint-profile, and ordered-ranking rounds, so all three live protocol formats
have explicit questionnaire controls.

The participant must also check a versioned commitment confirming authorization
to submit the agent, accuracy of the supplied information, and agreement to the
arena's evaluation, lock, hash, scoring, and reporting protocol. Counsel must
approve the exact production text and retention period.

After review, each numeric answer is normalized into the corresponding
schema-valid round forecast. The server must reload the question and deadline;
it cannot trust a question, target type, option list, or lock supplied by the
browser.

## Human wisdom

Human Wisdom is not a simplified agent forecast. A participant supplies a
username and private email, then chooses exactly one board: `topline`,
`profile`, or `ranking`. The board is the submission unit. The draft freezes
the open round IDs on that board, shows no questions from other boards, and may
be saved and resumed. A person can later start a separate submission for a
different board.

The UI supplies the question wording, unit, lock, resolution source, and the
latest published persistence reference when one exists. It never pre-fills an
answer. Human controls and losses are deliberately simpler than agent ones:

| round type | human answer | human loss |
|---|---|---|
| `continuous_normal` | one point estimate | absolute error |
| `binary_probability` | one `Yes` / `No` choice | 0/1 loss |
| `multiple_choice` | one declared option | 0/1 loss |
| `profile_energy` | one point per declared cell | profile RMSE |
| `ranking_list` | one ordered list | Kendall or RBO, as declared by the round |
| `short_answer` | one bounded line | archived, unscored until a rule is declared |

Human answers conform to `schema/human-intake.schema.json` and are scored by
`ssa/human_scoring.py`. They never acquire a fabricated standard deviation,
never normalize into `schema/forecast.schema.json`, and never appear on an
agent leaderboard. Human boards report their own raw loss, persistence-relative
skill, resolved count, and coverage.

The prototype's explicit **Save draft** action stores the selected board,
frozen round IDs, username, and partial answers in local browser storage; it
does not store email. Production save-and-resume uses an email magic link and
private storage, not browser storage.

## Data classification

| Field | Class | Public projection |
|---|---|---|
| participant type | private review metadata | none by default |
| organization and product / agent names | consent-controlled | both names after acceptance |
| primary contact name and email | private | never |
| OpenAI-compatible URL | private operational data | never |
| API key | secret | never; secret-store reference only |
| questionnaire answers | private before acceptance | normalized forecast after acceptance |
| agent commitment record | private | terms version or audit hash only |
| API reasoning trace | private artifact | hash/reference only by default |
| human username | consent-controlled | username after acceptance |
| human email | private | never |
| human point answers | private before lock | Human Wisdom board after resolution |

Private data is retained only for the documented review, active-season, and
dispute windows, then deleted or irreversibly anonymized. Credentials are
deleted on revocation and rotated without exposing their values to operators.

## Intake service boundary

The static prototype must not receive a production form action until the
service has TLS, CSRF/origin checks, rate limits, bot protection, redacted audit
logging, encrypted storage, an approved privacy notice, and access controls.

Suggested endpoints:

| Endpoint | Purpose | Response |
|---|---|---|
| `POST /v1/participant-intakes` | participant profile + one route, never the key | intake ID, status, receipt |
| `PUT /v1/participant-intakes/{id}/credential` | single-use encrypted key upload | credential version only |
| `POST /v1/participant-intakes/{id}/probe` | authoritative non-scored API contract test | typed pass/fail result |
| `POST /v1/human-submissions` | create one board draft and frozen manifest | submission ID, resume receipt |
| `GET /v1/human-submissions/{id}` | resume through magic-link authentication | current draft and locks |
| `PUT /v1/human-submissions/{id}/answers/{round_id}` | save one answer | draft version |
| `POST /v1/human-submissions/{id}/finalize` | finalize the complete board manifest | immutable receipt |

Administrative states are `draft`, `pending_review`, `changes_requested`,
`approved`, `active`, `rejected`, and `revoked`. Every transition records the
actor, time, reason, and intake version. Review is required before credential
upload, endpoint calls, or public projection.

## Arena boundaries

Predictive agents continue to end at the existing public forecast contract:

1. Maintainers define rounds in `questions/season0.json`.
2. Every accepted run becomes one
   `forecasts/<round_id>/<entrant_id>.json` object conforming to
   `schema/forecast.schema.json` before `lock_at`.
3. `tools/validate_submission.py` validates the schema, identities, round, and
   deadline, then prints the canonical SHA-256.
4. Lock manifests and OpenTimestamps preserve the pre-outcome record.
5. The existing resolution and CRPS pipeline scores the resulting forecast.

Human Wisdom deliberately ends at its separate submission schema and scorer.
It reuses the round definitions, locks, and resolution values, but not agent
canonicalization or agent scoring. This separation prevents Human point choices
from being represented as probabilistic model forecasts.

The intake service owns identity, consent, private contact data, secret custody,
review, agent operation, and Human draft state. It does not own question
definitions, lock calculation, resolution, or public score computation.

## Prototype behavior

`site/submit.html` exercises the two single-page tracks, the two agent route
buttons, an accessible custom participant-type listbox, live question loading,
board selection, separate Human answer presets, local save-and-resume, the
non-scored endpoint test, browser validation, and redacted packet construction.
The API key is sent only to the endpoint chosen for the explicit test and is
reduced to a boolean before the packet is displayed. Human draft storage omits
the contact email.

Production activation remains a separate change gated on the private intake
service, endpoint probe, approved commitment and consent text, privacy notice,
retention policy, configured secret store, and PII/credential leak tests.

## Migration

1. Review this design and prototype while repository-native forecast PRs remain
   the operational fallback.
2. Validate the API contract and starter server, then run accepted endpoints
   through the non-scored fixture.
3. Implement and threat-model the intake service, secret storage, Human magic
   links, and draft versioning.
4. Add Human board projections and shadow-score hand-checked fixtures without
   publishing standings.
5. Connect production forms and test lock enforcement, retry/idempotency,
   credential redaction, accessibility, and PII leakage.
6. Enable production submissions, monitor one live round, and retain the PR
   path as a maintainer recovery mechanism.
