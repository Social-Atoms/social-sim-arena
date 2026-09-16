"""Validate a participant request Issue and optionally write its entrant file.

The website puts one versioned JSON block in a public GitHub Issue.  This tool
reads only that block, verifies the Issue author against the entrant owner on
``origin/main``, and produces the one registration file a maintainer reviews.
It never calls the submitted endpoint and never executes Issue text.

New registrations stop at ``pending`` until a maintainer applies the
``participant-approved`` label.  Owner-authenticated updates and revocations
are ready immediately.  In all three cases the workflow opens a pull request;
this tool never merges it or closes the Issue.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
MARKER = "<!-- ssa-participant-request-v1 -->"
STATUS_MARKER = "<!-- participant-intake-status -->"
APPROVAL_LABEL = "participant-approved"
MAINTAINER_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
JSON_BLOCK = re.compile(r"```json\s*(.*?)```", re.IGNORECASE | re.DOTALL)

sys.path.insert(0, str(ROOT / "tools"))
import validate_submission as submission  # noqa: E402


class IntakeError(ValueError):
    """A public, actionable reason an Issue cannot be turned into a PR."""


@dataclass(frozen=True)
class Decision:
    operation: str
    entrant_id: str
    document: dict
    author: str
    ready: bool

    @property
    def target(self) -> str:
        return f"entrants/{self.entrant_id}.json"

    @property
    def title(self) -> str:
        verbs = {"register": "Register", "update": "Update", "revoke": "Revoke"}
        return f"{verbs[self.operation]} participant {self.entrant_id}"


def _load_schema(name: str) -> dict:
    with (ROOT / "schema" / name).open() as handle:
        return json.load(handle)


def _schema_error(document: dict, schema_name: str, label: str) -> None:
    validator = jsonschema.Draft7Validator(_load_schema(schema_name))
    errors = sorted(validator.iter_errors(document), key=lambda err: list(err.path))
    if not errors:
        return
    err = errors[0]
    where = ".".join(str(part) for part in err.absolute_path)
    raise IntakeError(f"{label}{f' field `{where}`' if where else ''}: {err.message}")


def parse_request(body: str) -> dict:
    """Return the single machine block from a website-generated Issue."""
    if MARKER not in (body or ""):
        raise IntakeError("This is not a participant request created by the submission page.")
    blocks = JSON_BLOCK.findall(body or "")
    if len(blocks) != 1:
        raise IntakeError("Keep exactly one fenced `json` block in the Issue body.")
    try:
        request = json.loads(blocks[0])
    except json.JSONDecodeError as error:
        raise IntakeError(f"The request JSON is invalid: {error.msg} at line {error.lineno}.") from error
    if not isinstance(request, dict):
        raise IntakeError("The request JSON must be one object.")
    _schema_error(request, "participant-request.schema.json", "Request")
    return request


def _base_document(base_ref: str, entrant_id: str) -> dict | None:
    path = f"entrants/{entrant_id}.json"
    proc = subprocess.run(
        ["git", "show", f"{base_ref}:{path}"], cwd=ROOT,
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    try:
        value = json.loads(proc.stdout)
    except json.JSONDecodeError as error:
        raise IntakeError(f"The existing `{path}` on `{base_ref}` is invalid JSON.") from error
    if not isinstance(value, dict):
        raise IntakeError(f"The existing `{path}` on `{base_ref}` is not an object.")
    return value


def _same_login(left: str | None, right: str | None) -> bool:
    return bool(left and right and left.casefold() == right.casefold())


def _maintainer(author: str, association: str) -> bool:
    return (association or "").upper() in MAINTAINER_ASSOCIATIONS or \
        author.casefold() in submission.MAINTAINERS


def _owner_check(document: dict, author: str | None, base_ref: str) -> None:
    """Reuse the same reserved-name, ownership, and route-cap rules as CI."""
    rel = f"entrants/{document['entrant_id']}.json"
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            submission.check_entrant_owner(rel, document, author, base_ref)
    except SystemExit as error:
        message = output.getvalue().strip()
        if message.startswith("FAIL:"):
            message = message[5:].strip()
        raise IntakeError(message or f"Ownership validation failed for `{rel}`.") from error


def decide(event: dict, base_ref: str = "origin/main") -> Decision:
    issue = event.get("issue") or {}
    if issue.get("pull_request"):
        raise IntakeError("Participant requests must be Issues, not pull requests.")
    author = ((issue.get("user") or {}).get("login") or "").strip()
    if not author:
        raise IntakeError("GitHub did not provide the Issue author's login.")
    association = (issue.get("author_association") or "NONE").upper()
    labels = {item.get("name") for item in issue.get("labels", []) if isinstance(item, dict)}
    request = parse_request(issue.get("body") or "")
    operation = request["operation"]

    if operation == "register":
        document = request["entrant"]
        entrant_id = document.get("entrant_id", "")
        _schema_error(document, "entrant.schema.json", "Registration")
        if _base_document(base_ref, entrant_id) is not None:
            raise IntakeError(
                f"`{entrant_id}` is already registered. Choose Update or Revoke on the submission page."
            )
        if document.get("type") != "participant" or not document.get("route"):
            raise IntakeError("A Season 0 registration must be a `participant` with an Agent API route.")
        if not _maintainer(author, association) and not _same_login(document.get("github"), author):
            raise IntakeError(
                f"The registration names `@{document.get('github') or ''}`, but this Issue was opened by `@{author}`."
            )
        _owner_check(document, None if _maintainer(author, association) else author,
                     base_ref)
        return Decision(operation, entrant_id, document, author,
                        APPROVAL_LABEL in labels)

    entrant_id = request["entrant_id"]
    old = _base_document(base_ref, entrant_id)
    if old is None:
        raise IntakeError(
            f"`{entrant_id}` is not registered on `main`. Choose Register on the submission page."
        )
    owner = old.get("github")
    if not _maintainer(author, association):
        if not owner:
            raise IntakeError(
                f"`{entrant_id}` has no GitHub owner on record; a maintainer must handle this request."
            )
        if not _same_login(owner, author):
            raise IntakeError(
                f"`{entrant_id}` belongs to `@{owner}`; this Issue was opened by `@{author}`."
            )

    document = dict(old)
    if operation == "update":
        for key, value in request["changes"].items():
            if key == "contact" and value is None:
                document.pop("contact", None)
            else:
                document[key] = value
    else:
        document["status"] = "revoked"
        document.pop("retired_at", None)

    _schema_error(document, "entrant.schema.json", "Updated registration")
    _owner_check(document, None if _maintainer(author, association) else author,
                 base_ref)
    return Decision(operation, entrant_id, document, author, True)


def _write_outputs(path: str | None, decision: Decision) -> None:
    if not path:
        return
    values = {
        "operation": decision.operation,
        "entrant_id": decision.entrant_id,
        "target": decision.target,
        "ready": str(decision.ready).lower(),
        "author": decision.author,
        "title": decision.title,
    }
    with open(path, "a") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def _write_status(path: str | None, text: str) -> None:
    if path:
        Path(path).write_text(f"{STATUS_MARKER}\n{text.rstrip()}\n")


def _event(path: str) -> dict:
    try:
        with open(path) as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        raise IntakeError(f"Cannot read the GitHub event: {error}.") from error
    if not isinstance(value, dict):
        raise IntakeError("The GitHub event must be one JSON object.")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--event", required=True, help="GitHub event JSON")
    parser.add_argument("--base", default="origin/main", help="trusted base ref")
    parser.add_argument("--write", action="store_true", help="write entrants/<id>.json")
    parser.add_argument("--github-output", help="append step outputs here")
    parser.add_argument("--status-file", help="write the Issue status comment here")
    args = parser.parse_args(argv)

    try:
        decision = decide(_event(args.event), args.base)
        _write_outputs(args.github_output, decision)
        if args.write:
            if not decision.ready:
                raise IntakeError(
                    f"Registration is waiting for the `{APPROVAL_LABEL}` label and cannot be written yet."
                )
            target = ROOT / decision.target
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(decision.document, indent=2) + "\n")
            print(f"wrote {decision.target}")
        elif decision.ready:
            _write_status(
                args.status_file,
                f"✅ `{decision.operation}` request by `@{decision.author}` is valid. Preparing the pull request.",
            )
        else:
            _write_status(
                args.status_file,
                "✅ The registration is valid. A maintainer must review it and add "
                f"the `{APPROVAL_LABEL}` label before the pull request is created. "
                "Editing the Issue re-runs validation; the Issue stays open.",
            )
        print(
            f"{decision.operation} {decision.entrant_id}: "
            f"{'ready' if decision.ready else 'pending approval'}"
        )
        return 0
    except IntakeError as error:
        message = str(error)
        _write_status(
            args.status_file,
            "❌ This request could not be accepted. " + message +
            "\n\nEdit the Issue after correcting it; validation will run again.",
        )
        print(f"FAIL: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
