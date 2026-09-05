"""The participant registry: registrations, keys and tokens in the private
intake repository, saved at registration time and read back by the cron.

Why the private repository and not a database or per-entrant secrets
--------------------------------------------------------------------
Onboarding used to need a person per participant: merge the registration,
install an Actions secret for the endpoint key, install a Vercel variable for
the upload token, file each upload by hand. Every one of those is a step that
can be forgotten the week it matters. Here the registration form writes
everything into `Social-Atoms/social-sim-arena-intake` once, and the six-hourly
refresh reads it back: it writes the public `entrants/<id>.json` files, loads
the endpoint keys into its own environment for the run, and files the bundle
uploads that arrived since last time. One secret in two places (the token that
reads and writes the intake repository) is the whole configuration.

What is stored where
--------------------
- `registrations/<entrant>.json` -- the public part: name, type, method,
  contact, endpoint URL. Exactly what `entrants/<entrant>.json` will say.
- `secrets/<entrant>.json` -- the participant's endpoint key as given, and the
  SHA-256 of the token we issued them. The key is stored as it is because the
  arena has to send it onward as a Bearer token; it is the key to *their*
  server, and the repository is private. Rotation overwrites; history keeps
  the old value, which is the honest limit of this design.
- `promo.json` -- `{"codes": [...]}`. Registration needs one of them.

The token
---------
One per entrant, minted at registration and shown once. It is the entrant's
identity for everything after that: uploading a bundle (Route B) and editing
the registration. There is no password and no login page; the token is the
account. Lost tokens are reissued by a maintainer editing `secrets/` by hand.

Storage backends mirror `questionnaire_api.store_record`: a directory named by
`SUBMISSION_STORAGE_DIR` (tests, the demo server), otherwise the intake
repository through the GitHub contents API. Updates carry the file's sha, so
two writers conflict instead of overwriting each other.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets as _secrets
import time
from pathlib import Path
from typing import Any

from ssa.questionnaire_api import INTAKE_REPO, INTAKE_API_VERSION, INTAKE_TIMEOUT

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRANTS_DIR = os.path.join(ROOT, "entrants")
ENTRANT_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,47}$")
HTTPS_URL = re.compile(r"^https://[^\s?#]+$")
TOKEN_PREFIX = "ssa_"
RECORD_VERSION = "ssa-registration-v1"


class RegistryError(Exception):
    """A refusal the page can show as it is."""

    def __init__(self, message: str, status: int = 400, code: str = "invalid"):
        super().__init__(message)
        self.status = status
        self.code = code


class Conflict(RegistryError):
    def __init__(self, message="This record changed under you; try again."):
        super().__init__(message, 409, "conflict")


# ---------------------------------------------------------------- storage --

def _github_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {os.environ['INTAKE_REPO_TOKEN']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": INTAKE_API_VERSION}


def configured() -> bool:
    return bool(os.environ.get("SUBMISSION_STORAGE_DIR")
                or os.environ.get("INTAKE_REPO_TOKEN"))


class Store:
    """Read, create, update and list JSON documents by path."""

    def __init__(self, root: str | None = None):
        self.root = root or os.environ.get("SUBMISSION_STORAGE_DIR")
        if not self.root and not os.environ.get("INTAKE_REPO_TOKEN"):
            raise RegistryError(
                "The registry is not configured on this deployment "
                "(INTAKE_REPO_TOKEN).", 503, "registry_unavailable")

    def _local(self, pathname: str) -> Path:
        base = Path(self.root).expanduser().resolve()
        target = (base / pathname).resolve()
        if base != target and base not in target.parents:
            raise RegistryError("bad path", 500, "internal")
        return target

    def _url(self, pathname: str) -> str:
        return f"https://api.github.com/repos/{INTAKE_REPO}/contents/{pathname}"

    def get(self, pathname: str) -> tuple[dict | None, str | None]:
        """(document, version) or (None, None)."""
        if self.root:
            p = self._local(pathname)
            if not p.is_file():
                return None, None
            raw = p.read_bytes()
            return json.loads(raw), hashlib.sha256(raw).hexdigest()
        import requests
        r = requests.get(self._url(pathname), timeout=INTAKE_TIMEOUT,
                         headers=_github_headers())
        if r.status_code == 404:
            return None, None
        if r.status_code >= 400:
            raise RegistryError(f"the registry returned HTTP {r.status_code}",
                                503, "registry_unavailable")
        body = r.json()
        return json.loads(base64.b64decode(body["content"])), body["sha"]

    def put(self, pathname: str, doc: dict, version: str | None) -> str:
        """Create when `version` is None, else replace that exact version."""
        raw = json.dumps(doc, indent=2, sort_keys=True,
                         ensure_ascii=False).encode("utf-8") + b"\n"
        if self.root:
            p = self._local(pathname)
            current = (hashlib.sha256(p.read_bytes()).hexdigest()
                       if p.is_file() else None)
            if current != version:
                raise Conflict()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(raw)
            return hashlib.sha256(raw).hexdigest()
        import requests
        body: dict[str, Any] = {"message": pathname,
                                "content": base64.b64encode(raw).decode("ascii")}
        if version:
            body["sha"] = version
        r = requests.put(self._url(pathname), json=body, timeout=INTAKE_TIMEOUT,
                         headers=_github_headers())
        if r.status_code in (409, 422):
            raise Conflict()
        if r.status_code >= 400:
            raise RegistryError(f"the registry returned HTTP {r.status_code}",
                                503, "registry_unavailable")
        return r.json()["content"]["sha"]

    def list(self, directory: str) -> list[str]:
        """File names (not paths) directly under `directory`; [] if absent."""
        if self.root:
            p = self._local(directory)
            if not p.is_dir():
                return []
            return sorted(n for n in os.listdir(p) if n.endswith(".json"))
        import requests
        r = requests.get(self._url(directory), timeout=INTAKE_TIMEOUT,
                         headers=_github_headers())
        if r.status_code == 404:
            return []
        if r.status_code >= 400:
            raise RegistryError(f"the registry returned HTTP {r.status_code}",
                                503, "registry_unavailable")
        return sorted(e["name"] for e in r.json()
                      if e.get("type") == "file" and e["name"].endswith(".json"))

    def walk(self, directory: str, depth: int = 2) -> list[str]:
        """Paths of every JSON file up to `depth` directories below."""
        if self.root:
            base = self._local(directory)
            if not base.is_dir():
                return []
            out = []
            for p in sorted(base.rglob("*.json")):
                rel = p.relative_to(self._local(""))
                if len(rel.parts) - len(Path(directory).parts) <= depth + 1:
                    out.append(str(rel))
            return out
        import requests
        out = []

        def visit(path, left):
            r = requests.get(self._url(path), timeout=INTAKE_TIMEOUT,
                             headers=_github_headers())
            if r.status_code == 404:
                return
            if r.status_code >= 400:
                raise RegistryError(f"the registry returned HTTP {r.status_code}",
                                    503, "registry_unavailable")
            for e in r.json():
                if e.get("type") == "file" and e["name"].endswith(".json"):
                    out.append(e["path"])
                elif e.get("type") == "dir" and left > 0:
                    visit(e["path"], left - 1)
        visit(directory, depth)
        return sorted(out)


# ------------------------------------------------------------- the token --

def mint_token() -> tuple[str, str]:
    """(token shown once, its stored hash)."""
    token = TOKEN_PREFIX + _secrets.token_urlsafe(27)
    return token, hashlib.sha256(token.encode()).hexdigest()


def token_matches(token: Any, stored_hash: str | None) -> bool:
    if not stored_hash or not isinstance(token, str) or not token:
        return False
    return hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(),
                               stored_hash)


# ---------------------------------------------------------- registrations --

def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _reg_path(entrant_id: str) -> str:
    return f"registrations/{entrant_id}.json"


def _secret_path(entrant_id: str) -> str:
    return f"secrets/{entrant_id}.json"


def promo_codes(store: Store) -> set[str]:
    doc, _ = store.get("promo.json")
    codes = set()
    if doc and isinstance(doc.get("codes"), list):
        codes |= {str(c).strip().upper() for c in doc["codes"] if str(c).strip()}
    raw = os.environ.get("SSA_PROMO_CODES") or ""
    codes |= {c.strip().upper() for c in raw.split(",") if c.strip()}
    return codes


def hand_registered(entrant_id: str, entrants_dir: str | None = None) -> bool:
    """True when a committed entrants/<id>.json exists in this checkout."""
    return os.path.isfile(os.path.join(entrants_dir or ENTRANTS_DIR,
                                       entrant_id + ".json"))


def _clean_fields(fields: dict, *, url_required: bool = False) -> dict:
    name = str(fields.get("name") or "").strip()
    method = str(fields.get("method") or "").strip()
    etype = fields.get("type") or "firm"
    contact = str(fields.get("contact") or "").strip()
    url = str(fields.get("url") or "").strip()
    if not 1 <= len(name) <= 80:
        raise RegistryError("Display name: 1 to 80 characters.")
    if not 1 <= len(method) <= 300:
        raise RegistryError("Method: one or two sentences, up to 300 characters.")
    if etype not in ("firm", "llm"):
        raise RegistryError("Type must be firm or llm.")
    if len(contact) > 120:
        raise RegistryError("Contact: up to 120 characters.")
    if url and (not HTTPS_URL.match(url) or len(url) > 300):
        raise RegistryError("Endpoint URL must be https, up to 300 characters, "
                            "with no query string or fragment.")
    if url_required and not url:
        raise RegistryError("Endpoint URL is required.")
    out = {"name": name, "type": etype, "method": method}
    if contact:
        out["contact"] = contact
    if url:
        out["url"] = url
    return out


def _clean_key(key: Any) -> str | None:
    if key is None or key == "":
        return None
    key = str(key).strip()
    if not 8 <= len(key) <= 512 or any(c.isspace() for c in key):
        raise RegistryError("The endpoint key must be 8 to 512 characters "
                            "with no spaces.")
    return key


def register(store: Store, fields: dict, promo_code: Any,
             entrants_dir: str | None = None) -> tuple[dict, str]:
    """Create a registration. Returns (public record, the token, shown once)."""
    codes = promo_codes(store)
    if not codes:
        raise RegistryError("Registration is closed for now.", 403, "closed")
    if str(promo_code or "").strip().upper() not in codes:
        raise RegistryError("That promo code is not valid.", 403, "bad_promo")
    entrant_id = str(fields.get("entrant_id") or "").strip()
    if not ENTRANT_ID.match(entrant_id):
        raise RegistryError("Entrant id: lower-case letters, digits, . _ -; "
                            "2 to 48 characters.")
    if hand_registered(entrant_id, entrants_dir):
        raise RegistryError("That entrant id is already registered.", 409, "taken")
    existing, _ = store.get(_reg_path(entrant_id))
    if existing:
        raise RegistryError("That entrant id is already registered.", 409, "taken")
    public = _clean_fields(fields)
    key = _clean_key(fields.get("endpoint_key"))
    token, digest = mint_token()
    now = _now_iso()
    record = {"record_version": RECORD_VERSION, "entrant_id": entrant_id,
              "created_at": now, "updated_at": now, **public}
    secret = {"entrant_id": entrant_id, "token_sha256": digest,
              "token_issued_at": now}
    if key:
        secret["endpoint_key"] = key
        secret["endpoint_key_set_at"] = now
    # The registration first: it is the uniqueness check, and a create on a
    # taken path is refused by both backends.
    store.put(_reg_path(entrant_id), record, None)
    store.put(_secret_path(entrant_id), secret, None)
    return record, token


def load(store: Store, entrant_id: str) -> tuple[dict | None, str | None]:
    if not isinstance(entrant_id, str) or not ENTRANT_ID.match(entrant_id):
        return None, None
    return store.get(_reg_path(entrant_id))


def authenticate(store: Store, entrant_id: Any, token: Any) -> bool:
    if not isinstance(entrant_id, str) or not ENTRANT_ID.match(entrant_id):
        return False
    secret, _ = store.get(_secret_path(entrant_id))
    return token_matches(token, (secret or {}).get("token_sha256"))


def update(store: Store, entrant_id: str, token: Any, fields: dict) -> dict:
    """Edit a registration with its token. The entrant id cannot change."""
    if not authenticate(store, entrant_id, token):
        raise RegistryError("Unknown entrant or token.", 401, "unauthorized")
    record, version = store.get(_reg_path(entrant_id))
    if not record:
        raise RegistryError("No such registration.", 404, "not_found")
    public = _clean_fields({**record, **{k: v for k, v in fields.items()
                                         if k in ("name", "type", "method",
                                                  "contact", "url")}})
    # An explicit empty string clears an optional field.
    for k in ("contact", "url"):
        if k in fields and str(fields[k] or "").strip() == "":
            public.pop(k, None)
    record = {k: v for k, v in record.items()
              if k in ("record_version", "entrant_id", "created_at")}
    record.update(public)
    record["updated_at"] = _now_iso()
    store.put(_reg_path(entrant_id), record, version)
    if "endpoint_key" in fields:
        secret, sversion = store.get(_secret_path(entrant_id))
        key = _clean_key(fields.get("endpoint_key"))
        if key:
            secret["endpoint_key"] = key
            secret["endpoint_key_set_at"] = record["updated_at"]
        else:
            secret.pop("endpoint_key", None)
            secret.pop("endpoint_key_set_at", None)
        store.put(_secret_path(entrant_id), secret, sversion)
    return record


def rotate_token(store: Store, entrant_id: str, token: Any) -> str:
    """Replace the token; the old one stops working at once."""
    if not authenticate(store, entrant_id, token):
        raise RegistryError("Unknown entrant or token.", 401, "unauthorized")
    secret, version = store.get(_secret_path(entrant_id))
    new, digest = mint_token()
    secret["token_sha256"] = digest
    secret["token_issued_at"] = _now_iso()
    store.put(_secret_path(entrant_id), secret, version)
    return new


def public_view(record: dict, secret: dict | None = None) -> dict:
    out = {k: record.get(k) for k in ("entrant_id", "name", "type", "method",
                                      "contact", "url", "created_at",
                                      "updated_at") if record.get(k)}
    if secret is not None:
        out["endpoint_key_set"] = bool(secret.get("endpoint_key"))
    return out


def entrant_file(record: dict, has_key: bool = True) -> dict:
    """The public registration the pipeline reads, from the record.

    A URL registered without a key is called without one (`auth: none`);
    otherwise the default `bearer` would make the cron wait for a credential
    that will never come and leave the entrant silently off the roster.
    """
    doc = {"entrant_id": record["entrant_id"], "name": record["name"],
           "type": record.get("type", "firm"), "method": record["method"]}
    if record.get("contact"):
        doc["contact"] = record["contact"]
    if record.get("url"):
        doc["route"] = {"kind": "agent_api", "url": record["url"]}
        if not has_key:
            doc["route"]["auth"] = "none"
    return doc


# ------------------------------------------------------- what the cron does --

def registrations(store: Store) -> list[dict]:
    out = []
    for name in store.list("registrations"):
        doc, _ = store.get(f"registrations/{name}")
        if doc and ENTRANT_ID.match(str(doc.get("entrant_id") or "")):
            out.append(doc)
    return out


def materialize(store: Store, entrants_dir: str | None = None) -> list[str]:
    """Write entrants/<id>.json for every registration. Returns the ids
    written or rewritten; a file that already says the same is left alone.
    Only ids the registry owns are touched: a hand-written registration whose
    id was never registered here is never overwritten."""
    entrants_dir = entrants_dir or ENTRANTS_DIR
    os.makedirs(entrants_dir, exist_ok=True)
    changed = []
    for record in registrations(store):
        secret, _ = store.get(_secret_path(record["entrant_id"]))
        doc = entrant_file(record, bool((secret or {}).get("endpoint_key")))
        raw = json.dumps(doc, indent=2, sort_keys=True) + "\n"
        path = os.path.join(entrants_dir, record["entrant_id"] + ".json")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                if fh.read() == raw:
                    continue
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(raw)
        changed.append(record["entrant_id"])
    return changed


def endpoint_keys(store: Store) -> dict[str, str]:
    """{entrant_id: endpoint key} for every registration that stored one."""
    out = {}
    for name in store.list("secrets"):
        doc, _ = store.get(f"secrets/{name}")
        if doc and doc.get("endpoint_key") and \
                ENTRANT_ID.match(str(doc.get("entrant_id") or "")):
            out[doc["entrant_id"]] = doc["endpoint_key"]
    return out


def load_keys_into_env(store: Store) -> list[str]:
    """Set SSA_ENTRANT_KEY_<ID> for this process from the registry, without
    overriding a variable an operator set by hand. Returns the ids loaded."""
    from ssa import participants
    loaded = []
    for entrant_id, key in endpoint_keys(store).items():
        var = participants.key_env(entrant_id)
        if not os.environ.get(var):
            os.environ[var] = key
            loaded.append(entrant_id)
    return loaded


def file_uploads(store: Store, out_dir: str | None = None,
                 bundles_dir: str | None = None) -> list[dict]:
    """Turn every stored bundle upload into forecast files, idempotently.

    The packet carries the response as uploaded and the server's receipt
    time; the deadline check is re-run against that time, never against now,
    so a packet filed six hours after it arrived is judged as it was received.
    Returns one summary per packet.
    """
    from ssa import bundle as bundle_lib
    from ssa import participants
    out_dir = out_dir or os.path.join(ROOT, "forecasts")
    bundles_dir = bundles_dir or os.path.join(ROOT, "questions", "bundles")
    summaries = []
    for path in store.walk("bundles", depth=2):
        packet, _ = store.get(path)
        if not packet or not isinstance(packet.get("response"), dict):
            continue
        response = packet["response"]
        receipt = packet.get("receipt") or {}
        batch_id = response.get("batch_id")
        bpath = os.path.join(bundles_dir, f"{batch_id}.json")
        if not isinstance(batch_id, str) or not os.path.isfile(bpath):
            summaries.append({"packet": path, "status": "skipped",
                              "why": "unknown batch"})
            continue
        with open(bpath, encoding="utf-8") as fh:
            questions = json.load(fh)
        try:
            entrant = participants.registration(response.get("entrant_id"))
        except ValueError:
            entrant = None
        received = receipt.get("received_at")
        try:
            outcome = bundle_lib.normalise(
                response, questions, now=_parse_iso(received), entrant=entrant)
        except bundle_lib.BundleError as err:
            summaries.append({"packet": path, "status": "refused",
                              "why": f"{err.code}: {err}"})
            continue
        states = [state for _, state in
                  bundle_lib.file_records(outcome["records"], out_dir)]
        summaries.append({"packet": path, "status": "filed",
                          "accepted": len(outcome["records"]),
                          "written": sum(1 for s in states if s != "unchanged")})
    return summaries


def _parse_iso(value):
    from datetime import datetime, timezone
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return None
