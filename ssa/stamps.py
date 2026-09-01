"""Proof that a forecast set existed when submissions closed, without trust in us.

Today the evidence is a SHA-256 in this repository's git history. A reader who
trusts us needs nothing more; a reader who does not gets nothing, because we can
rewrite that history. The claim "these forecasts were fixed before the answer
existed" is the arena's central one, and it currently has exactly the wrong
trust model.

OpenTimestamps fixes that for free. A hash submitted to its public calendars is
aggregated into a Merkle tree whose root goes into a Bitcoin transaction; the
returned `.ots` proof is the path from our file to that block. Anyone can then
run `ots verify` and learn that this exact byte sequence existed before a block
that was mined at a known time -- without trusting us, GitHub, or the calendars,
since the attestation is on a chain none of us controls.

**What is stamped, and why it is not the forecasts themselves.** A round has
thirty-four entrants; stamping each file would mean thirty-four submissions per
round, and the object a skeptic actually needs is "all of them when submissions
closed, and nothing added later". So the unit is a *manifest*: one file per
round listing every forecast's canonical hash plus the lock snapshot's, and the
manifest is what gets stamped. One proof covers the round, and a forecast
quietly edited afterwards no longer matches the hash the stamp covers.

**The manifest is written once and never rewritten.** Rewriting it after the
stamp would invalidate the proof, which is the one thing this file exists to
prevent. A later run only ever *upgrades* the proof -- see below.

**Stamping is two-phase, and the second phase matters.** `ots stamp` returns in
a second with a calendar's word for it; the Bitcoin attestation appears hours
later, once a block is mined and the calendar has the path. `ots upgrade`
rewrites the `.ots` with that path. A proof that is never upgraded still verifies
against the calendar, which means trusting the calendar -- so a run that skips
the upgrade leaves the trust model only half-fixed.

**Degrading is deliberate.** If the client is absent the manifest is still
written and recorded as unstamped, because the manifest is useful on its own and
a refresh must not fail over a proof. What must never happen is silence: an
unstamped round says so in the file.
"""
import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAMPS = os.path.join(ROOT, "stamps")
FORECASTS = os.path.join(ROOT, "forecasts")
LOCKS = os.path.join(ROOT, "locks")

# Seconds. `ots stamp` talks to four calendars and returns in about a second;
# `ots upgrade` walks a proof and can be slower. Neither should ever hold up a
# refresh that has forecasts to file.
TIMEOUT = 120


def canonical_sha256(obj):
    """The repository's canonical hash, unchanged: sorted keys, no whitespace.

    This is what the leaderboard and the paper cite, so the serialization is
    fixed. `tools/validate_submission.py` prints the same function's output on
    every submission.
    """
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def file_sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def have_client():
    return shutil.which("ots") is not None


def manifest_path(round_id):
    return os.path.join(STAMPS, f"{round_id}.json")


def proof_path(round_id):
    return manifest_path(round_id) + ".ots"


def build_manifest(round_id, lock_at, forecasts_dir=None, locks_dir=None):
    """Everything fixed for a round at submission close, as one hashable object.

    Forecast hashes are canonical (the object), not file hashes: whitespace in
    a submitted file is not part of what was claimed, and two files differing
    only in indentation state the same forecast.
    """
    fdir = os.path.join(forecasts_dir or FORECASTS, round_id)
    entries = {}
    for name in sorted(os.listdir(fdir)) if os.path.isdir(fdir) else []:
        if not name.endswith(".json"):
            continue
        with open(os.path.join(fdir, name)) as f:
            entries[name[:-5]] = canonical_sha256(json.load(f))
    lock_file = os.path.join(locks_dir or LOCKS, f"{round_id}.json")
    lock_hash = None
    if os.path.exists(lock_file):
        with open(lock_file) as f:
            lock_hash = canonical_sha256(json.load(f))
    return {
        "round_id": round_id,
        "lock_at": lock_at,
        "built_at": _utcnow(),
        "forecast_count": len(entries),
        "forecasts": entries,
        # The history the round froze. Without it a stamp proves the forecasts
        # existed but not what they were forecasting from, and "the baselines
        # were computed from pre-lock data" is a claim too.
        "lock_snapshot_sha256": lock_hash,
        "canonical": "json.dumps(obj, sort_keys=True, separators=(',',':')) utf-8, sha256",
    }


def write_manifest(body):
    os.makedirs(STAMPS, exist_ok=True)
    path = manifest_path(body["round_id"])
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(body, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)
    return path


def _run(args):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=TIMEOUT)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.SubprocessError) as e:
        return 1, f"{type(e).__name__}: {e}"


def stamp(path):
    """Submit a file to the calendars. Returns (ok, output).

    Reports rather than raises: a refresh with forecasts to file must not die
    because four public calendars were briefly unreachable, and the next run
    retries.
    """
    if not have_client():
        return False, "the ots client is not installed"
    if os.path.exists(path + ".ots"):
        return True, "already stamped"
    return (lambda rc, out: (rc == 0 and os.path.exists(path + ".ots"), out))(
        *_run(["ots", "stamp", path]))


def upgrade(path):
    """Replace a calendar-only proof with one attested in a Bitcoin block.

    Idempotent and cheap: once complete, `ots upgrade` says so and changes
    nothing. Until then it is the difference between trusting a calendar and
    trusting a chain, which is the whole point.
    """
    ots = path + ".ots"
    if not have_client() or not os.path.exists(ots):
        return False, "nothing to upgrade"
    before = file_sha256(ots)
    rc, out = _run(["ots", "--no-bitcoin", "upgrade", ots])
    # `ots upgrade` writes a .bak next to the proof; it is a working file, not
    # evidence, and committing it would be committing the superseded proof.
    bak = ots + ".bak"
    if os.path.exists(bak):
        os.remove(bak)
    return (rc == 0 and file_sha256(ots) != before), out


def attested(path):
    """True when the proof carries a Bitcoin attestation rather than only a
    calendar's word. Read from `ots info`, which needs no network."""
    ots = path + ".ots"
    if not have_client() or not os.path.exists(ots):
        return False
    rc, out = _run(["ots", "info", ots])
    return rc == 0 and "verify BitcoinBlockHeaderAttestation" in out


def status(round_id):
    """What exists for one round, for the site and for a human."""
    m, p = manifest_path(round_id), proof_path(round_id)
    if not os.path.exists(m):
        return {"round_id": round_id, "manifest": False}
    return {
        "round_id": round_id,
        "manifest": True,
        "manifest_file": os.path.relpath(m, ROOT),
        "manifest_sha256": file_sha256(m),
        "proof": os.path.exists(p),
        "proof_file": os.path.relpath(p, ROOT) if os.path.exists(p) else None,
        "bitcoin_attested": attested(m),
    }


def ensure(round_id, lock_at, now=None):
    """Build-and-stamp on first sight, upgrade on every later sight.

    Called once per locked round per refresh. The manifest is built exactly
    once -- rebuilding it after the stamp would invalidate the proof -- so a
    forecast that lands after the participant deadline is *not* covered, which
    is the correct outcome and is what the landing audit independently rejects.
    """
    path = manifest_path(round_id)
    fresh = not os.path.exists(path)
    if fresh:
        write_manifest(build_manifest(round_id, lock_at))
    ok, out = (stamp(path) if not os.path.exists(proof_path(round_id))
               else upgrade(path))
    st = status(round_id)
    st["action"] = "stamped" if fresh else "upgraded"
    st["ok"] = ok
    st["detail"] = out.strip().splitlines()[-1] if out.strip() else ""
    return st
