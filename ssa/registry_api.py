"""KEPT, NOT WIRED (Season 0, issue #81): no `vercel.json` rewrite points here
and `api/registrations.py` is gone. See the note atop `ssa/registry.py`.

The registration endpoint's logic, as one function the Vercel handler and
the tests both call: (method, entrant id from the path, bearer token, body)
in, (status, JSON) out. Raises nothing; every refusal is a status and a code.

    POST  /api/v1/registrations          register; returns the token once
    GET   /api/v1/registrations/<id>     the registration, with the token
    PATCH /api/v1/registrations/<id>     edit fields, set or clear the key,
                                         or {"rotate_token": true}
"""
from __future__ import annotations

from typing import Any

from ssa import registry

PUBLIC_FIELDS = ("entrant_id", "name", "type", "method", "contact", "url")


def _err(status: int, code: str, message: str) -> tuple[int, dict[str, Any]]:
    return status, {"error": {"code": code, "message": message}}


def handle(method: str, entrant_id: str | None, token: str | None,
           body: Any, entrants_dir: str | None = None,
           store: registry.Store | None = None) -> tuple[int, dict[str, Any]]:
    try:
        store = store or registry.Store()
    except registry.RegistryError as err:
        return _err(err.status, err.code, str(err))
    try:
        if method == "POST" and not entrant_id:
            if not isinstance(body, dict):
                return _err(422, "invalid_body", "Body must be a JSON object.")
            record, new_token = registry.register(
                store, body, body.get("invitation_code"), entrants_dir)
            return 201, {"registration": registry.public_view(record),
                         "token": new_token,
                         "note": ("Keep the token: it is shown once. It "
                                  "uploads your bundles and edits this "
                                  "registration.")}
        if not entrant_id:
            return _err(405, "method_not_allowed", "POST to register.")
        if method == "GET":
            if not registry.authenticate(store, entrant_id, token):
                return _err(401, "unauthorized", "Unknown entrant or token.")
            record, _ = registry.load(store, entrant_id)
            secret, _ = store.get(f"secrets/{entrant_id}.json")
            return 200, {"registration": registry.public_view(record, secret)}
        if method in ("PATCH", "PUT"):
            if not isinstance(body, dict):
                return _err(422, "invalid_body", "Body must be a JSON object.")
            if body.get("rotate_token") is True:
                new_token = registry.rotate_token(store, entrant_id, token)
                return 200, {"token": new_token,
                             "note": "The previous token no longer works."}
            record = registry.update(store, entrant_id, token, body)
            secret, _ = store.get(f"secrets/{entrant_id}.json")
            return 200, {"registration": registry.public_view(record, secret)}
        return _err(405, "method_not_allowed", "GET, PATCH or POST.")
    except registry.RegistryError as err:
        return _err(err.status, err.code, str(err))
    except (OSError, ValueError, KeyError):
        return _err(503, "registry_unavailable",
                    "The registry is not available. Try again later.")
