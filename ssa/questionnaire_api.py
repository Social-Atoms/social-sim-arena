"""Question manifest, validation, and private storage for questionnaire intake.

The browser and programmatic clients use the same contracts.  This module is
deliberately independent of Vercel's request handler so it can be tested
locally and moved to another host without changing the public API.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator, FormatChecker

from . import batches


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "site" / "data.json"
PARTICIPANT_SCHEMA_PATH = ROOT / "schema" / "participant-intake.schema.json"
HUMAN_SCHEMA_PATH = ROOT / "schema" / "human-intake.schema.json"
MANIFEST_VERSION = "ssa-questionnaire-manifest-v1"
SUBMISSION_VERSION = "ssa-questionnaire-submission-v1"
TERMS_VERSION = "ssa-participant-v1"
MAX_BODY_BYTES = 512 * 1024


class SubmissionError(ValueError):
    """A client-visible submission validation error."""

    def __init__(self, message: str, *, code: str = "invalid_submission",
                 details: list[dict[str, str]] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or []


class StorageUnavailable(RuntimeError):
    """No private submission store is configured."""


class IdempotencyConflict(SubmissionError):
    """An idempotency key was reused with a different payload."""

    def __init__(self):
        super().__init__(
            "This Idempotency-Key was already used for a different payload.",
            code="idempotency_conflict",
        )


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def load_arena_data() -> dict[str, Any]:
    return _read_json(DATA_PATH)


def _parse_iso(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _round_deadline(round_data: dict[str, Any]) -> datetime:
    """Published deadline, or the canonical calendar for an older artifact."""
    if round_data.get("deadline"):
        return _parse_iso(round_data["deadline"])
    return batches.effective_deadline(round_data["lock_at"])


def normalize_target_type(value: Any) -> str:
    target_type = str(value or "continuous_normal")
    return {
        "continuous": "continuous_normal",
        "numeric": "continuous_normal",
        "binary": "binary_probability",
    }.get(target_type, target_type)


def board_for_round(round_data: dict[str, Any]) -> str:
    target_type = normalize_target_type(round_data.get("target_type"))
    if target_type == "profile_energy":
        return "profile"
    if target_type == "ranking_list":
        return "ranking"
    return "topline"


def _schema_branches(path: Path) -> dict[str, dict[str, Any]]:
    schema = _read_json(path)
    branches = schema["definitions"]["questionnaire_answer"]["oneOf"]
    return {
        branch["properties"]["target_type"]["const"]: branch
        for branch in branches
    }


def _latest_public_reference(round_data: dict[str, Any]) -> dict[str, Any] | None:
    persistence = (round_data.get("baselines") or {}).get("persistence")
    if not persistence:
        profile = round_data.get("profile") or {}
        persistence = (profile.get("baselines") or {}).get("persistence")
    if not persistence:
        ranking = round_data.get("ranking") or {}
        persistence = (ranking.get("baselines") or {}).get("persistence")
    if not persistence:
        return None
    return persistence


def open_rounds(data: dict[str, Any], now: datetime | None = None
                ) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    rounds = [
        round_data for round_data in data.get("rounds", [])
        if round_data.get("status") == "open"
        # `status` comes from a six-hourly artifact and can be stale. Enforce
        # the published batch deadline again on the serving path; old artifacts
        # without the new field are derived from the same canonical calendar.
        and _round_deadline(round_data) > now
    ]
    return sorted(
        rounds,
        key=lambda item: (_round_deadline(item), item["lock_at"],
                          item["round_id"]))


def build_manifest(data: dict[str, Any] | None = None,
                   now: datetime | None = None) -> dict[str, Any]:
    if data is None:
        data = load_arena_data()
    now = now or datetime.now(timezone.utc)
    agent_schemas = _schema_branches(PARTICIPANT_SCHEMA_PATH)
    human_schemas = _schema_branches(HUMAN_SCHEMA_PATH)
    questions = []
    for round_data in open_rounds(data, now):
        target_type = normalize_target_type(round_data.get("target_type"))
        question = {
            "round_id": round_data["round_id"],
            "board_id": board_for_round(round_data),
            "question": round_data["question"],
            "target_type": target_type,
            "unit": round_data.get("unit"),
            "release_at": round_data.get("release_at"),
            "deadline": _iso(_round_deadline(round_data)),
            "lock_at": round_data["lock_at"],
            "resolution_rule": round_data.get("resolve"),
            # Season 0 stores a human-readable resolution rule but does not
            # currently declare a canonical source URL as a separate field.
            "resolution_source_url": round_data.get("resolution_source_url"),
            "latest_public_reference": _latest_public_reference(round_data),
            "answer_schema": {
                "agent": agent_schemas.get(target_type),
                "human": human_schemas.get(target_type),
            },
        }
        for field in ("cells", "options", "profile", "ranking"):
            if field in round_data:
                question[field] = round_data[field]
        questions.append(question)
    return {
        "schema_version": MANIFEST_VERSION,
        "generated_at": _iso(now),
        "submission_endpoint": "/api/v1/questionnaire-submissions",
        "commitment": {
            "required_for": ["agent", "human"],
            "terms_version": TERMS_VERSION,
            "text": (
                "I am authorized to make this submission; the information is "
                "accurate; and I agree that accepted answers may be evaluated, "
                "locked, hashed, scored, and reported under the arena protocol."
            ),
        },
        "questions": questions,
    }


def _validation_details(schema: dict[str, Any], body: Any
                        ) -> list[dict[str, str]]:
    validator = Draft7Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(body), key=lambda item: list(item.path))
    return [{
        "path": ".".join(str(part) for part in error.absolute_path) or "$",
        "message": error.message,
    } for error in errors]


def _assert_exact_answers(submission: dict[str, Any], expected: list[str],
                          round_lookup: dict[str, dict[str, Any]]) -> None:
    answers = submission.get("answers")
    if answers is None:
        answers = (submission.get("delivery") or {}).get("answers", [])
    answer_ids = [answer.get("round_id") for answer in answers]
    if len(answer_ids) != len(set(answer_ids)):
        raise SubmissionError("Each round_id may appear only once in answers.")
    if set(answer_ids) != set(expected):
        missing = [round_id for round_id in expected if round_id not in answer_ids]
        extra = [round_id for round_id in answer_ids if round_id not in expected]
        details = []
        if missing:
            details.append({"path": "answers", "message":
                            "Missing round_id values: " + ", ".join(missing)})
        if extra:
            details.append({"path": "answers", "message":
                            "Unexpected round_id values: " + ", ".join(extra)})
        raise SubmissionError(
            "Answers must match the current open-question manifest exactly.",
            details=details,
        )
    for index, answer in enumerate(answers):
        current = round_lookup[answer["round_id"]]
        expected_type = normalize_target_type(current.get("target_type"))
        if answer.get("target_type") != expected_type:
            raise SubmissionError(
                f"answers[{index}].target_type must be {expected_type}.",
                details=[{"path": f"answers.{index}.target_type",
                          "message": f"Expected {expected_type}."}],
            )
        response = answer["response"]
        if expected_type == "multiple_choice" and current.get("options"):
            if response["choice"] not in current["options"]:
                raise SubmissionError(
                    f"answers[{index}] must choose a declared option.",
                    details=[{"path": f"answers.{index}.response.choice",
                              "message": "Allowed: " +
                              ", ".join(current["options"])}],
                )
        if expected_type == "ranking_list":
            spec = current.get("ranking") or {}
            ranking = response["ranking"]
            length = spec.get("length")
            if length and len(ranking) != length:
                raise SubmissionError(
                    f"answers[{index}] must contain exactly {length} ranked items.")
            allowed = spec.get("items")
            if allowed and set(ranking) != set(allowed):
                raise SubmissionError(
                    f"answers[{index}] must rank every declared item exactly once.")
        if expected_type == "profile_energy":
            declared = current.get("cells") or (
                current.get("profile") or {}).get("cells") or []
            if declared and set(response["profile"]) != set(declared):
                raise SubmissionError(
                    f"answers[{index}] must include every declared profile cell exactly once.")


def validate_submission(envelope: Any, data: dict[str, Any] | None = None,
                        now: datetime | None = None) -> dict[str, Any]:
    if not isinstance(envelope, dict):
        raise SubmissionError("Request body must be a JSON object.")
    if set(envelope) != {"track", "submission"}:
        raise SubmissionError(
            "Request body must contain only track and submission.")
    track = envelope.get("track")
    if track not in ("agent", "human"):
        raise SubmissionError("track must be agent or human.")
    submission = envelope.get("submission")
    if not isinstance(submission, dict):
        raise SubmissionError("submission must be a JSON object.")

    schema_path = PARTICIPANT_SCHEMA_PATH if track == "agent" else HUMAN_SCHEMA_PATH
    details = _validation_details(_read_json(schema_path), submission)
    if details:
        raise SubmissionError(
            "Submission does not match the questionnaire contract.",
            details=details,
        )
    if track == "agent" and submission["delivery"]["method"] != "questionnaire_commitment":
        raise SubmissionError(
            "The questionnaire endpoint accepts the questionnaire_commitment agent route only.")

    if data is None:
        data = load_arena_data()
    current_rounds = open_rounds(data, now)
    round_lookup = {item["round_id"]: item for item in current_rounds}
    if track == "agent":
        expected = [item["round_id"] for item in current_rounds]
    else:
        board_id = submission["board_id"]
        expected = [item["round_id"] for item in current_rounds
                    if board_for_round(item) == board_id]
        if set(submission["round_manifest"]) != set(expected):
            raise SubmissionError(
                "round_manifest must match the current open questions on the selected board.",
                details=[{"path": "round_manifest",
                          "message": "Expected: " + ", ".join(expected)}],
            )
    if not expected:
        raise SubmissionError("No questions are currently open for this submission.",
                              code="no_open_questions")
    _assert_exact_answers(submission, expected, round_lookup)
    return {"track": track, "submission": submission}


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _submission_record(validated: dict[str, Any], idempotency_key: str,
                       now: datetime) -> tuple[str, str, dict[str, Any]]:
    payload_hash = hashlib.sha256(canonical_json(validated)).hexdigest()
    key_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    submission_id = "ssa_" + key_hash[:24]
    record = {
        "record_version": SUBMISSION_VERSION,
        "submission_id": submission_id,
        "track": validated["track"],
        "status": "pending_review",
        "received_at": _iso(now),
        "receipt_hash": payload_hash,
        "idempotency_key_hash": key_hash,
        "submission": validated["submission"],
    }
    return submission_id, payload_hash, record


def _store_local(pathname: str, record_bytes: bytes,
                 receipt_hash: str) -> dict[str, Any] | None:
    root = Path(os.environ["SUBMISSION_STORAGE_DIR"]).expanduser().resolve()
    path = root / pathname
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(record_bytes)
        return None
    except FileExistsError:
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get("receipt_hash") != receipt_hash:
            raise IdempotencyConflict()
        return existing


def _store_blob(pathname: str, record_bytes: bytes,
                receipt_hash: str) -> dict[str, Any] | None:
    try:
        from vercel.blob import BlobClient
        from vercel.blob.errors import BlobError
    except ImportError as error:  # pragma: no cover - deployment dependency
        raise StorageUnavailable(
            "The Vercel Blob SDK is not installed.") from error
    client = BlobClient()
    existing = client.get(pathname, access="private", use_cache=False)
    if existing is not None:
        stored = json.loads(bytes(existing).decode("utf-8"))
        if stored.get("receipt_hash") != receipt_hash:
            raise IdempotencyConflict()
        return stored
    try:
        client.put(pathname, record_bytes, access="private",
                   content_type="application/json", add_random_suffix=False,
                   overwrite=False)
    except BlobError:
        # Two identical retries may race between GET and PUT. Re-read after a
        # failed conditional create; preserve other storage failures.
        existing = client.get(pathname, access="private", use_cache=False)
        if existing is None:
            raise
        stored = json.loads(bytes(existing).decode("utf-8"))
        if stored.get("receipt_hash") != receipt_hash:
            raise IdempotencyConflict()
        return stored
    return None


def store_submission(validated: dict[str, Any], idempotency_key: str,
                     now: datetime | None = None) -> dict[str, Any]:
    if not isinstance(idempotency_key, str) or not 8 <= len(idempotency_key) <= 200:
        raise SubmissionError(
            "Idempotency-Key header must contain 8 to 200 characters.",
            code="invalid_idempotency_key",
        )
    now = now or datetime.now(timezone.utc)
    submission_id, receipt_hash, record = _submission_record(
        validated, idempotency_key, now)
    pathname = f"questionnaire-submissions/{submission_id}.json"
    record_bytes = canonical_json(record)
    if os.environ.get("SUBMISSION_STORAGE_DIR"):
        existing = _store_local(pathname, record_bytes, receipt_hash)
    elif os.environ.get("BLOB_READ_WRITE_TOKEN"):
        existing = _store_blob(pathname, record_bytes, receipt_hash)
    else:
        raise StorageUnavailable(
            "Private submission storage is not configured. Connect a private "
            "Vercel Blob store before accepting submissions.")
    stored_record = existing or record
    return {
        "submission_id": submission_id,
        "status": "pending_review",
        "received_at": stored_record["received_at"],
        "receipt_hash": receipt_hash,
        "idempotent_replay": existing is not None,
    }
