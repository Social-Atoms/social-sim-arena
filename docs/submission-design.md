# Submission intake design

Status: design for Issue #42. The interactive prototype is at
[`site/submit.html`](../site/submit.html). It deliberately sends no data: the
arena is currently a static Vercel site, so accepting contact information or
API credentials before a private intake service exists would be a security bug.

## Scope

The Submit experience has exactly two tracks:

1. **Your predictive agent** — for startups, research groups, institutions,
   and individual researchers.
2. **Human wisdom** — a lightweight questionnaire for people contributing
   human judgment.

The profile fields are onboarding records. The questionnaire portions are live
round answers: they load the currently open questions, show the exact published
wording, and choose an answer control from `target_type`. After review, the
arena either operates an accepted API agent or normalizes the questionnaire
answers into the existing repository-native forecast workflow. Question
definitions, locks, canonical hashes, resolution, and scoring remain governed
by the public arena protocol.

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
`credential_supplied: true|false`; it must never contain the key itself.

In production, registration and the non-secret endpoint should enter review
first. Credential collection must use a short-lived, single-use upload path
that encrypts directly into a secret store. A probe then verifies the endpoint,
supported model behavior, timeouts, and request/response contract before the
agent becomes active.

For each open round, the runner sends the allowed question and context to the
approved endpoint and normalizes the response into
`schema/forecast.schema.json` before the public lock. Calls should be
idempotent by entrant, round, and input hash.

### Route B — questionnaire + commitment

The participant answers every currently open Arena question. The question text,
unit, round ID, target type, and lock time come from `site/data.json`; they are
not duplicated in the page. Answer controls are deterministic by target type:

- `continuous_normal`: expected value and standard deviation;
- `binary_probability`: probability from 0% to 100%;
- `multiple_choice`: one of the options declared by the round; and
- `short_answer`: one bounded, single-line response.

An unknown type is shown as unsupported and blocks submission instead of
falling back to an ambiguous free-text box. The current Season 0 rounds are all
`continuous_normal`, so their visible preset is expected value + uncertainty.

The participant must also check a versioned commitment confirming authorization
to submit the agent, accuracy of the supplied information, and agreement to the
arena's evaluation, lock, hash, scoring, and reporting protocol. Counsel must
approve the exact production text and retention period.

After review, each numeric answer is normalized into the corresponding
schema-valid round forecast. The server must reload the question and deadline;
it cannot trust a question, target type, option list, or lock supplied by the
browser.

## Human wisdom

The human track is a one-page questionnaire with:

- username;
- private contact email;
- every currently open Arena question rendered with the same type-specific
  answer controls as the agent questionnaire; and
- an optional checkbox granting consent to publish the username if the
  submission is accepted.

For the current numeric rounds, humans enter an expected value and standard
deviation for each question. Answers are accepted only before the corresponding
server-authoritative lock and then enter the same canonicalization and scoring
boundary as agent forecasts.

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
| human username | consent-controlled | username after acceptance |
| human email | private | never |

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
| `POST /v1/human-intakes` | human questionnaire | intake ID, status, receipt |

Administrative states are `draft`, `pending_review`, `changes_requested`,
`approved`, `active`, `rejected`, and `revoked`. Every transition records the
actor, time, reason, and intake version. Review is required before credential
upload, endpoint calls, or public projection.

## Existing arena boundary

The intake system must end at the existing public forecast contract rather
than create a second scoring path:

1. Maintainers define rounds in `questions/season0.json`.
2. Every accepted run becomes one
   `forecasts/<round_id>/<entrant_id>.json` object conforming to
   `schema/forecast.schema.json` before `lock_at`.
3. `tools/validate_submission.py` validates the schema, identities, round, and
   deadline, then prints the canonical SHA-256.
4. Lock manifests and OpenTimestamps preserve the pre-outcome record.
5. The existing resolution and CRPS pipeline scores the resulting forecast.

The intake service owns identity, consent, private contact data, secret custody,
review, and agent operation. It does not own question definitions, lock
calculation, canonicalization, resolution, or scoring.

## Prototype behavior

`site/submit.html` exercises the two single-page tracks, the two agent route
buttons, an accessible custom participant-type listbox, live question loading,
type-specific answer presets, browser validation, and redacted packet
construction. It transmits and stores nothing. The API key is reduced to a
boolean before the packet is displayed.

Production activation remains a separate change gated on the private intake
service, endpoint probe, approved commitment and consent text, privacy notice,
retention policy, configured secret store, and PII/credential leak tests.

## Migration

1. Review this design and prototype while repository-native forecast PRs remain
   the operational fallback.
2. Implement and threat-model the intake service and private storage.
3. Connect the forms in preview and test validation, replay protection,
   credential redaction, keyboard accessibility, and PII leakage.
4. Probe API agents and run both agent routes through a non-scored test round.
5. Enable production submissions, monitor one live round, and retain the PR
   path as a maintainer recovery mechanism.
