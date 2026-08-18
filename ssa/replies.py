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


def path(round_id, entrant, ih, persona=None):
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
