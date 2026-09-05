"""Route B: the authenticated upload that turns a bundle response into a receipt.

`tools/accept_bundle.py` does the same work from argv and files the resulting
records. This does it from a request and stores the packet instead: filing
stays a commit a person reads, because Vercel's filesystem is ephemeral and
`forecasts/` lives in git.

Every value the request supplies is type-checked before it reaches a regex, a
path join, `compare_digest` or `os.environ`. The two helpers this borrows look
safe and are not: `participants.registration` tests `_ENTRANT_ID.match(entrant
or "")`, whose cushion catches only falsy non-strings, and `re.fullmatch` has
no cushion at all. Either one reached with the wrong type is a 500.
"""
from __future__ import annotations

import hmac
import json
import os
import re
from typing import Any

from ssa import bundle, participants
from ssa.questionnaire_api import StorageUnavailable, store_record

UPLOAD_KEY_PREFIX = "SSA_UPLOAD_KEY_"
RECORD_VERSION = "ssa-bundle-response-v1"
BUNDLE_DIR = os.path.join(bundle.ROOT, "questions", "bundles")
# The response schema's own pattern rather than a third copy of it, applied
# before batch_id becomes a path component. `match` would accept a trailing
# newline, so every use is `fullmatch`.
BATCH_ID = re.compile(bundle.load_schema("bundle_response.schema.json")
                      ["properties"]["batch_id"]["pattern"])


def upload_key_env(entrant: str) -> str:
    """The variable holding this entrant's upload token.

    A separate secret from `participants.key_env`, which is the participant's
    own key and travels to their server on every Route A call.
    """
    return UPLOAD_KEY_PREFIX + re.sub(r"[^A-Z0-9]", "_", entrant.upper())


def _fail(status: int, code: str, message: str) -> tuple[int, dict[str, Any]]:
    return status, {"error": {"code": code, "message": message}}


def _authenticate(entrant_id: Any, token: Any,
                  entrants_dir: str | None) -> dict[str, Any] | None:
    if not isinstance(entrant_id, str) or not isinstance(token, str):
        return None
    try:
        entrant = participants.registration(entrant_id, entrants_dir)
    except ValueError:
        entrant = None
    expected = (os.environ.get(upload_key_env(entrant_id)) or "").strip()
    if expected and isinstance(entrant, dict):
        # A token installed by hand. `compare_digest` refuses a non-ASCII
        # `str`, and a plain `encode` refuses a lone surrogate, which a token
        # can carry and `os.environ` produces for any byte the platform could
        # not decode.
        if hmac.compare_digest(token.encode("utf-8", "surrogatepass"),
                               expected.encode("utf-8", "surrogatepass")):
            return entrant
        return None
    # Otherwise the registry: the token minted at registration. A registry
    # that is not configured or cannot be reached is "no token", never an
    # open door. A registration the cron has not yet written to entrants/
    # is still a registration; its public record comes from the registry.
    try:
        from ssa import registry
        store = registry.Store()
        if not registry.authenticate(store, entrant_id, token):
            return None
        if not isinstance(entrant, dict):
            record, _ = registry.load(store, entrant_id)
            entrant = registry.entrant_file(record) if record else None
    except Exception:
        return None
    return entrant if isinstance(entrant, dict) else None


def accept_upload(body: Any, token: Any, now=None, entrants_dir=None,
                  bundles_dir=None) -> tuple[int, dict[str, Any]]:
    """The status and JSON body for one upload. Raises nothing."""
    if not isinstance(body, dict):
        return _fail(422, "invalid_response", "Body must be a JSON object.")
    entrant = _authenticate(body.get("entrant_id"), token, entrants_dir)
    if entrant is None:
        # One answer for every credential failure, so none is an oracle.
        return _fail(401, "unauthorized", "Unknown entrant or bearer token.")

    batch_id = body.get("batch_id")
    if not isinstance(batch_id, str) or not BATCH_ID.fullmatch(batch_id):
        return _fail(422, "invalid_batch_id",
                     "batch_id must name a published batch.")
    path = os.path.join(bundles_dir or BUNDLE_DIR, batch_id + ".json")
    if not os.path.isfile(path):
        return _fail(404, "unknown_batch", f"No published bundle {batch_id}.")
    try:
        with open(path, encoding="utf-8") as handle:
            questions = json.load(handle)
        problems = bundle.check_bundle(questions)
    except (OSError, ValueError):
        problems = ["unreadable"]
    if problems:
        # The arena's own file, so this is not something the participant can
        # act on.
        return _fail(503, "bundle_unpublishable",
                     f"The published bundle {batch_id} is not usable.")

    try:
        outcome = bundle.normalise(body, questions, now=now, entrant=entrant)
    except bundle.BundleError as error:
        status = 403 if error.code == "entrant_revoked" else 422
        return _fail(status, error.code, str(error))

    receipt = outcome["receipt"]
    record = {
        "record_version": RECORD_VERSION,
        "receipt_hash": receipt["response_sha256"],
        "received_at": receipt["received_at"],
        "receipt": receipt,
        "results": outcome["results"],
        "response": body,
    }
    pathname = (f"bundles/{batch_id}/{receipt['entrant_id']}"
                f"/{receipt['response_sha256']}.json")
    try:
        stored = store_record(pathname, bundle.canonical(record),
                              receipt["response_sha256"]) or record
        return 200, {"receipt": stored["receipt"],
                     "results": stored["results"]}
    except (StorageUnavailable, OSError, ValueError, KeyError):
        # A store that will not answer, and a stored record we cannot read, are
        # both storage faults rather than bad requests. The message is ours, not
        # `StorageUnavailable`'s, which names a private variable and asks the
        # reader to set it.
        return _fail(503, "submission_storage_unavailable",
                     "Submission storage is not available. Try again later.")
