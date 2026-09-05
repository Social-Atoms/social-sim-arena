"""The subject taxonomy: every series placed, and placed only once.

Run: PYTHONPATH=. python tests/test_domains.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa import domains, series  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASSED = []


def ok(name):
    PASSED.append(name)
    print("ok", name)


def season_rounds():
    with open(os.path.join(ROOT, "questions", "season0.json")) as fh:
        return json.load(fh)["rounds"]


def test_every_registered_series_has_a_subject():
    """A series with no domain is a round the board cannot file anywhere.

    This is the check that makes `domain_of` safe to call from `refresh`:
    the raise there is a real failure mode, and this is where it is caught --
    when the series is added, not on the Monday its first round is asked.
    """
    missing = sorted(s for s in series.SERIES if not domains.known(s))
    assert not missing, (
        f"{len(missing)} registered series have no domain in ssa/domains.py: "
        f"{', '.join(missing[:8])}")
    ok("test_every_registered_series_has_a_subject")


def test_every_round_in_the_frozen_season_resolves_to_a_subject():
    rounds = season_rounds()
    for r in rounds:
        d = domains.domain_of(r["series"])
        assert d in domains.IDS, f"{r['round_id']}: domain {d!r} is not one of ours"
    assert len(rounds) > 50, "season looks empty; this test would pass vacuously"
    ok("test_every_round_in_the_frozen_season_resolves_to_a_subject")


def test_the_publish_path_names_an_unplaced_series_instead_of_stopping():
    """`refresh` asks with strict=False. A gap in the table is a thing to fix
    on Monday, not a reason for the six-hourly data refresh to stop -- and the
    round still reaches the page, under a heading that says what happened."""
    assert domains.domain_of("nobody_registered_this", strict=False) \
        == domains.UNPLACED
    assert domains.UNPLACED not in domains.IDS, \
        "UNPLACED must not become a fifth subject heading"
    counts = domains.counts([{"series": "nobody_registered_this"}])
    assert counts[domains.UNPLACED] == 1, "an unplaced round was dropped"
    ok("test_the_publish_path_names_an_unplaced_series_instead_of_stopping")


def test_an_unplaced_series_raises_rather_than_defaulting():
    """The whole point of the module. A silent default would file a new round
    under whichever heading the fallback names, and tell nobody."""
    try:
        domains.domain_of("some_series_nobody_registered")
    except domains.UnknownDomain as err:
        assert "ssa/domains.py" in str(err), "the error must say where to fix it"
    else:
        raise AssertionError("an unknown series was given a domain")
    assert domains.known("civiqs_net_approval")
    assert not domains.known("some_series_nobody_registered")
    ok("test_an_unplaced_series_raises_rather_than_defaulting")


def test_the_four_subjects_are_declared_once_and_agree_with_each_other():
    ids = [d for d, _, _ in domains.DOMAINS]
    assert len(ids) == len(set(ids)), "a domain id is declared twice"
    assert tuple(ids) == domains.IDS
    assert set(domains.LABELS) == set(ids)
    for _, label, blurb in domains.DOMAINS:
        assert label and blurb.endswith("."), f"{label!r} needs a sentence"
    ok("test_the_four_subjects_are_declared_once_and_agree_with_each_other")


def test_counts_keeps_empty_subjects_so_a_board_does_not_lose_a_heading():
    """A domain that disappears when nothing is scheduled reads to a visitor
    as a domain that was removed from the arena."""
    one = [r for r in season_rounds() if r["series"] == "civiqs_net_approval"]
    assert one, "expected the approval tracker in the season"
    counts = domains.counts(one)
    assert set(counts) == set(domains.IDS), "a heading vanished"
    assert counts["public-opinion"] == len(one)
    assert counts["elections"] == 0
    ok("test_counts_keeps_empty_subjects_so_a_board_does_not_lose_a_heading")


def test_the_economic_civiqs_questions_are_not_filed_under_approval():
    """The reason this module keys on the series rather than the tracker.

    Civiqs publishes both the approval tracker and four questions about how
    the economy feels. Filing all fifty-two Civiqs rounds under one heading
    would tell a visitor the arena asks one question fifty-two times.
    """
    assert domains.domain_of("civiqs_net_approval") == "public-opinion"
    for econ in ("civiqs_net_econ_now", "civiqs_net_econ_direction",
                 "civiqs_net_family_finances", "civiqs_net_inflation_concern"):
        assert domains.domain_of(econ) == "consumer", econ
    # And the same publisher's two answers landing either side of elections.
    assert domains.domain_of("yougov_approval") == "public-opinion"
    assert domains.domain_of("yougov_generic_margin") == "elections"
    ok("test_the_economic_civiqs_questions_are_not_filed_under_approval")


def test_the_publish_gate_requires_a_subject_on_every_round():
    """`site/leaderboard.html` groups the batch on `domain`, so a round
    without one renders under an empty heading on a public page.

    Asserted against the gate rather than against the committed
    `site/data.json`: that file is rebuilt by the six-hourly cron, so a test
    reading it would be red on every checkout between adding a field and the
    next refresh, and green afterwards for reasons no reviewer can see.
    """
    from ssa import refresh
    assert "domain" in refresh.SITE_ROUND_FIELDS, (
        "assert_site_contract must refuse to publish a round with no domain")
    published = [k for k in refresh.SITE_ROUND_FIELDS]
    assert len(published) == len(set(published)), "a field is gated twice"
    ok("test_the_publish_gate_requires_a_subject_on_every_round")


def test_a_published_payload_that_has_the_field_uses_only_real_subjects():
    """Tolerant of a payload written before the field existed, strict about a
    payload that carries it: a typo'd domain is worse than a missing one."""
    path = os.path.join(ROOT, "site", "data.json")
    with open(path) as fh:
        rounds = json.load(fh)["rounds"]
    seen = [r["domain"] for r in rounds if "domain" in r]
    allowed = set(domains.IDS) | {domains.UNPLACED}
    bad = sorted({d for d in seen if d not in allowed})
    assert not bad, f"published rounds carry unknown domains: {bad}"
    ok("test_a_published_payload_that_has_the_field_uses_only_real_subjects")


if __name__ == "__main__":
    for fn in list(globals().values()):
        if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
            fn()
    print(f"{len(PASSED)} passed")
