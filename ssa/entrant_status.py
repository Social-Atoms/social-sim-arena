"""Per-entrant status for the site: coverage and call health.

Coverage counts the questions an entrant was asked against the ones it
answered. A question counts as asked once its call window has closed (the
round is locked, awaiting resolution or resolved); an open question is not
held against anyone yet. An answer is a forecast on disk, the same test the
board uses.

Health reads the harness's own records. `replies/<round>/<entrant>.<hash>.json`
is a call that came back (its `logged_at` is the call time);
`replies/<round>/failures/<entrant>.<hash>.json` holds every attempt that
produced no forecast. The last call is whichever of the two is later.
"""
import json
import os

from . import replies

ASKED_STATUSES = ("locked", "awaiting_resolution", "resolved")


def _entrant_of(filename):
    return filename.split(".")[0]


def call_records(root=None):
    """(successes, failures) keyed by entrant id, read from the replies tree."""
    root = root or replies.root()
    ok, bad = {}, {}
    if not os.path.isdir(root):
        return ok, bad
    for round_id in sorted(os.listdir(root)):
        rdir = os.path.join(root, round_id)
        if not os.path.isdir(rdir):
            continue
        for fn in os.listdir(rdir):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(rdir, fn)) as fh:
                    rec = json.load(fh)
            except (OSError, ValueError):
                continue
            ent = rec.get("entrant") or _entrant_of(fn)
            at = rec.get("logged_at")
            if at:
                cur = ok.setdefault(ent, {"calls": 0, "last_at": None})
                cur["calls"] += 1
                if cur["last_at"] is None or at > cur["last_at"]:
                    cur["last_at"] = at
        fdir = os.path.join(rdir, "failures")
        if not os.path.isdir(fdir):
            continue
        for fn in os.listdir(fdir):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(fdir, fn)) as fh:
                    rec = json.load(fh)
            except (OSError, ValueError):
                continue
            ent = rec.get("entrant") or _entrant_of(fn)
            attempts = rec.get("attempts") or []
            last = attempts[-1] if attempts else {}
            at = rec.get("last_failed_at") or last.get("logged_at")
            if not at:
                continue
            cur = bad.setdefault(ent, {"failures": 0, "last_at": None, "recent": []})
            cur["failures"] += int(rec.get("attempts_total") or len(attempts) or 1)
            if cur["last_at"] is None or at > cur["last_at"]:
                cur["last_at"] = at
            cur["recent"].append({"round_id": round_id, "at": at,
                                  "error": (last.get("error_type") or "") + (": " if last.get("error_type") else "")
                                           + (last.get("error") or "")[:160]})
    for cur in bad.values():
        cur["recent"] = sorted(cur["recent"], key=lambda a: a["at"])[-5:]
    return ok, bad


def build(rounds, entrants, root=None, forecasts_root=None):
    """The rows data.json carries under entrant_status, keyed by entrant id.

    An answer is a forecast file for the round and entrant (any shape: a
    profile or a ranking counts as much as a number); the round's published
    `forecasts`, which carry only toplines, are the fallback when the tree is
    not on disk."""
    ok, bad = call_records(root)
    forecasts_root = forecasts_root or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "forecasts")
    ids = [e["entrant_id"] for e in entrants if e.get("entrant_id")]
    out = {}
    asked = [r for r in rounds if r.get("status") in ASKED_STATUSES]
    for ent in ids:
        row = {"asked": 0, "answered": 0, "by_domain": {}}
        for r in asked:
            dom = r.get("domain") or "other"
            d = row["by_domain"].setdefault(dom, {"asked": 0, "answered": 0})
            row["asked"] += 1
            d["asked"] += 1
            if ent in (r.get("forecasts") or {}) or os.path.exists(os.path.join(forecasts_root, str(r.get("round_id")), ent + ".json")):
                row["answered"] += 1
                d["answered"] += 1
        good, fail = ok.get(ent), bad.get(ent)
        row["calls"] = (good or {}).get("calls", 0)
        row["failures"] = (fail or {}).get("failures", 0)
        row["recent_failures"] = (fail or {}).get("recent", [])
        last_ok, last_bad = (good or {}).get("last_at"), (fail or {}).get("last_at")
        if last_ok or last_bad:
            row["last_call_at"] = max(x for x in (last_ok, last_bad) if x)
            row["last_call_ok"] = bool(last_ok) and (not last_bad or last_ok >= last_bad)
        else:
            row["last_call_at"] = None
            row["last_call_ok"] = None
        out[ent] = row
    return out
