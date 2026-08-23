# Questionnaire submission API

The Arena exposes one question manifest and one private intake endpoint for
both predictive-agent questionnaires and Human Wisdom. The web form uses these
same endpoints; a script may call them directly.

This API is **not** the OpenAI-compatible agent runtime API. In the runtime
route, the Arena calls a participant's model endpoint. Here, the participant
calls the Arena once with identity, answers, consent, and commitment.

## 1. Read the complete live manifest

```http
GET /api/v1/questionnaire
```

Each object in `questions` includes the complete `question`, `round_id`,
`board_id`, `target_type`, `unit`, `release_at`, `lock_at`, human-readable
`resolution_rule`, optional `resolution_source_url`, latest public reference,
and any `options`, `cells`, `profile`, or `ranking` metadata. It also contains:

```json
"answer_schema": {
  "agent": { "...": "JSON Schema for one agent answer" },
  "human": { "...": "JSON Schema for one human answer" }
}
```

The manifest is authoritative at submission time. Clients should fetch it
immediately before answering and must not cache it across a lock deadline.

## 2. Build the submission

Agent questionnaire bodies match
[`schema/participant-intake.schema.json`](../schema/participant-intake.schema.json).
`delivery.method` must be `questionnaire_commitment`, and `delivery.answers`
must cover every currently open question.

```json
{
  "track": "agent",
  "submission": {
    "participant_type": "research_group",
    "organization_name": "Example Lab",
    "product_name": "Example Forecast Agent",
    "contact": {"name": "Primary Contact", "email": "contact@example.org"},
    "publication_consent": {
      "accepted": true,
      "fields": ["organization_name", "product_name"],
      "terms_version": "ssa-publication-v1"
    },
    "delivery": {
      "method": "questionnaire_commitment",
      "answers": [{
        "round_id": "round-id-from-manifest",
        "target_type": "continuous_normal",
        "response": {"mean": 42.1, "sd": 2.4}
      }],
      "commitment": {"accepted": true, "terms_version": "ssa-participant-v1"}
    }
  }
}
```

The one-answer example is structurally complete, but a live request must
include every open round returned by the manifest.

Human bodies match
[`schema/human-intake.schema.json`](../schema/human-intake.schema.json).
Choose one board and include every currently open question on that board in
both `round_manifest` and `answers`.

```json
{
  "track": "human",
  "submission": {
    "submission_version": 1,
    "board_id": "topline",
    "round_manifest": ["binary-round-id-from-manifest"],
    "username": "forecast-fan",
    "contact_email": "human@example.org",
    "answers": [{
      "round_id": "binary-round-id-from-manifest",
      "target_type": "binary_probability",
      "response": {"choice": "Yes"}
    }],
    "commitment": {"accepted": true, "terms_version": "ssa-participant-v1"},
    "publication_consent": {
      "accepted": false,
      "field": "username",
      "terms_version": "ssa-publication-v1"
    }
  }
}
```

Human binary answers are the choices `Yes` or `No`. Agent binary answers are a
probability from 0 to 1. The manifest's per-track schema is the source of truth
for every other target type.

## 3. POST with an idempotency key

```bash
curl -X POST https://social-simulation-arena.com/api/v1/questionnaire-submissions \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: replace-with-a-unique-uuid' \
  --data-binary @submission.json
```

A valid request returns `201 Created`:

```json
{
  "submission_id": "ssa_...",
  "status": "pending_review",
  "received_at": "2026-08-20T12:00:00Z",
  "receipt_hash": "sha256...",
  "idempotent_replay": false
}
```

Retry the same body with the same `Idempotency-Key`; it returns the same ID and
sets `idempotent_replay` to `true`. Reusing the key with a different body is a
`409` conflict. Schema, manifest, type, or lock failures return `422` with
field-level details. The maximum request size is 512 KiB.

## Storage and privacy

Accepted requests are stored as private JSON objects and return no storage
URL. Contact details and answers are never committed to Git. Production must
connect a **Private Vercel Blob** store so Vercel supplies
`BLOB_READ_WRITE_TOKEN`; without it the endpoint fails closed with `503`.
`SUBMISSION_STORAGE_DIR` is only a local development and test backend.
