"""The weekly bundle: one file of questions out, one file of answers back.

**Why a bundle rather than a round.** A round is the arena's unit; it is not a
participant's. Season 0's rounds lock on six different weekdays, so a
per-round hand-off asked an external team to track six recurring deadlines and
to file each answer separately -- and a team that missed one of the six had no
way to tell a question they had skipped from one they had never been shown.
`ssa/batches.py` collapsed the six into one Monday 12:00Z deadline, and a
bundle is simply that batch written down: one deadline, every question due at
it, and one reply carrying every answer. Everything here computes the deadline
from `batches.effective_deadline`. **A round's `lock_at` is never a
participant's deadline** -- it is the arena's clock, 0 to 7 days later -- and a
surface that shows it as one is showing a date that is both wrong and late.

**Why all three answer shapes ride in one payload.** A real batch mixes them:
`batch-2026-09-14` is 12 scalar rounds and one 16-cell profile, and other weeks
carry a ranking. Splitting the reply by shape would mean three uploads, three
receipts, and no single moment at which a participant's week is complete --
and, worse, a partial upload would be indistinguishable from a deliberate
abstention. So `normalise` takes one response and returns a verdict *per
round*: eleven accepted and two refused is a normal, fully-described outcome,
not an error.

**Why the shape rules are borrowed, not restated.** Everything a record must
satisfy to survive CI already lives in `tools/validate_submission.py`, which is
the single source of truth the pull-request and lock-audit workflows both shell
out to. Re-implementing those rules here would create the one failure this path
cannot have: a receipt issued for an answer that the validator later rejects,
leaving a participant holding a signed acknowledgement of a forecast that never
landed. So `_checks()` loads that file and calls its own functions, with `fail`
rebound to raise instead of exiting. It imports nothing from `ssa/`, so there
is no cycle, and by construction the intake cannot accept what CI refuses.

**Why the receipt carries the server's clock and both hashes.** The deadline is
decided by when the arena received the payload, never by a timestamp inside it;
a client clock is a field the client controls. The bundle hash pins which
questions were asked -- so a later dispute cannot turn on a manifest that moved
-- and the response hash pins the exact bytes answered, which is also what
makes a re-upload of the same file identifiable as a replay rather than a
second, conflicting submission.

Canonical form is the arena's throughout: `json.dumps(obj, sort_keys=True,
separators=(",", ":"))` encoded UTF-8, then sha256. It is what the leaderboard
and the paper cite; it does not change.
"""
import hashlib
import importlib.util
import json
import os
from datetime import datetime, timezone

from ssa import batches

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_DIR = os.path.join(ROOT, "schema")
VALIDATOR_PATH = os.path.join(ROOT, "tools", "validate_submission.py")

# Bumped only when a field changes meaning. A participant pins it, so a silent
# change is a silent breakage on somebody else's machine.
BUNDLE_VERSION = "1.0.0"

PROFILE_TARGET_TYPE = "profile_energy"
RANKING_TARGET_TYPE = "ranking_list"
SCALAR_TARGET_TYPE = "continuous_normal"

# Which answer key each round type takes. The mapping is one-to-one and there
# is no default: a round type this table does not know is refused rather than
# guessed at, because guessing produces a scored forecast nobody asked for.
ANSWER_KEY = {
    SCALAR_TARGET_TYPE: "topline",
    PROFILE_TARGET_TYPE: "profile",
    RANKING_TARGET_TYPE: "ranking",
}


class BundleError(ValueError):
    """The payload as a whole cannot be processed, so no answer in it can be."""

    def __init__(self, message, code="invalid_bundle"):
        super().__init__(message)
        self.code = code


class Rejected(ValueError):
    """One answer refused, with a reason a participant can act on."""

    def __init__(self, message, reason="invalid_answer"):
        super().__init__(message)
        self.reason = reason


# ---------------------------------------------------------------- primitives

def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_of(obj):
    return hashlib.sha256(canonical(obj)).hexdigest()


def iso(t):
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now(now=None):
    return now or datetime.now(timezone.utc)


_CHECKS = None


def _checks():
    """`tools/validate_submission.py`, with `fail` rebound to raise.

    Loaded by path rather than imported because `tools/` is not a package -- it
    is a directory of scripts CI runs on a bare checkout, which is also why
    that file imports nothing from `ssa/` and this direction of dependency is
    the only one that exists.
    """
    global _CHECKS
    if _CHECKS is None:
        spec = importlib.util.spec_from_file_location(
            "_ssa_submission_checks", VALIDATOR_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.fail = lambda message: (_ for _ in ()).throw(Rejected(message))
        _CHECKS = mod
    return _CHECKS


def load_schema(name):
    with open(os.path.join(SCHEMA_DIR, name), encoding="utf-8") as fh:
        return json.load(fh)


def _iter_schema_errors(obj, name):
    """(where, message) per violation of `schema/<name>`, or None if unchecked.

    Returns None -- not an empty list -- when `jsonschema` is absent, so a
    caller can tell "nothing was wrong" from "nothing was checked". Degrading
    silently to a pass is how an intake starts accepting anything on a machine
    that happens to be missing a dependency.
    """
    try:
        import jsonschema
    except ImportError:
        return None
    validator = jsonschema.Draft7Validator(load_schema(name))
    return sorted(validator.iter_errors(obj), key=lambda e: list(e.path))


def schema_errors(obj, name):
    """[] when `obj` matches `schema/<name>`, else one line per violation.

    Degrades to a shallow required-key check when `jsonschema` is absent, for
    the reason the submission validator does: a participant checking their file
    on a laptop with no virtualenv should get a useful answer rather than a
    traceback. The degraded path is not the contract -- CI and the arena both
    run the real one.
    """
    errors = _iter_schema_errors(obj, name)
    if errors is None:
        required = load_schema(name).get("required", [])
        return [f"missing required field '{k}'" for k in required
                if not isinstance(obj, dict) or k not in obj]
    out = []
    for err in errors:
        where = "/".join(str(p) for p in err.absolute_path) or "(root)"
        out.append(f"{where}: {err.message}")
    return out


def split_response_errors(response):
    """Schema violations, split into ones that kill the file and ones that kill
    one answer.

    A malformed `sd` inside answer nine is not a reason to refuse the other
    twelve, and refusing them would make a participant's per-round verdict list
    -- the thing this route exists to give them -- collapse into a single
    unhelpful "invalid". So an error whose JSON pointer lands inside
    `answers/<i>` is charged to that answer; anything else (a wrong
    `batch_id`, a missing `entrant_id`, `answers` that is not a list) is
    charged to the file, because no answer in it can be trusted either.
    """
    errors = _iter_schema_errors(response, "bundle_response.schema.json")
    if errors is None:
        required = load_schema("bundle_response.schema.json")["required"]
        return ([f"missing required field '{k}'" for k in required
                 if not isinstance(response, dict) or k not in response], {})
    envelope, per_answer = [], {}
    for err in errors:
        path = list(err.absolute_path)
        if len(path) >= 2 and path[0] == "answers" and isinstance(path[1], int):
            where = "/".join(str(p) for p in path[2:]) or "(answer)"
            per_answer.setdefault(path[1], []).append(f"{where}: {err.message}")
        else:
            where = "/".join(str(p) for p in path) or "(root)"
            envelope.append(f"{where}: {err.message}")
    return envelope, per_answer


# ------------------------------------------------------------- building out

def _require(round_data, field):
    value = round_data.get(field)
    if not value:
        raise BundleError(
            f"round '{round_data.get('round_id')}' has no '{field}'; a question "
            "missing it cannot be answered, and publishing it anyway would ask "
            "for a number in an unstated unit")
    return value


def question_from_round(round_data):
    """One round definition, reduced to what a participant needs to answer it.

    Wording, unit and resolution rule are carried verbatim rather than
    summarised: they are what the answer is scored against, and a paraphrase
    that drifts from the frozen round is a question nobody was actually asked.
    """
    lock_at = _require(round_data, "lock_at")
    target = round_data.get("target_type") or SCALAR_TARGET_TYPE
    if target not in ANSWER_KEY:
        raise BundleError(
            f"round '{round_data.get('round_id')}' has target_type '{target}', "
            "which this bundle version does not know how to ask")
    question = {
        "round_id": _require(round_data, "round_id"),
        "tracker": _require(round_data, "tracker"),
        "series": _require(round_data, "series"),
        "question": _require(round_data, "question"),
        "unit": _require(round_data, "unit"),
        "target_type": target,
        "release_at": _require(round_data, "release_at"),
        "lock_at": lock_at,
        "horizon_days": round(batches.horizon_days(lock_at), 3),
        "resolve": _require(round_data, "resolve"),
    }
    if target == PROFILE_TARGET_TYPE:
        cells = round_data.get("cells")
        if not cells:
            raise BundleError(
                f"profile round '{question['round_id']}' does not name its "
                "`cells`; the energy score is over the whole vector, so a "
                "roster nobody published is a round nobody can answer")
        question["cells"] = list(cells)
    if target == RANKING_TARGET_TYPE:
        spec = round_data.get("ranking") or {}
        length = spec.get("length") or len(spec.get("items") or [])
        if not length:
            raise BundleError(
                f"ranking round '{question['round_id']}' names neither a "
                "`ranking.length` nor an item basket")
        question["ranking_length"] = length
        if spec.get("items"):
            question["items"] = list(spec["items"])
        elif spec.get("exclusions"):
            question["exclusions"] = spec["exclusions"]
    return question


def batch_ids(rounds):
    """Every batch present in `rounds`, oldest first."""
    return sorted({batches.batch_of(r["lock_at"]) for r in rounds})


def next_batch_id(rounds, now=None):
    """The earliest batch whose deadline has not passed. Raises if none has."""
    now = _now(now)
    open_ids = sorted({
        batches.batch_of(r["lock_at"]) for r in rounds
        if batches.governed_by_batch(r["lock_at"])
        and batches.deadline_for(r["lock_at"]) > now
    })
    if not open_ids:
        raise BundleError(
            "no batch is still open: every round in this season either locks "
            "before the cutover or has passed its deadline. Publishing an "
            "empty bundle would tell a participant there was nothing to answer, "
            "which is a different claim from 'the season is over'.",
            code="no_open_batch")
    return open_ids[0]


def build_bundle(rounds, batch_id=None, now=None):
    """The question bundle for one batch, from frozen round definitions.

    Read-only by construction: it never touches `questions/season0.json`, which
    is the file a human owns. Refuses a pre-cutover batch outright -- those
    rounds each carried their own deadline, so there is no single moment to put
    in the `deadline` field, and inventing one would publish a due date that
    `tools/validate_submission.py` does not enforce.
    """
    batch_id = batch_id or next_batch_id(rounds, now)
    picked = sorted(
        (r for r in rounds if batches.batch_of(r["lock_at"]) == batch_id),
        key=lambda r: (r["lock_at"], r["round_id"]))
    if not picked:
        raise BundleError(f"no round belongs to {batch_id}", code="empty_batch")
    ungoverned = [r["round_id"] for r in picked
                  if not batches.governed_by_batch(r["lock_at"])]
    if ungoverned:
        raise BundleError(
            f"{batch_id} predates the batch cutover "
            f"({iso(batches.FIRST_DEADLINE)}): {len(ungoverned)} of its rounds "
            "were bought and scored against their own locks, so the batch has "
            "no single deadline to publish",
            code="pre_cutover_batch")
    lock_at = picked[0]["lock_at"]
    return {
        "schema_version": BUNDLE_VERSION,
        "batch_id": batch_id,
        "deadline": iso(batches.effective_deadline(lock_at)),
        "published_at": iso(batches.published_at(lock_at)),
        "questions": [question_from_round(r) for r in picked],
    }


def check_bundle(bundle):
    """[] when the bundle is publishable, else one line per problem.

    Beyond the schema: the declared deadline has to be the deadline
    `ssa/batches.py` computes for every question in it. A bundle whose header
    disagrees with the calendar is the failure mode that matters here, because
    the header is what a participant reads and the calendar is what the
    validator enforces.
    """
    problems = list(schema_errors(bundle, "bundle.schema.json"))
    if problems:
        return problems
    for q in bundle["questions"]:
        lock_at = q["lock_at"]
        if not batches.governed_by_batch(lock_at):
            problems.append(
                f"{q['round_id']}: locks before the batch cutover, so its "
                "deadline is its own lock and it does not belong in a bundle")
            continue
        if batches.batch_of(lock_at) != bundle["batch_id"]:
            problems.append(
                f"{q['round_id']}: locks in {batches.batch_of(lock_at)}, not "
                f"{bundle['batch_id']}")
        due = iso(batches.effective_deadline(lock_at))
        if due != bundle["deadline"]:
            problems.append(
                f"{q['round_id']}: its deadline is {due}, but the bundle "
                f"declares {bundle['deadline']}")
        want = round(batches.horizon_days(lock_at), 3)
        if abs(q["horizon_days"] - want) > 0.001:
            problems.append(
                f"{q['round_id']}: horizon_days is {q['horizon_days']}, "
                f"computed {want}")
    return problems


# ---------------------------------------------------------------- answers in

def _round_shim(question):
    """The round-definition fields the borrowed checks read.

    A bundle question and a season round are the same question written for two
    audiences; this is the small translation between them, kept in one place so
    the borrowed checks never see a shape they were not written for.
    """
    shim = {"round_id": question["round_id"],
            "target_type": question["target_type"]}
    if question["target_type"] == PROFILE_TARGET_TYPE:
        shim["cells"] = list(question.get("cells") or [])
    if question["target_type"] == RANKING_TARGET_TYPE:
        spec = {"length": question.get("ranking_length")
                or len(question.get("items") or [])}
        if question.get("items"):
            spec["items"] = list(question["items"])
        if question.get("exclusions"):
            spec["exclusions"] = question["exclusions"]
        shim["ranking"] = spec
    return shim


def _notes_for(answer, response, batch_id):
    """Participant notes plus a provenance stamp, inside the schema's 500.

    The stamp says which batch the record came from and hashes *this answer*,
    not the whole upload. Hashing the upload was the obvious first choice and
    it was wrong: revising one round would change the notes of every record in
    the file, so a one-line correction rewrote thirteen forecasts and thirteen
    canonical hashes. Per answer, an untouched round is byte-identical across
    re-uploads, which is what lets `file_records` tell a revision from a retry.

    The participant's own text is truncated rather than the stamp, because the
    stamp is the part a reader cannot reconstruct.
    """
    stamp = f" [bundle={batch_id} ans={sha256_of(answer)[:12]}]"
    text = answer.get("notes") or response.get("notes") or ""
    room = 500 - len(stamp)
    if len(text) > room:
        text = text[:max(0, room - 1)].rstrip() + "…"
    return (text + stamp) if text else stamp.strip()


def forecast_record(answer, question, entrant_id, notes):
    """One `forecasts/<round_id>/<entrant_id>.json` object.

    Identity, round and provenance come from the arena; only the numbers come
    from the participant. That split is the whole reason an upload can be
    trusted enough to score: a payload cannot file itself under someone else's
    entrant id, or against a round it was not asked.
    """
    key = ANSWER_KEY[question["target_type"]]
    if key not in answer:
        got = [k for k in ("topline", "profile", "ranking") if k in answer]
        raise Rejected(
            f"{question['round_id']}: this is a {question['target_type']} "
            f"round and takes a `{key}`; the answer carries "
            f"{('a `' + got[0] + '`') if got else 'nothing'}. One shape does "
            "not substitute for another -- a single number is not a weaker "
            "answer to a profile question, it is an answer to a different one.",
            reason="wrong_shape")
    record = {
        "round_id": question["round_id"],
        "entrant": entrant_id,
        key: answer[key],
        "notes": notes,
    }
    checks = _checks()
    rid = question["round_id"]
    checks.check_answer_matches_round(rid, record, _round_shim(question))
    for label, dist in checks.answer_blocks(record):
        checks.check_shape(rid, label, dist)
        checks.check_quantiles(rid, label, dist)
    return record


def entrant_is_revoked(entrant):
    """A registration explicitly withdrawn. Absent status means active.

    Revocation is checked on the way in rather than on the way out: an accepted
    upload from a revoked entrant would already have a receipt, and a receipt
    the arena intends to ignore is worse than a refusal.
    """
    return bool(entrant) and entrant.get("status") == "revoked"


def normalise(response, bundle, now=None, entrant=None):
    """Turn one uploaded response into forecast records and a per-round verdict.

    Returns `{"receipt": ..., "results": [...], "records": {round_id: ...}}`.
    Every answer in the payload appears in `results` exactly once, accepted or
    refused with a reason; only accepted ones appear in `records`.

    The deadline is applied per answer, from each question's own
    `effective_deadline`, rather than once from the bundle header. Within a
    live bundle those are the same instant, so this looks redundant -- until a
    payload is uploaded that mixes a question from this batch with one whose
    batch already closed, which is precisely the case where a single verdict
    for the whole file would either reject twelve good answers or accept one
    late one.
    """
    received_at = _now(now)
    if not isinstance(response, dict):
        raise BundleError("response must be a JSON object",
                          code="invalid_response")
    envelope_problems, answer_problems = split_response_errors(response)
    if envelope_problems:
        raise BundleError(
            "response does not match schema/bundle_response.schema.json: "
            + "; ".join(envelope_problems[:5]),
            code="invalid_response")
    if response["schema_version"] != bundle.get("schema_version"):
        raise BundleError(
            f"response is schema_version {response['schema_version']}, the "
            f"bundle is {bundle.get('schema_version')}",
            code="version_mismatch")
    if response["batch_id"] != bundle.get("batch_id"):
        raise BundleError(
            f"response is for {response['batch_id']}, this bundle is "
            f"{bundle.get('batch_id')}. Answers are not portable between "
            "weeks: the questions differ even when the round ids repeat.",
            code="batch_mismatch")
    if entrant is not None and entrant.get("entrant_id") != response["entrant_id"]:
        raise BundleError(
            f"registration is for '{entrant.get('entrant_id')}', the response "
            f"claims '{response['entrant_id']}'",
            code="entrant_mismatch")
    if entrant_is_revoked(entrant):
        raise BundleError(
            f"entrant '{response['entrant_id']}' is revoked and cannot file. "
            "Ask the maintainers to reinstate the registration before "
            "uploading again.",
            code="entrant_revoked")

    questions = {q["round_id"]: q for q in bundle.get("questions", [])}
    response_sha = sha256_of(response)

    results, records, seen = [], {}, set()
    for index, answer in enumerate(response["answers"]):
        rid = answer.get("round_id") if isinstance(answer, dict) else None
        rid = rid if isinstance(rid, str) else f"answers[{index}]"
        result = {"round_id": rid, "status": "rejected", "messages": []}
        if index in answer_problems:
            result["reason"] = "invalid_answer"
            result["messages"] = [f"{rid}: {line}"
                                  for line in answer_problems[index]]
            results.append(result)
            seen.add(rid)
            continue
        if rid in seen:
            result["reason"] = "duplicate_round"
            result["messages"] = [
                f"{rid}: answered more than once in this payload. Which of the "
                "two is the forecast is not something the arena may decide."]
            results.append(result)
            continue
        seen.add(rid)
        question = questions.get(rid)
        if question is None:
            result["reason"] = "unknown_round"
            result["messages"] = [
                f"{rid}: not a question in {bundle['batch_id']}"]
            results.append(result)
            continue
        result["deadline"] = iso(batches.effective_deadline(question["lock_at"]))
        result["horizon_days"] = question["horizon_days"]

        record, reasons = None, []
        try:
            record = forecast_record(
                answer, question, response["entrant_id"],
                _notes_for(answer, response, bundle["batch_id"]))
        except Rejected as err:
            reasons.append((err.reason, str(err)))
        due = batches.effective_deadline(question["lock_at"])
        if received_at >= due:
            reasons.append((
                "late",
                f"{rid}: {batches.batch_of(question['lock_at'])} closed at "
                f"{iso(due)}; this payload arrived {iso(received_at)}"))
        if reasons:
            # `late` wins the headline because it is the one problem a
            # re-upload cannot fix, but every message is carried so a payload
            # that is both late and malformed does not hide the malformation
            # until the following week.
            late = [r for r in reasons if r[0] == "late"]
            result["reason"] = (late or reasons)[0][0]
            result["messages"] = [m for _, m in reasons]
            results.append(result)
            continue
        result["status"] = "accepted"
        result.pop("messages")
        result["sha256"] = sha256_of(record)
        records[rid] = record
        results.append(result)

    accepted = [r for r in results if r["status"] == "accepted"]
    receipt = {
        "schema_version": BUNDLE_VERSION,
        "batch_id": bundle["batch_id"],
        "entrant_id": response["entrant_id"],
        "deadline": bundle["deadline"],
        "received_at": iso(received_at),
        "bundle_sha256": sha256_of(bundle),
        "response_sha256": response_sha,
        "accepted": len(accepted),
        "rejected": len(results) - len(accepted),
        "unanswered": sorted(set(questions) - seen),
    }
    return {"receipt": receipt, "results": results, "records": records}


def file_records(records, out_dir):
    """Write accepted records to `<out_dir>/<round_id>/<entrant>.json`.

    Rewriting an identical record is reported as `unchanged` rather than
    written again, so a participant who uploads the same file twice does not
    get a second, differently-timestamped submission. A record that differs
    from one already on disk overwrites it: before the deadline an entrant may
    revise, and `normalise` has already refused everything that arrived after
    it, so a write reaching this point is by construction still in time.
    """
    written = []
    for rid, record in sorted(records.items()):
        path = os.path.join(out_dir, rid, record["entrant"] + ".json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        blob = json.dumps(record, indent=2, sort_keys=True) + "\n"
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                if json.load(fh) == record:
                    written.append((path, "unchanged"))
                    continue
            state = "replaced"
        else:
            state = "created"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(blob)
        written.append((path, state))
    return written
