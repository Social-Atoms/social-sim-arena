"""The weekly bundle: one deadline, every shape, and nothing unreviewed in it."""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import batches, bundle                             # noqa: E402

PY = sys.executable
SEASON = bundle.load_season(ROOT)


def _schema():
    with open(os.path.join(ROOT, "schema", "bundle.schema.json")) as fh:
        return json.load(fh)


def test_the_next_bundle_matches_the_schema():
    import jsonschema
    b = bundle.build(SEASON, bundle.next_deadline(
        datetime(2026, 8, 31, 21, tzinfo=timezone.utc)))
    jsonschema.validate(b, _schema())
    assert b["batch_id"] == "batch-2026-09-14"
    assert b["deadline"] == "2026-09-14T12:00:00Z"
    assert b["published_at"] == "2026-09-07T12:00:00Z"
    assert b["schema_version"] == "1.0.0"
    print("ok test_the_next_bundle_matches_the_schema")


def test_the_deadline_is_never_a_round_lock():
    """The one rule a participant-facing payload cannot get wrong.

    `lock_at` is the arena's clock: a round locks 0 to 7 days *after* the
    deadline its entrants were held to. Publishing the lock as the deadline
    would tell an entrant they had up to a week more than they do, and would
    re-open the unequal-vantage problem the batch cutover closed.
    """
    b = bundle.build(SEASON, bundle.next_deadline(
        datetime(2026, 8, 31, 21, tzinfo=timezone.utc)))
    assert b["questions"], "expected a non-empty batch to test against"
    for q in b["questions"]:
        assert q["lock_at"] >= b["deadline"], q["round_id"]
        assert batches.batch_of(q["lock_at"]) == b["batch_id"], q["round_id"]
        want = batches.effective_deadline(q["lock_at"])
        assert want.strftime("%Y-%m-%dT%H:%M:%SZ") == b["deadline"], q["round_id"]
        assert 0 <= q["horizon_days"] <= 7, q["round_id"]
    print("ok test_the_deadline_is_never_a_round_lock")


def test_a_bundle_never_predates_the_cutover():
    """Rounds that closed under the per-round lock rule have no common deadline,
    so there is no honest bundle to build for them."""
    early = datetime(2026, 8, 3, 6, tzinfo=timezone.utc)
    assert bundle.next_deadline(early) == batches.FIRST_DEADLINE
    # And a Monday exactly at 12:00Z rolls to the following week rather than
    # bundling a deadline that has just closed.
    monday = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)
    assert bundle.next_deadline(monday) == datetime(
        2026, 9, 28, 12, tzinfo=timezone.utc)
    print("ok test_a_bundle_never_predates_the_cutover")


def test_every_shape_carries_both_list_fields():
    """`cells` and `items` are always present, even empty.

    A mixed-shape payload whose keys depend on the shape hands its reader a
    KeyError on the first scalar round in the batch, which is the most common
    way this kind of bundle breaks.
    """
    profile = [r for r in SEASON if r.get("target_type") == "profile_energy"]
    scalar = [r for r in SEASON if r.get("target_type") == "continuous_normal"]
    ranking = [r for r in SEASON if r.get("target_type") == "ranking_list"]
    assert profile and scalar and ranking, "season should hold all three shapes"
    for r in profile[:1] + scalar[:1] + ranking:
        q = bundle.question(r)
        assert isinstance(q["cells"], list) and isinstance(q["items"], list)
    assert bundle.question(profile[0])["cells"], "a profile round names its cells"
    assert not bundle.question(scalar[0])["cells"]
    for r in ranking:
        # Every ranking round in season 0 is the Wikipedia top-10, whose answer
        # is drawn from the whole wiki. A named candidate list would be a
        # different and much easier question, so `items` is empty by design.
        assert bundle.question(r)["items"] == [], r["round_id"]
    print("ok test_every_shape_carries_both_list_fields")


def test_a_closed_set_ranking_publishes_its_items():
    """The other ranking kind does name its items, and they must reach the
    entrant: a basket round is an ordering of exactly those five queries."""
    from ssa.series import BASKET
    r = {
        "round_id": "trends-basket-2026-w40", "tracker": "google_trends",
        "series": "trends_basket_us", "question": "Q", "unit": "order",
        "release_at": "2026-10-06T14:00:00Z", "lock_at": "2026-10-04T14:00:00Z",
        "resolve": "archived basket order", "target_type": "ranking_list",
        "ranking": {"kind": "trends_basket", "length": 5, "loss": "kendall",
                    "week_start": "2026-09-27", "week_end": "2026-10-03",
                    "items": list(BASKET)},
    }
    assert bundle.question(r)["items"] == list(BASKET)
    print("ok test_a_closed_set_ranking_publishes_its_items")


def test_the_digest_is_the_repository_canonical_hash():
    """Same serialization as `locks/` and the leaderboard, or a bundle digest
    is not comparable to any other hash this project has published."""
    b = bundle.build(SEASON, batches.FIRST_DEADLINE)
    assert bundle.canonical(b) == json.dumps(
        b, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert bundle.digest(b) == bundle.digest(bundle.build(
        SEASON, batches.FIRST_DEADLINE)), "digest is not stable"
    print("ok test_the_digest_is_the_repository_canonical_hash")


def test_an_empty_batch_is_a_state_not_a_crash():
    """A week with no reviewed round locking in it is a real operating state.
    Raising would make the operator's Monday depend on the season being full."""
    b = bundle.build(SEASON, datetime(2027, 6, 7, 12, tzinfo=timezone.utc))
    assert b["questions"] == []
    assert b["batch_id"] == "batch-2027-06-07"
    print("ok test_an_empty_batch_is_a_state_not_a_crash")


def test_one_command_produces_it_from_a_clean_checkout():
    """The acceptance criterion, run as a command rather than asserted about.

    No network, no keys, no environment: `questions/season0.json` and the
    calendar are the whole input. If producing the bundle ever needed a fetch,
    a source being down on a Monday would mean nobody could be given a question
    that week -- for rounds that do not depend on that fetch at all.
    """
    env = dict(os.environ, PYTHONPATH=ROOT)
    for var in list(env):
        if var.endswith("_API_KEY") or var.startswith("SSA_"):
            env.pop(var)
    out = subprocess.run(
        [PY, os.path.join(ROOT, "tools", "emit_bundle.py"),
         "--now", "2026-08-31T21:00:00Z"],
        capture_output=True, text=True, env=env, cwd=ROOT, timeout=120)
    assert out.returncode == 0, out.stderr
    assert "batch-2026-09-14" in out.stdout
    assert "13 questions" in out.stdout, out.stdout
    print("ok test_one_command_produces_it_from_a_clean_checkout")


def test_it_refuses_a_batch_that_closed_under_the_old_rule():
    env = dict(os.environ, PYTHONPATH=ROOT)
    out = subprocess.run(
        [PY, os.path.join(ROOT, "tools", "emit_bundle.py"),
         "--batch", "batch-2026-08-31"],
        capture_output=True, text=True, env=env, cwd=ROOT, timeout=120)
    assert out.returncode != 0
    assert "predates the batch cutover" in out.stderr, out.stderr
    print("ok test_it_refuses_a_batch_that_closed_under_the_old_rule")


if __name__ == "__main__":
    test_the_next_bundle_matches_the_schema()
    test_the_deadline_is_never_a_round_lock()
    test_a_bundle_never_predates_the_cutover()
    test_every_shape_carries_both_list_fields()
    test_a_closed_set_ranking_publishes_its_items()
    test_the_digest_is_the_repository_canonical_hash()
    test_an_empty_batch_is_a_state_not_a_crash()
    test_one_command_produces_it_from_a_clean_checkout()
    test_it_refuses_a_batch_that_closed_under_the_old_rule()
    print("9 passed")
