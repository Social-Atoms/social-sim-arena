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


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_")]
    for test in tests:
        test()
        print("ok", test.__name__)
    print(f"{len(tests)} passed")
