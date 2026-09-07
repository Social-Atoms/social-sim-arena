"""Raw provider replies, written the moment they arrive.

A live forecast call is money already spent. Until this module the only thing
that survived one was the *parsed* forecast, which means a reply that came back
malformed -- prose around the JSON, a refusal, a truncated object -- left the
tokens billed and the text gone. So did a run that died between the call and
the commit, and the runner is ephemeral by design: there is nothing to go back
to. On a round whose lock does not wait, the same prompt then gets bought twice.

So every reply is written here before anything tries to parse it, one file per
call:

    replies/<round_id>/<entrant>.<ih>.json
    replies/<round_id>/<entrant>.<ih>.<persona_id>.json     persona panels

`ih` is the same twelve-hex input hash the forecast's notes already carry as
`in=<ih>` -- the hash of (call identity, prompt) from `harness.prompt_hash`. A
reply here and the forecast it produced are therefore joined by a value that is
already published, and two things fall out of that:

  * **Runs resume.** A reply on disk that parses is a forecast nobody has to buy
    again. `harness._ask` looks here before every paid call, so a crash, a bad
    parse or a cancelled workflow costs the tokens once instead of once per
    retry.
  * **The season is auditable.** Temperature is deliberately never set, so a
    rerun does not reproduce and cannot be the reproducibility mechanism. The
    committed raw text is. That claim was already written down in `harness`
    before anything wrote the text down; this directory is what makes it true.

The persona id is in the *filename* because a panel is 192 calls under one
input hash, and one file per respondent is what lets an interrupted panel
resume the 37 it had left rather than re-running all 192.

**Committed, and not rotated.** About 1-2 KB per reply and a few hundred replies
a month across the season's entrants and cells -- single-digit megabytes a
season, against an audit trail that cannot be rebuilt at any price. Nothing here
expires.

That is also the difference from `cache/model_backtest/`, which is this idea for
the backtest and is deliberately *not* committed: a backtest call can always be
re-run for money, while a live round's prompt stops being answerable the moment
the release lands.
"""
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def root():
    """Where the log lives.

    Read per call rather than bound at import, so SSA_REPLIES_DIR can redirect
    it -- which is how the tests exercise the log without writing into the
    repository, and how a local run can keep its replies out of the tree.
    """
    return os.environ.get("SSA_REPLIES_DIR") or os.path.join(ROOT, "replies")


def _segment(value, what):
    """One path segment, or a raise.

    Every id that reaches this module comes from a schema-validated file --
    `round_id` is `^[a-z0-9][a-z0-9-]{2,63}$` in the forecast schema, `entrant_id` likewise --
    so this has never fired. It is here because the guarantee that a reply or a
    failure lands inside the log should be a property of this module rather
    than of every caller: `os.path.join(root(), "../..", name)` writes wherever
    it is told, and a log that can be aimed is not a log.
    """
    text = str(value)
    if not text or text in (".", "..") or "/" in text or "\\" in text \
            or text.startswith("."):
        raise ValueError(f"{what} is not a usable path segment: {value!r}")
    return text


def path(round_id, entrant, ih, persona=None):
    round_id = _segment(round_id, "round_id")
    entrant = _segment(entrant, "entrant")
    name = f"{entrant}.{ih}.json" if persona is None else \
        f"{entrant}.{ih}.{persona}.json"
    return os.path.join(root(), round_id, name)


def prompt_sha256(prompt):
    """The full digest of the prompt text itself.

    Not the same thing as `ih`, and both are worth storing: `ih` is truncated
    and folds in the endpoint, so it answers "would we send this again"; this
    answers "was the text byte-identical" without keeping a copy of it.
    """
    return hashlib.sha256((prompt or "").encode("utf-8")).hexdigest()


def log(round_id, entrant, ih, record):
    """Write one reply. Returns the path written, or None if it could not be.

    The round, entrant and hash are stamped in from the arguments, so the
    filename and the contents cannot disagree about which call this was. The
    caller supplies the rest: model id, via, prompt_sha256, reply, usage, and
    `persona` for a panel member -- which is read from the record rather than
    passed separately, for the same reason.

    **A failure here never raises.** The caller is holding a reply it has
    already paid for and is about to file as a forecast; losing that over an
    unwritable directory would be a worse outcome than losing the log entry.
    It says so on stderr instead, which is the workflow log.
    """
    persona = record.get("persona")
    dest = path(round_id, entrant, ih, persona)
    body = dict(record)
    body.update({"round_id": round_id, "entrant": entrant, "input_hash": ih,
                 "logged_at": datetime.now(timezone.utc)
                 .strftime("%Y-%m-%dT%H:%M:%SZ")})
    # Unique per process as well as per record: two refreshes can overlap, and a
    # shared tmp name would let one truncate the other's file mid-write.
    tmp = f"{dest}.{os.getpid()}.tmp"
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(tmp, "w") as f:
            json.dump(body, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, dest)
    except OSError as e:
        print(f"  ! reply log: {entrant} {round_id} {ih}: {type(e).__name__}: {e}",
              file=sys.stderr)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return None
    return dest


def lookup(round_id, entrant, ih, persona=None):
    """The stored reply for this exact call, or None.

    A missing file and an unreadable one answer the same way on purpose: the
    caller's next move either way is to pay for the call, and a half-written
    file from a killed run must not be able to stop it.
    """
    try:
        with open(path(round_id, entrant, ih, persona)) as f:
            got = json.load(f)
    except (OSError, ValueError):
        return None
    return got if isinstance(got, dict) else None


# --- failures ---------------------------------------------------------------
#
# A call that produced no usable reply is evidence too, and until now it left
# nothing behind: the exception went to the workflow log of an ephemeral runner
# and died with it, so "the endpoint has been failing all week" was something an
# operator could believe but not show. Worse, a call that was *cut off* -- the
# hour cap, the 1 MB cap -- threw away the bytes that had arrived, which are the
# only thing that distinguishes a slow endpoint from a proxy error page from a
# body that never ends.
#
# Failures live under `failures/` inside the round's reply directory, so
# `git add -A ... replies` in refresh.yml commits them along with the replies,
# and so `lookup` can never reach one: its path is
# `replies/<round>/<entrant>.<ih>.json`, never a subdirectory. That separation
# is deliberate and load-bearing -- a failure record must never be replayable as
# an answer, which is also why nothing written here carries a `reply` key.

# Attempts kept per (round, entrant, input hash). A dead endpoint fails three
# times a run and a run happens every six hours, so an outage that lasts a week
# is bounded at the most recent twenty attempts rather than eighty files; the
# running total is kept so the count stays honest after trimming.
MAX_ATTEMPTS_KEPT = 20


def failure_path(round_id, entrant, ih, persona=None):
    round_id = _segment(round_id, "round_id")
    entrant = _segment(entrant, "entrant")
    name = f"{entrant}.{ih}.json" if persona is None else \
        f"{entrant}.{ih}.{persona}.json"
    return os.path.join(root(), round_id, "failures", name)


def log_failure(round_id, entrant, ih, record):
    """Append one failed attempt. Returns the path written, or None.

    Read-modify-write, so the file holds the history of one call rather than
    the last thing that went wrong. Two overlapping refreshes writing the same
    (round, entrant, hash) can lose one attempt to last-writer-wins; that costs
    a duplicate of a failure the other process also recorded, which is not worth
    a lock file.

    Like `log`, this never raises. It is called from an exception path, and an
    unwritable directory must not replace the failure being reported with a
    different one.
    """
    dest = failure_path(round_id, entrant, ih, record.get("persona"))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    attempt = dict(record)
    attempt.pop("reply", None)                   # never replayable, see above
    attempt["logged_at"] = now
    body = {"round_id": round_id, "entrant": entrant, "input_hash": ih,
            "attempts_total": 0, "attempts": []}
    try:
        with open(dest) as f:
            got = json.load(f)
        if isinstance(got, dict) and isinstance(got.get("attempts"), list):
            body["attempts"] = got["attempts"]
            body["attempts_total"] = int(got.get("attempts_total") or
                                         len(got["attempts"]))
    except (OSError, ValueError, TypeError):
        pass                                     # first failure, or unreadable
    body["attempts_total"] += 1
    body["attempts"] = (body["attempts"] + [attempt])[-MAX_ATTEMPTS_KEPT:]
    body["last_failed_at"] = now
    tmp = f"{dest}.{os.getpid()}.tmp"
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(tmp, "w") as f:
            json.dump(body, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, dest)
    except OSError as e:
        print(f"  ! failure log: {entrant} {round_id} {ih}: {type(e).__name__}: {e}",
              file=sys.stderr)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return None
    return dest


def failures(round_id, entrant, ih, persona=None):
    """The recorded attempts for one call, oldest first. Never raises."""
    try:
        with open(failure_path(round_id, entrant, ih, persona)) as f:
            got = json.load(f)
    except (OSError, ValueError):
        return []
    return got.get("attempts") or [] if isinstance(got, dict) else []
