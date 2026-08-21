"""WSGI entrypoint for the questionnaire manifest and private intake API."""

import json
import os
from http import HTTPStatus
from urllib.parse import urlparse

from ssa.questionnaire_api import (
    IdempotencyConflict,
    MAX_BODY_BYTES,
    StorageUnavailable,
    SubmissionError,
    build_manifest,
    store_submission,
    validate_submission,
)


MANIFEST_PATH = "/api/v1/questionnaire"
SUBMISSION_PATH = "/api/v1/questionnaire-submissions"
# Vercel rewrites both public endpoints to this function path. Depending on
# the runtime adapter, PATH_INFO may contain either the source or destination.
FUNCTION_PATH = "/api/app"


def _origin_allowed(origin, host):
    if not origin:
        return True
    allowed = {
        value.strip() for value in
        os.environ.get("SUBMISSION_ALLOWED_ORIGINS", "").split(",")
        if value.strip()
    }
    parsed = urlparse(origin)
    return origin in allowed or parsed.netloc == host


def _json(start_response, status, payload, headers=None):
    body = json.dumps(payload, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")
    response_headers = [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Content-Length", str(len(body))),
    ]
    response_headers.extend(headers or [])
    start_response(
        f"{status} {HTTPStatus(status).phrase}", response_headers)
    return [body]


def _submission_headers(origin):
    headers = [("Cache-Control", "no-store")]
    if origin:
        headers.extend([
            ("Access-Control-Allow-Origin", origin),
            ("Vary", "Origin"),
        ])
    return headers


def _submission_response(environ, start_response):
    origin = environ.get("HTTP_ORIGIN")
    host = environ.get("HTTP_HOST", "")
    headers = _submission_headers(origin if _origin_allowed(origin, host)
                                  else None)
    if not _origin_allowed(origin, host):
        return _json(start_response, 403, {"error": {
            "code": "origin_not_allowed",
            "message": "This browser origin is not allowed to submit.",
        }}, headers)

    content_type = environ.get("CONTENT_TYPE", "").split(";", 1)[0]
    if content_type.lower() != "application/json":
        return _json(start_response, 415, {"error": {
            "code": "unsupported_media_type",
            "message": "Content-Type must be application/json.",
        }}, headers)
    try:
        length = int(environ.get("CONTENT_LENGTH", "0"))
    except ValueError:
        length = 0
    if length <= 0 or length > MAX_BODY_BYTES:
        status = 413 if length > MAX_BODY_BYTES else 400
        return _json(start_response, status, {"error": {
            "code": "invalid_body_size",
            "message": f"Request body must contain 1 to {MAX_BODY_BYTES} bytes.",
        }}, headers)

    try:
        envelope = json.loads(environ["wsgi.input"].read(length))
        validated = validate_submission(envelope)
        receipt = store_submission(
            validated, environ.get("HTTP_IDEMPOTENCY_KEY", ""))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _json(start_response, 400, {"error": {
            "code": "invalid_json",
            "message": "Request body is not valid JSON.",
        }}, headers)
    except IdempotencyConflict as error:
        return _json(start_response, 409, {"error": {
            "code": error.code,
            "message": str(error),
            "details": error.details,
        }}, headers)
    except SubmissionError as error:
        return _json(start_response, 422, {"error": {
            "code": error.code,
            "message": str(error),
            "details": error.details,
        }}, headers)
    except StorageUnavailable as error:
        return _json(start_response, 503, {"error": {
            "code": "submission_storage_unavailable",
            "message": str(error),
        }}, headers)
    return _json(start_response,
                 200 if receipt["idempotent_replay"] else 201,
                 receipt, headers)


def _options(environ, start_response, submission=False):
    origin = environ.get("HTTP_ORIGIN")
    host = environ.get("HTTP_HOST", "")
    if submission and not _origin_allowed(origin, host):
        start_response("403 Forbidden", [("Content-Length", "0")])
        return [b""]
    headers = [
        ("Allow", "POST, OPTIONS" if submission else "GET, OPTIONS"),
        ("Access-Control-Allow-Methods",
         "POST, OPTIONS" if submission else "GET, OPTIONS"),
        ("Content-Length", "0"),
    ]
    if submission:
        headers.append(("Access-Control-Allow-Headers",
                        "Content-Type, Idempotency-Key"))
    if origin and submission:
        headers.extend([
            ("Access-Control-Allow-Origin", origin),
            ("Vary", "Origin"),
        ])
    else:
        headers.append(("Access-Control-Allow-Origin", "*"))
    start_response("204 No Content", headers)
    return [b""]


def application(environ, start_response):
    """Dispatch the two public endpoints without a framework dependency."""
    path = environ.get("PATH_INFO", "")
    method = environ.get("REQUEST_METHOD", "GET").upper()
    is_function_path = path in {FUNCTION_PATH, FUNCTION_PATH + ".py"}

    if method == "GET" and (path == MANIFEST_PATH or is_function_path):
        return _json(start_response, 200, build_manifest(), [
            ("Cache-Control", "public, max-age=30, s-maxage=30"),
            ("Access-Control-Allow-Origin", "*"),
        ])
    if method == "POST" and (path == SUBMISSION_PATH or is_function_path):
        return _submission_response(environ, start_response)
    if method == "OPTIONS" and path == MANIFEST_PATH:
        return _options(environ, start_response)
    if method == "OPTIONS" and (path == SUBMISSION_PATH or is_function_path):
        return _options(environ, start_response, submission=True)
    return _json(start_response, 404, {"error": {
        "code": "not_found",
        "message": "API route not found.",
    }}, [("Cache-Control", "no-store")])

