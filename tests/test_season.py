"""The season manifest fails before publication when its meaning is invalid."""
import copy
import json
import os

from ssa import bundle, inventory, season

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def reviewed():
    with open(os.path.join(ROOT, "questions", "season0.json")) as fh:
        return json.load(fh)


def one_problem(mutator):
    document = reviewed()
    mutator(document["rounds"])
    return "\n".join(season.validate_document(document))


def test_the_reviewed_season_is_semantically_publishable():
    assert season.validate_document(reviewed()) == []


def test_an_invalid_schedule_fails():
    def break_it(rounds):
        rounds[0]["lock_at"] = rounds[0]["release_at"]
    got = one_problem(break_it)
    assert "invalid schedule" in got and "before release_at" in got, got


def test_civiqs_cannot_keep_48_hours_but_move_to_an_invented_weekday():
    def break_it(rounds):
        target = next(r for r in rounds
                      if r["round_id"] == "civiqs-2026-w38-approval")
        target["lock_at"] = "2026-09-17T14:00:00Z"
        target["release_at"] = "2026-09-19T14:00:00Z"
    got = one_problem(break_it)
    assert "Civiqs release is Saturday" in got, got


def test_behavioral_schedule_is_checked_against_its_week_not_a_magic_offset():
    document = reviewed()
    wiki = next(r for r in document["rounds"]
                if r["round_id"] == "wiki-2026-08-30-trump")
    wiki["lock_at"] = "2026-08-19T14:00:00Z"  # still before Mon Aug 24
    assert season.validate_document(document) == []

    wiki["lock_at"] = "2026-08-24T00:00:00Z"  # measured week already began
    got = "\n".join(season.validate_document(document))
    assert "behavioral round locks" in got, got


def test_a_shape_that_disagrees_with_target_type_fails():
    def break_it(rounds):
        rounds[0]["cells"] = ["civiqs_net_approval", "civiqs_net_approval_rep"]
    got = one_problem(break_it)
    assert "scalar round carries shape" in got, got


def test_a_moving_resolution_rule_fails():
    def break_it(rounds):
        rounds[0]["resolve"] = "use the latest current value"
    got = one_problem(break_it)
    assert "moving target" in got, got


def test_the_same_target_under_a_new_id_fails():
    def break_it(rounds):
        duplicate = copy.deepcopy(rounds[0])
        duplicate["round_id"] = "another-name-for-the-same-target"
        rounds.append(duplicate)
    got = one_problem(break_it)
    assert "duplicate target" in got and "umich-2026-08-prelim" in got, got


def test_a_time_edit_cannot_disguise_the_same_scalar_target():
    def break_it(rounds):
        duplicate = copy.deepcopy(rounds[0])
        duplicate["round_id"] = "same-release-one-hour-later"
        duplicate["lock_at"] = "2026-08-12T15:00:00Z"
        duplicate["release_at"] = "2026-08-14T15:00:00Z"
        rounds.append(duplicate)
    got = one_problem(break_it)
    assert "duplicate target" in got, got


def test_a_release_edit_cannot_disguise_the_same_ranking_week():
    def break_it(rounds):
        source = next(r for r in rounds if r["target_type"] == "ranking_list")
        duplicate = copy.deepcopy(source)
        duplicate["round_id"] = "same-ranking-week-released-later"
        duplicate["release_at"] = "2026-09-08T15:00:00Z"
        rounds.append(duplicate)
    got = one_problem(break_it)
    assert "duplicate target" in got, got


def _round_from_a_blocked_source(rounds, rid="blocked-2026-10-07", week=5):
    """Attach a new round to a source the inventory refuses, and return its id.

    Since 2026-09-03 every *registered* source is approved, so a blocked round
    cannot be found in the season file any more -- it has to be built. The
    series is registered only for the duration of the test, pointed at a source
    that is `rejected` in the real inventory, so what is exercised is the
    validator's own lookup rather than a stubbed verdict.
    """
    template = copy.deepcopy(next(
        r for r in rounds if r.get("target_type") == "continuous_normal"
        and r.get("series")))
    template.update(round_id=rid, series="blocked_series",
                    lock_at=f"2026-10-{week:02d}T14:00:00Z",
                    release_at=f"2026-10-{week + 2:02d}T14:00:00Z")
    rounds.append(template)
    return rid


def test_one_more_round_from_an_unapproved_source_fails():
    """Adding an adapter, or reviving a withdrawn one, must not add rounds."""
    source = next(k for k, r in inventory.INVENTORY.items()
                  if r["rights"] == inventory.REJECTED)
    season.SERIES["blocked_series"] = {"source": source}
    try:
        got = one_problem(_round_from_a_blocked_source)
    finally:
        del season.SERIES["blocked_series"]
    assert f"source {source!r} rights are 'rejected'" in got, got


def test_grandfathering_is_exact_not_a_source_wide_bypass():
    """The exception list is empty, so this checks the mechanism, not a member.

    An id on the list exempts that one round. A second round from the same
    source still fails -- which is the whole reason the list is keyed by round
    id rather than by source, and the property that would be lost if somebody
    ever 'simplified' it into a source-wide allow.
    """
    assert season.GRANDFATHERED_RIGHTS == frozenset(), \
        "a new exception was added; extend this test to cover it"
    source = next(k for k, r in inventory.INVENTORY.items()
                  if r["rights"] == inventory.REJECTED)
    season.SERIES["blocked_series"] = {"source": source}
    exempt, second = "blocked-exempt", "blocked-second"
    real = season.GRANDFATHERED_RIGHTS
    season.GRANDFATHERED_RIGHTS = frozenset({exempt})
    try:
        def add_both(rounds):
            _round_from_a_blocked_source(rounds, exempt, week=5)
            _round_from_a_blocked_source(rounds, second, week=12)
        got = one_problem(add_both)
    finally:
        season.GRANDFATHERED_RIGHTS = real
        del season.SERIES["blocked_series"]
    assert f"{second}: source {source!r} rights" in got, got
    assert f"{exempt}: source" not in got, \
        f"the exempt round was refused anyway: {got}"


def test_election_specials_are_audited_against_certified_results():
    specials = [r for r in reviewed()["rounds"]
                if r["tracker"] == "midterm_special"]
    assert len(specials) == 2
    assert {season.source_for(r) for r in specials} == {
        "certified_election_results"}


def test_a_ranking_contract_that_cannot_be_scored_fails():
    def break_it(rounds):
        ranking = next(r for r in rounds if r["target_type"] == "ranking_list")
        ranking["ranking"]["loss"] = "kendall"
    got = one_problem(break_it)
    assert "kind wiki_top10 is scored with rbo" in got, got


def test_every_committed_bundle_is_the_byte_identical_reviewed_projection():
    rounds = season.require_valid(reviewed())
    directory = os.path.join(ROOT, "questions", "bundles")
    paths = sorted(name for name in os.listdir(directory) if name.endswith(".json"))
    assert paths
    for name in paths:
        path = os.path.join(directory, name)
        with open(path) as fh:
            committed = json.load(fh)
        rebuilt = bundle.build_bundle(rounds, committed["batch_id"])
        assert bundle.canonical(rebuilt) == bundle.canonical(committed), name
        assert bundle.check_bundle(rebuilt) == [], (name, bundle.check_bundle(rebuilt))


def test_a_withdrawn_round_keeps_its_evidence_and_leaves_the_season():
    """`questions/legacy/` is where a round goes when it is pulled before its
    lock. Three properties, because dropping any one of them turns a withdrawal
    into a quiet deletion:

    - it is gone from the season, so nothing calls an endpoint for it again;
    - it says when and why it was withdrawn, in the file itself;
    - whatever was already bought for it is still on disk. A round pulled after
      four entrants answered it cost four calls; deleting those files would
      throw away the only record that they were made.

    The precedent this replaces: `yougov-xtab-2026-09` was withdrawn in
    September by deleting the round, its forecast and its lock snapshot in one
    commit, which left no trace of the question ever having been asked.
    """
    import glob
    legacy = sorted(glob.glob(os.path.join(ROOT, "questions", "legacy", "*.json")))
    if not legacy:
        print("ok test_a_withdrawn_round_keeps_its_evidence_and_leaves_the_season "
              "(nothing withdrawn yet)")
        return
    with open(os.path.join(ROOT, "questions", "season0.json")) as fh:
        live = {r["round_id"] for r in json.load(fh)["rounds"]}
    for path in legacy:
        with open(path) as fh:
            r = json.load(fh)
        rid = r["round_id"]
        assert rid == os.path.basename(path)[:-5], path
        assert rid not in live, f"{rid} is in questions/legacy and still in the season"
        assert r.get("withdrawn_at"), f"{rid} does not say when it was withdrawn"
        assert len(r.get("withdrawn_reason") or "") > 40, \
            f"{rid} does not say why it was withdrawn"
        for keep in (os.path.join(ROOT, "forecasts", rid),
                     os.path.join(ROOT, "locks", rid + ".json")):
            if os.path.exists(keep):
                continue
            # A round withdrawn before anything was filed has neither, which is
            # fine; a round that had forecasts must still have them.
            assert not glob.glob(os.path.join(ROOT, "forecasts", rid, "*.json")), \
                f"{rid}: forecasts were deleted with the round"
    print(f"ok test_a_withdrawn_round_keeps_its_evidence_and_leaves_the_season "
          f"({len(legacy)} withdrawn)")


def test_no_round_asks_for_a_base_its_publisher_stopped_reporting():
    """Economist/YouGov moved its whole wave to a registered-voter base on
    2026-09-08, and the adult-base cuts stop at 2026-08-29. A round left
    pointing at one of them locks, releases and never scores -- which costs an
    entrant a call and the season a row, so the six that did were removed
    rather than left to rot in the resolver's report.

    `yougov_strong_approval` itself is kept on the adult base, with no future
    round on it. `yougov-2026-w39-strong-approval` already locked against that
    base with fourteen sealed forecasts behind it, and repointing its series
    would have answered an adults question with a voters number -- one that
    was public eleven days before that lock. So the new base is a new id.
    """
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc)

    def at(value):
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))

    rounds = reviewed()["rounds"]
    stalled = {"yougov_approval", "yougov_strong_approval",
               "yougov_weak_approval", "yougov_cost_approval",
               "yougov_trade_approval"}
    future = [r for r in rounds if at(r["lock_at"]) > now]
    on_stalled = [r["round_id"] for r in future if r.get("series") in stalled]
    assert not on_stalled, \
        f"these lock against a base the publisher no longer reports: {on_stalled}"

    strong = [r for r in future if "strong-approval" in r["round_id"]]
    assert strong, "the strong rounds vanished; they were meant to be moved"
    for r in strong:
        assert r["series"] == "yougov_rv_strong_approval", r["round_id"]
        assert "registered voters" in r["question"], r["round_id"]


def test_every_filed_forecast_belongs_to_a_live_or_withdrawn_round():
    """The other half of `questions/legacy/`, and the half a test can enforce.

    That directory exists because `yougov-xtab-2026-09` was pulled in
    September by deleting the round, its forecast and its snapshot in one
    commit, leaving no trace the question had ever been asked. The rule since:
    out of the season, says why, evidence stays.

    `test_a_withdrawn_round_keeps_its_evidence_and_leaves_the_season` checks
    the rounds that are in `legacy/`. It cannot check the ones that should be
    and are not, so this walks the evidence from the other end: a `forecasts/`
    directory with no live round and no withdrawal record is a round that was
    deleted rather than withdrawn, which is the shape of that mistake.

    Written first as its own opposite -- asserting a removed round must leave
    *nothing* behind -- which codified the practice `legacy/` replaced. Kept
    pointing the right way as a reminder that a convention with a test behind
    it can still be reversed by someone who did not read the commit that set
    it.
    """
    import glob
    live = {r["round_id"] for r in reviewed()["rounds"]}
    withdrawn = {os.path.basename(path)[:-len(".json")]
                 for path in glob.glob(os.path.join(
                     ROOT, "questions", "legacy", "*.json"))}
    # `_example` is the file a new entrant copies; it answers no round.
    allowed = live | withdrawn | {"_example"}
    stray = sorted(os.path.basename(d)
                   for d in glob.glob(os.path.join(ROOT, "forecasts", "*"))
                   if os.path.isdir(d) and os.path.basename(d) not in allowed)
    assert not stray, (
        "these hold filed forecasts but are in neither the season nor "
        f"questions/legacy/: {stray}")



if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_")]
    for test in tests:
        test()
        print("ok", test.__name__)
    print(f"{len(tests)} passed")
