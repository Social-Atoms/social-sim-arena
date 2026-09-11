"""Route A: the registrations the arena calls, and the rules for calling them.

An entrant that carries a `route` block in `entrants/<id>.json` is answered by
someone else's HTTPS endpoint rather than by a model in `harness.MODELS`. This
module is the only place that reads those blocks, so every rule about reaching
a stranger's server is stated once.

Why a route and not a model
---------------------------
`docs/agent-api.md` promises the arena "reuses its existing request runner,
response-text extraction, validation, and filing window". `harness.route()`
already returns everything the runner needs -- `env`, `api`, `base`, `model`,
`params`, `via` -- so a participant is expressible as one more route. Adding a
parallel calling path instead would give the season two request runners, two
retry policies and two definitions of the buy window, and the participant's
would be the one nobody exercises weekly.

What this file refuses, and why each refusal exists
---------------------------------------------------
- **No credential at all.** The arena signs every request it sends
  (`ssa/signing.py`) and the participant verifies with the published public
  key, so a registration carries no key and the arena stores none. A
  registration that could name a credential could name `ANTHROPIC_API_KEY`;
  the schema refuses any such field.
- **HTTPS only.** Checked here as well as in the schema. The schema runs when a
  registration is opened as a pull request; this runs every time we are about
  to send a signed request.
- **No standby.** `harness.standby_route` falls back to OpenRouter when a
  configured route is terminally down. For a participant that would send their
  round to a third-party vendor on our account and file the reply as their
  forecast. A participant's endpoint is the only place their forecast can come
  from, so a failure is a failure.
- **Revocation stops the call.** `status: "revoked"` is honoured here, at the
  point of dialling, not only in the probe. A revocation the caller does not
  read is a revocation in name only.
"""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRANTS = os.path.join(ROOT, "entrants")

DEFAULT_MODEL = "ssa-agent"
KIND = "agent_api"

# Mirrors schema/entrant.schema.json. Both exist on purpose: the schema is what
# a pull request is checked against, this is what a request is checked against.
# A registration can be edited on main by anyone who can push there.
_HTTPS = re.compile(r"^https://[^\s?#]+$")
_ENTRANT_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,47}$")


def _path(entrant):
    return os.path.join(ENTRANTS, entrant + ".json")


def registration(entrant, entrants_dir=None):
    """The registration dict, or None when there is no such file."""
    if not _ENTRANT_ID.match(entrant or ""):
        return None
    path = (os.path.join(entrants_dir, entrant + ".json")
            if entrants_dir else _path(entrant))
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        return json.load(fh)


def revoked(entrant, entrants_dir=None):
    reg = registration(entrant, entrants_dir) or {}
    return reg.get("status") in ("revoked", "retired")


def route(entrant, entrants_dir=None):
    """A `harness.route`-shaped dict for a registered participant, or None.

    None means "not a participant" -- an unregistered id, a registration with
    no `route` block, or a revoked one. Every caller treats None as "this is
    one of our own models", which is what it was before this module existed.

    Raises only when a registration *is* a route and is unusable. That
    distinction matters: a missing route is an ordinary entrant, a malformed
    one is a configuration error somebody has to see.
    """
    reg = registration(entrant, entrants_dir)
    if not reg:
        return None
    spec = reg.get("route")
    if not spec:
        return None
    if reg.get("status") in ("revoked", "retired"):
        return None

    kind = spec.get("kind")
    if kind != KIND:
        raise ValueError(
            f"{entrant}: unknown route kind {kind!r}; only {KIND!r} exists")
    url = spec.get("url") or ""
    if not _HTTPS.match(url):
        raise ValueError(
            f"{entrant}: route url must be an https URL with no query or "
            f"fragment, got {spec.get('url')!r}")
    return {
        # No credential: the arena signs its requests (ssa/signing.py) and the
        # participant verifies with the published key. "" tells the runner
        # there is nothing to look up.
        "env": "",
        "api": "agent",
        "base": url,
        "model": DEFAULT_MODEL,
        "params": {},
        "via": "participant",
    }


def is_participant(entrant, entrants_dir=None):
    """True when this id is answered by someone else's endpoint.

    Reads the file rather than `route()` so a revoked participant is still a
    participant: the roster has to be able to say "registered, not called"
    instead of quietly reclassifying them as one of our models.
    """
    reg = registration(entrant, entrants_dir) or {}
    return bool(reg.get("route"))


def callable_now(entrant, entrants_dir=None):
    """(ok, why-not). Everything that has to be true before we dial.

    Returns the reason rather than a bare False so the operator status line
    can distinguish a revoked registration from a missing secret -- two very
    different things to do about it.
    """
    reg = registration(entrant, entrants_dir)
    if not reg or not reg.get("route"):
        return False, "not a registered Route A participant"
    if reg.get("status") in ("revoked", "retired"):
        return False, "registration is revoked"
    try:
        route(entrant, entrants_dir)
    except ValueError as err:
        return False, str(err)
    from . import signing
    if signing.live_signer() is None:
        return False, (f"no signing key in {signing.LIVE_KEY_ENV}; the arena "
                       "does not call participants unsigned")
    return True, ""


def registered(entrants_dir=None):
    """Every Route A entrant id, revoked ones included, sorted.

    Revoked ones are included so a caller that must skip them does so by
    reading `callable_now`, visibly, rather than by never seeing them.
    """
    directory = entrants_dir or ENTRANTS
    if not os.path.isdir(directory):
        return []
    out = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        entrant = name[:-len(".json")]
        if is_participant(entrant, entrants_dir):
            out.append(entrant)
    return out
