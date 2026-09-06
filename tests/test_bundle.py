"""The weekly bundle: what a participant is shown, and what an upload becomes.

Run: PYTHONPATH=. python tests/test_bundle.py

No network, no provider, nothing written outside a temporary directory.

The properties worth testing here are all about the two things this route can
get wrong in a way nobody notices until a season is unscoreable. First, the
deadline: a bundle that shows a round's `lock_at` is showing a moment up to
seven days after the one a submission is actually judged against, so every test
that touches a date asserts against `ssa.batches`, never against a literal.
Second, the record: an upload that receipts clean and then fails CI leaves a
participant holding an acknowledgement of a forecast that never landed, so the
records this produces are checked against the same schema and the same
`tools/validate_submission.py` functions the pull-request path runs.
"""
import copy
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import batches                                       # noqa: E402
from ssa import bundle                                        # noqa: E402

SANDBOX = os.path.join(ROOT, "examples", "bundle", "sandbox-batch.json")
SANDBOX_RESPONSE = os.path.join(ROOT, "examples", "bundle",
                                "sandbox-response.json")
SEASON = os.path.join(ROOT, "questions", "season0.json")


def read(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def season_rounds():
    return read(SEASON)["rounds"]


def _locks(bundle_doc):
    return [datetime.fromisoformat(q["lock_at"].replace("Z", "+00:00"))
            for q in bundle_doc["questions"]]


def before_deadline(bundle_doc):
    """A moment every question in the bundle is still open at.

    Each question closes at its own lock, so "inside the window" is before the
    *earliest* of them, not before the header.
    """
    return min(_locks(bundle_doc)) - timedelta(days=2)


def after_deadline(bundle_doc):
    """A moment every question has closed at: past the last lock."""
    return max(_locks(bundle_doc)) + timedelta(seconds=1)


def submission_checks():
    """`tools/validate_submission.py` with `fail` raising, for cross-checking."""
    spec = importlib.util.spec_from_file_location(
        "_ci_checks", os.path.join(ROOT, "tools", "validate_submission.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def raise_instead(message):
        raise AssertionError(message)

    mod.fail = raise_instead
    return mod


# ------------------------------------------------------- the deadline shown

def test_the_deadline_a_bundle_shows_is_every_questions_own_lock():
    """The one rule the whole participant surface rests on.

    Each question closes at its own lock, and that is what the validator
    enforces, so that is what the bundle must show. The header is the listing:
    it opens when the first question is listed and closes when the last one
    closes, and no question may close after it.
    """
    doc = bundle.build_bundle(season_rounds(), "batch-2026-09-14")
    locks = [q["lock_at"] for q in doc["questions"]]
    assert doc["deadline"] == max(locks), (doc["deadline"], max(locks))
    for q in doc["questions"]:
        due = bundle.iso(batches.effective_deadline(q["lock_at"]))
        assert due == q["lock_at"], (q["round_id"], due)
        assert q["lock_at"] <= doc["deadline"], q["round_id"]
        assert q["lock_at"] > doc["published_at"], q["round_id"]


def test_one_bundle_carries_the_whole_weeks_mixed_horizons():
    """0.1 to 6.1 days inside one batch, and identical for every entrant.

    That spread is why the horizon is reported per question rather than
    normalised away: it is a property of the question, not a head start.
    """
    rounds = season_rounds()
    doc = bundle.build_bundle(rounds, "batch-2026-09-14")
    # Counted from the season rather than pinned. A literal here goes red the
    # week a round is promoted into this batch, which is a normal thing to do
    # and not a bundle defect; what has to hold is that the bundle carries
    # every round of the batch and nothing else.
    expected = {r["round_id"] for r in rounds
                if batches.batch_of(r["lock_at"]) == "batch-2026-09-14"}
    assert {q["round_id"] for q in doc["questions"]} == expected, (
        sorted(expected ^ {q["round_id"] for q in doc["questions"]}))
    assert len(doc["questions"]) == len(expected)
    shapes = {q["target_type"] for q in doc["questions"]}
    assert shapes == {
        "continuous_normal", "profile_energy", "ranking_list"}, shapes
    locks = [q["lock_at"] for q in doc["questions"]]
    assert locks == sorted(locks), "questions are not in lock order"
    # The horizon is now the distance from a question's close to its answer,
    # so it is a property of the question, not of where the week's calendar
    # happened to put it. A week mixes 48-hour rounds with the ones that must
    # lock before the period they measure.
    horizons = [q["horizon_days"] for q in doc["questions"]]
    assert min(horizons) == 2.0 and max(horizons) > 6.0, horizons
    for q in doc["questions"]:
        assert abs(q["horizon_days"] - batches.horizon_days(
            q["lock_at"], q["release_at"])) < 0.001


def test_a_pre_cutover_batch_is_refused_rather_than_given_a_deadline():
    """Those rounds each carried their own; there is no single moment to show,
    and inventing one publishes a due date the validator does not enforce."""
    try:
        bundle.build_bundle(season_rounds(), "batch-2026-08-31")
        assert False, "a pre-cutover batch was bundled"
    except bundle.BundleError as err:
        assert err.code == "pre_cutover_batch", err.code


def test_every_generated_bundle_satisfies_its_own_schema():
    rounds = season_rounds()
    governed = [b for b in bundle.batch_ids(rounds)
                if batches.governed_by_batch(
                    next(r["lock_at"] for r in rounds
                         if batches.batch_of(r["lock_at"]) == b))]
    assert governed, "no batch in the season falls under the batch rule"
    for batch_id in governed:
        doc = bundle.build_bundle(rounds, batch_id)
        assert bundle.check_bundle(doc) == [], (batch_id,
                                               bundle.check_bundle(doc))


def test_a_bundle_whose_header_disagrees_with_the_calendar_is_caught():
    """The header says when the listing closes; each question closes at its
    own lock. A header that closes before one of its questions is a header
    that would tell a participant they were late when they were not."""
    doc = bundle.build_bundle(season_rounds(), "batch-2026-09-14")
    doc["deadline"] = "2026-09-15T12:00:00Z"
    problems = bundle.check_bundle(doc)
    assert problems and any("declared close" in p for p in problems), problems


# ------------------------------------------------------ answers coming back

def test_all_three_answer_shapes_round_trip_in_one_payload():
    """A batch mixes scalar, profile and ranking rounds, so a reply must too.

    Split by shape, a participant would need three uploads and there would be
    no single moment at which their week is complete -- and a missing upload
    would look exactly like a deliberate abstention.
    """
    doc, response = read(SANDBOX), read(SANDBOX_RESPONSE)
    assert bundle.check_bundle(doc) == [], bundle.check_bundle(doc)
    out = bundle.normalise(response, doc, now=before_deadline(doc))
    assert out["receipt"]["accepted"] == 3, out["results"]
    assert out["receipt"]["rejected"] == 0, out["results"]
    keys = sorted(next(k for k in ("topline", "profile", "ranking")
                       if k in record)
                  for record in out["records"].values())
    assert keys == ["profile", "ranking", "topline"], keys


def test_accepted_answers_become_the_records_ci_already_accepts():
    """The receipt would otherwise acknowledge a forecast that never lands.

    Checked against `schema/forecast.schema.json` and against
    `tools/validate_submission.py`'s own round-shape and quantile functions --
    the same code the pull-request and lock-audit workflows shell out to.
    """
    import jsonschema
    schema = read(os.path.join(ROOT, "schema", "forecast.schema.json"))
    checks = submission_checks()
    doc = read(SANDBOX)
    for use_quantiles in (False, True):
        response = build_answers(doc, quantiles=use_quantiles)
        out = bundle.normalise(response, doc, now=before_deadline(doc))
        assert out["receipt"]["rejected"] == 0, out["results"]
        for rid, record in out["records"].items():
            jsonschema.validate(record, schema)
            question = next(q for q in doc["questions"] if q["round_id"] == rid)
            checks.check_answer_matches_round(
                rid, record, bundle._round_shim(question))
            for label, dist in checks.answer_blocks(record):
                checks.check_shape(rid, label, dist)
                checks.check_quantiles(rid, label, dist)
            assert record["entrant"] == response["entrant_id"]
            assert record["round_id"] == rid


def test_a_wrong_shaped_answer_is_refused_rather_than_reshaped():
    """A single number is not a weak answer to a profile round; it answers a
    different question, and scoring it as an entry would put a forecaster who
    never modelled the population on the same board as one who did."""
    doc = read(SANDBOX)
    response = build_answers(doc)
    swap = {"sandbox-profile-2028-w01": {"topline": {"mean": 1.0, "sd": 2.0}},
            "sandbox-approval-2028-w01": {"ranking": ["a", "b"]}}
    for answer in response["answers"]:
        if answer["round_id"] in swap:
            for key in ("topline", "profile", "ranking"):
                answer.pop(key, None)
            answer.update(swap[answer["round_id"]])
    out = bundle.normalise(response, doc, now=before_deadline(doc))
    verdict = {r["round_id"]: r for r in out["results"]}
    assert verdict["sandbox-profile-2028-w01"]["reason"] == "wrong_shape"
    assert verdict["sandbox-approval-2028-w01"]["reason"] == "wrong_shape"
    assert verdict["sandbox-ranking-2028-w01"]["status"] == "accepted"


def test_a_point_guess_is_refused_without_taking_the_file_down_with_it():
    """One bad `sd` is one bad answer. Collapsing the whole payload into a
    single 'invalid' would destroy the per-round verdict this route exists to
    give, and a participant would have to bisect their own file to find it."""
    doc = read(SANDBOX)
    response = build_answers(doc)
    response["answers"][0]["topline"] = {"mean": 3.0, "sd": 0}
    out = bundle.normalise(response, doc, now=before_deadline(doc))
    verdict = {r["round_id"]: r for r in out["results"]}
    bad = verdict["sandbox-approval-2028-w01"]
    assert bad["reason"] == "invalid_answer", bad
    assert out["receipt"]["accepted"] == 2, out["results"]


def test_a_profile_missing_one_cell_is_refused():
    """The energy score is a norm over the whole vector: a hole has no score."""
    doc = read(SANDBOX)
    response = build_answers(doc)
    answer = next(a for a in response["answers"]
                  if a["round_id"] == "sandbox-profile-2028-w01")
    answer["profile"].pop(sorted(answer["profile"])[0])
    out = bundle.normalise(response, doc, now=before_deadline(doc))
    verdict = {r["round_id"]: r for r in out["results"]}
    bad = verdict["sandbox-profile-2028-w01"]
    assert bad["reason"] == "invalid_answer", bad
    assert "missing" in " ".join(bad["messages"]), bad["messages"]


def test_a_ranking_outside_the_published_basket_is_refused():
    """A fixed-basket round is a permutation, not a free choice of items."""
    doc = read(SANDBOX)
    response = build_answers(doc)
    answer = next(a for a in response["answers"]
                  if a["round_id"] == "sandbox-ranking-2028-w01")
    answer["ranking"][0] = "something_nobody_published"
    out = bundle.normalise(response, doc, now=before_deadline(doc))
    verdict = {r["round_id"]: r for r in out["results"]}
    assert verdict["sandbox-ranking-2028-w01"]["reason"] == "invalid_answer"


def test_an_answer_to_a_round_outside_the_bundle_is_refused_not_ignored():
    """Silently dropping it would leave a participant believing they answered."""
    doc = read(SANDBOX)
    response = build_answers(doc)
    response["answers"].append({"round_id": "not-in-this-bundle",
                                "topline": {"mean": 1.0, "sd": 1.0}})
    out = bundle.normalise(response, doc, now=before_deadline(doc))
    verdict = {r["round_id"]: r for r in out["results"]}
    assert verdict["not-in-this-bundle"]["reason"] == "unknown_round"
    assert out["receipt"]["accepted"] == 3


def test_the_same_round_answered_twice_is_refused_rather_than_resolved():
    """Which of the two is the forecast is not the arena's decision to make."""
    doc = read(SANDBOX)
    response = build_answers(doc)
    duplicate = copy.deepcopy(response["answers"][0])
    duplicate["topline"] = {"mean": 99.0, "sd": 1.0}
    response["answers"].append(duplicate)
    out = bundle.normalise(response, doc, now=before_deadline(doc))
    reasons = [r.get("reason") for r in out["results"]]
    assert "duplicate_round" in reasons, out["results"]
    assert out["receipt"]["accepted"] == 3


def test_an_unanswered_round_is_reported_and_is_not_an_error():
    doc = read(SANDBOX)
    response = build_answers(doc)
    response["answers"] = response["answers"][:1]
    out = bundle.normalise(response, doc, now=before_deadline(doc))
    assert out["receipt"]["accepted"] == 1
    assert out["receipt"]["rejected"] == 0
    assert out["receipt"]["unanswered"] == ["sandbox-profile-2028-w01",
                                            "sandbox-ranking-2028-w01"]


# --------------------------------------------------------------- deadlines

def test_a_payload_that_arrives_after_the_deadline_is_refused_by_the_batch():
    """And the message names the moment *this question* closed, which is its
    own lock: quoting anything else invites the participant to argue they were
    in time."""
    doc = read(SANDBOX)
    response = build_answers(doc)
    out = bundle.normalise(response, doc, now=after_deadline(doc))
    assert out["receipt"]["accepted"] == 0, out["results"]
    by_round = {q["round_id"]: q["lock_at"] for q in doc["questions"]}
    for result in out["results"]:
        assert result["reason"] == "late", result
        said = " ".join(result["messages"])
        assert doc["batch_id"] in said
        assert by_round[result["round_id"]] in said, said


def test_the_deadline_is_decided_per_answer_not_once_for_the_whole_file():
    """`check_bundle` refuses to publish a bundle spanning two batches, but the
    intake must still be right if one ever reaches it -- a single verdict for
    the file would either reject every on-time answer or accept a late one."""
    rounds = season_rounds()
    doc = bundle.build_bundle(rounds, "batch-2026-09-14")
    later = bundle.build_bundle(rounds, "batch-2026-09-21")
    doc["questions"].append(later["questions"][0])
    response = build_answers(doc)
    # After batch-2026-09-14 closed, before batch-2026-09-21 does.
    out = bundle.normalise(response, doc,
                           now=datetime(2026, 9, 15, tzinfo=timezone.utc))
    verdict = {r["round_id"]: r for r in out["results"]}
    assert verdict[doc["questions"][0]["round_id"]]["reason"] == "late"
    assert verdict[later["questions"][0]["round_id"]]["status"] == "accepted"


def test_a_client_timestamp_cannot_move_the_deadline():
    """The schema has no field for one, so an attempt is a schema violation
    rather than a value quietly preferred over the server's clock."""
    doc = read(SANDBOX)
    response = build_answers(doc)
    response["submitted_at"] = "2020-01-01T00:00:00Z"
    try:
        bundle.normalise(response, doc, now=after_deadline(doc))
        assert False, "a client timestamp was accepted into the payload"
    except bundle.BundleError as err:
        assert err.code == "invalid_response", err.code


# ------------------------------------------------- identity and re-uploads

def test_answers_are_not_portable_between_weeks():
    doc = read(SANDBOX)
    response = build_answers(doc)
    response["batch_id"] = "batch-2026-09-14"
    try:
        bundle.normalise(response, doc, now=before_deadline(doc))
        assert False, "answers for another batch were accepted"
    except bundle.BundleError as err:
        assert err.code == "batch_mismatch", err.code


def test_a_revoked_entrant_is_refused_before_a_receipt_exists():
    """A receipt the arena intends to ignore is worse than a refusal."""
    doc = read(SANDBOX)
    response = build_answers(doc)
    entrant = {"entrant_id": response["entrant_id"], "name": "Demo",
               "type": "llm", "method": "example", "status": "revoked"}
    try:
        bundle.normalise(response, doc, now=before_deadline(doc),
                         entrant=entrant)
        assert False, "a revoked entrant filed"
    except bundle.BundleError as err:
        assert err.code == "entrant_revoked", err.code
    entrant["status"] = "active"
    out = bundle.normalise(response, doc, now=before_deadline(doc),
                           entrant=entrant)
    assert out["receipt"]["accepted"] == 3


def test_a_payload_cannot_file_under_someone_elses_entrant_id():
    doc = read(SANDBOX)
    response = build_answers(doc)
    entrant = {"entrant_id": "somebody_else", "name": "Other", "type": "llm",
               "method": "example"}
    try:
        bundle.normalise(response, doc, now=before_deadline(doc),
                         entrant=entrant)
        assert False, "a response filed under another registration"
    except bundle.BundleError as err:
        assert err.code == "entrant_mismatch", err.code


def test_the_same_upload_twice_is_a_replay_not_a_second_submission():
    """Identical bytes give an identical receipt hash and rewrite nothing, so a
    retried upload after a dropped connection does not become a second,
    differently-timestamped entry."""
    doc = read(SANDBOX)
    response = build_answers(doc)
    when = before_deadline(doc)
    first = bundle.normalise(response, doc, now=when)
    second = bundle.normalise(copy.deepcopy(response), doc,
                              now=when + timedelta(minutes=5))
    assert (first["receipt"]["response_sha256"]
            == second["receipt"]["response_sha256"])
    assert first["receipt"]["received_at"] != second["receipt"]["received_at"]
    out_dir = tempfile.mkdtemp(prefix="ssa-bundle-")
    try:
        created = bundle.file_records(first["records"], out_dir)
        assert {state for _, state in created} == {"created"}, created
        again = bundle.file_records(second["records"], out_dir)
        assert {state for _, state in again} == {"unchanged"}, again
    finally:
        shutil.rmtree(out_dir)


def test_a_revision_before_the_deadline_replaces_the_earlier_file():
    """Filing early has to stay free of cost, which means a revision has to be
    possible right up to the deadline."""
    doc = read(SANDBOX)
    out_dir = tempfile.mkdtemp(prefix="ssa-bundle-")
    try:
        first = bundle.normalise(build_answers(doc), doc,
                                 now=before_deadline(doc))
        bundle.file_records(first["records"], out_dir)
        revised = build_answers(doc)
        revised["answers"][0]["topline"] = {"mean": -1.5, "sd": 4.0}
        second = bundle.normalise(revised, doc, now=before_deadline(doc))
        states = dict((os.path.basename(os.path.dirname(p)), s)
                      for p, s in bundle.file_records(second["records"],
                                                      out_dir))
        assert states["sandbox-approval-2028-w01"] == "replaced", states
        assert states["sandbox-profile-2028-w01"] == "unchanged", states
    finally:
        shutil.rmtree(out_dir)


def test_a_late_revision_never_overwrites_what_landed_on_time():
    """The failure that would be invisible: an on-time forecast silently
    replaced by a better one written after the deadline."""
    doc = read(SANDBOX)
    out_dir = tempfile.mkdtemp(prefix="ssa-bundle-")
    try:
        first = bundle.normalise(build_answers(doc), doc,
                                 now=before_deadline(doc))
        bundle.file_records(first["records"], out_dir)
        path = os.path.join(out_dir, "sandbox-approval-2028-w01",
                            "demo_bundle_entrant.json")
        on_time = read(path)
        revised = build_answers(doc)
        revised["answers"][0]["topline"] = {"mean": 42.0, "sd": 1.0}
        late = bundle.normalise(revised, doc, now=after_deadline(doc))
        assert late["records"] == {}, late["records"]
        bundle.file_records(late["records"], out_dir)
        assert read(path) == on_time
    finally:
        shutil.rmtree(out_dir)


# ------------------------------------------------------------ the provenance

def test_a_filed_record_says_which_batch_and_which_answer_produced_it():
    """Traceable without anyone consulting anything private -- and hashed per
    answer, so revising one round does not rewrite the other twelve records'
    notes and, with them, twelve canonical hashes that nobody changed."""
    doc = read(SANDBOX)
    response = build_answers(doc)
    out = bundle.normalise(response, doc, now=before_deadline(doc))
    for rid, record in out["records"].items():
        answer = next(a for a in response["answers"] if a["round_id"] == rid)
        assert doc["batch_id"] in record["notes"], record["notes"]
        assert bundle.sha256_of(answer)[:12] in record["notes"], record["notes"]
        assert len(record["notes"]) <= 500


def test_long_participant_notes_are_truncated_and_the_stamp_survives():
    """The stamp is the part a reader cannot reconstruct, so it is not what
    gets cut."""
    doc = read(SANDBOX)
    response = build_answers(doc)
    response["notes"] = "x" * 500
    out = bundle.normalise(response, doc, now=before_deadline(doc))
    for record in out["records"].values():
        assert len(record["notes"]) <= 500, len(record["notes"])
        assert record["notes"].endswith("]"), record["notes"][-40:]


def test_the_canonical_hash_is_the_arenas_and_has_not_moved():
    """The leaderboard and the paper cite this serialization; it does not
    change. Pinned against a literal rather than against itself."""
    import hashlib
    obj = {"b": 1, "a": [1, 2]}
    assert bundle.canonical(obj) == b'{"a":[1,2],"b":1}'
    assert bundle.sha256_of(obj) == hashlib.sha256(
        b'{"a":[1,2],"b":1}').hexdigest()


# ------------------------------------------------------------------ helpers

def build_answers(bundle_doc, entrant_id="demo_bundle_entrant",
                  quantiles=False):
    """The shipped example entrant, run in process on an arbitrary bundle.

    Tests run the participant's own starter code rather than a private fixture:
    an example that drifts out of agreement with the intake is exactly the bug
    a new team would hit first.
    """
    spec = importlib.util.spec_from_file_location(
        "_example_entrant",
        os.path.join(ROOT, "examples", "bundle", "entrant.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    anchors = {}
    for q in bundle_doc["questions"]:
        if q["target_type"] == "continuous_normal":
            anchors[q["round_id"]] = -6.5
        elif q["target_type"] == "profile_energy":
            anchors[q["round_id"]] = {cell: -6.5 for cell in q["cells"]}
        elif q["target_type"] == "ranking_list" and not q.get("items"):
            anchors[q["round_id"]] = [f"item_{i}"
                                      for i in range(q["ranking_length"])]
    return module.build_response(bundle_doc, entrant_id, anchors, quantiles,
                                 False)


def test_the_shipped_example_response_is_what_the_example_entrant_produces():
    """The committed response is a checked-in artefact, so it can rot. If the
    entrant changes and this file does not, a new team's first copy-paste
    produces something the docs say is impossible."""
    doc = read(SANDBOX)
    anchors = read(os.path.join(ROOT, "examples", "bundle",
                                "sandbox-anchors.json"))
    spec = importlib.util.spec_from_file_location(
        "_example_entrant2",
        os.path.join(ROOT, "examples", "bundle", "entrant.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    produced = module.build_response(doc, "demo_bundle_entrant", anchors,
                                     False, False)
    assert produced == read(SANDBOX_RESPONSE), \
        "examples/bundle/sandbox-response.json is stale; regenerate it"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
