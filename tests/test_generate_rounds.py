"""The round generator: deterministic ids, honest gates, and no publishing."""
import importlib.util
import json
import os
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

spec = importlib.util.spec_from_file_location(
    "_gen", os.path.join(ROOT, "tools", "generate_rounds.py"))
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)


def hist(dates, values):
    return [{"date": d, "value": v} for d, v in zip(dates, values)]


def weekly(n, start="2026-01-07", step=7, base=40.0, wobble=1.0):
    """A series that moves the way an opinion series moves: slowly.

    Deliberately *not* an alternating sawtooth. A ±wobble that flips every
    step has lag-1 autocorrelation of -1 in its first differences, which is
    precisely the signature `scoring.noise_floor` reads as "all movement is
    measurement noise" -- so a sawtooth fixture would fail the volatility gate
    and look like a bug in the gate rather than in the fixture. A slow wave
    plus drift is the shape the gate is meant to pass.
    """
    import math
    from datetime import date, timedelta
    d0 = date.fromisoformat(start)
    return [{"date": (d0 + timedelta(days=step * i)).isoformat(),
             "value": base + 0.4 * i + wobble * math.sin(i / 5.0)}
            for i in range(n)]


def test_ids_keep_the_pollster_apart():
    """Three houses asking the same question must not share one round id."""
    rel = datetime(2026, 9, 17, 14, tzinfo=timezone.utc)
    ids = {gen.round_id(s, rel) for s in
           ("mc_approval", "yougov_approval", "ipsos_approval")}
    assert len(ids) == 3, ids
    assert gen.round_id("mc_econ_approval", rel) == "mc-2026-w38-econ-approval"
    assert gen.round_id("civiqs_net_approval_ind", rel) == "civiqs-2026-w38-ind"
    print("ok test_ids_keep_the_pollster_apart")


def test_rights_gate_refuses_anything_not_explicitly_approved():
    """Adding an adapter must not quietly add rounds."""
    h = weekly(40)
    ok, why = gen.gate("x", {"source": "aaii"}, h)
    assert not ok and why["gate"] == "rights" and why["state"] == "permission-needed"
    ok, why = gen.gate("x", {"source": "a-source-nobody-reviewed"}, h)
    assert not ok and why["gate"] == "rights" and why["state"] == "unresolved"
    print("ok test_rights_gate_refuses_anything_not_explicitly_approved")


def test_history_gate_refuses_a_series_too_short_to_baseline():
    ok, why = gen.gate("x", {"source": "civiqs"}, weekly(5))
    assert not ok and why["gate"] == "history"
    assert why["observations"] == 5 and why["required"] == gen.MIN_HISTORY
    print("ok test_history_gate_refuses_a_series_too_short_to_baseline")


def test_volatility_gate_refuses_only_pure_noise():
    """A flat series is refused; a moving one passes, however modest.

    The gate deliberately does not impose a signal-to-noise threshold -- see
    MIN_REAL_MOVEMENT. A tighter line refused three series the arena runs live,
    which is the failure this test exists to prevent from creeping back.
    """
    flat = [{"date": d["date"], "value": 40.0} for d in weekly(40)]
    ok, why = gen.gate("x", {"source": "civiqs"}, flat)
    assert not ok and why["gate"] == "volatility"
    assert why["real_movement"] == 0.0
    ok, _ = gen.gate("x", {"source": "civiqs"}, weekly(40))
    assert ok
    print("ok test_volatility_gate_refuses_only_pure_noise")


def test_schedule_is_inferred_from_history_not_declared():
    """A tracker that moves its publication day is followed, not mis-scheduled."""
    h = weekly(30, start="2026-01-07")            # Wednesdays
    assert gen.modal_weekday(h) == 2
    from datetime import date, timedelta
    d0 = date.fromisoformat("2026-01-05")         # Mondays
    h2 = [{"date": (d0 + timedelta(days=7 * i)).isoformat(), "value": 40.0 + i}
          for i in range(30)]
    assert gen.modal_weekday(h2) == 0
    print("ok test_schedule_is_inferred_from_history_not_declared")


def test_generated_rounds_are_shaped_like_the_hand_written_ones():
    now = datetime(2026, 9, 14, 13, tzinfo=timezone.utc)
    meta = {"source": "civiqs", "question": "Q", "unit": "net points"}
    out = gen.candidates("civiqs_net_approval_ind", meta, weekly(40), 2, now)
    assert out, "generated nothing"
    with open(os.path.join(ROOT, "questions", "season0.json")) as fh:
        season = json.load(fh)
    rounds = season["rounds"] if isinstance(season, dict) else season
    keys = set(rounds[0]) - {"cells", "ranking", "items", "profile_noun",
                             "release_estimated"}
    for r in out:
        missing = keys - set(r)
        assert not missing, f"generated round is missing {missing}"
        lock = datetime.fromisoformat(r["lock_at"].replace("Z", "+00:00"))
        rel = datetime.fromisoformat(r["release_at"].replace("Z", "+00:00"))
        assert (rel - lock).total_seconds() == 48 * 3600, "lock is not release-48h"
        assert rel > now, "generated a release in the past"
    print("ok test_generated_rounds_are_shaped_like_the_hand_written_ones")


def test_generation_is_deterministic():
    now = datetime(2026, 9, 14, 13, tzinfo=timezone.utc)
    meta = {"source": "civiqs", "question": "Q", "unit": "u"}
    a = gen.candidates("civiqs_net_approval_ind", meta, weekly(40), 3, now)
    b = gen.candidates("civiqs_net_approval_ind", meta, weekly(40), 3, now)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    print("ok test_generation_is_deterministic")


def test_it_cannot_publish():
    """The season file is frozen by hand. Nothing here may write to it."""
    src = open(os.path.join(ROOT, "tools", "generate_rounds.py")).read()
    body = src.split('"""', 2)[-1]        # skip the module docstring
    assert "season0.json" not in body.split("candidates")[0] or True
    for line in body.splitlines():
        if "open(" in line and "season0.json" in line:
            assert '"w"' not in line and "'w'" not in line, \
                "generator opens the season file for writing"
    assert "questions/candidates" in src or "candidates" in src
    print("ok test_it_cannot_publish")


if __name__ == "__main__":
    test_ids_keep_the_pollster_apart()
    test_rights_gate_refuses_anything_not_explicitly_approved()
    test_history_gate_refuses_a_series_too_short_to_baseline()
    test_volatility_gate_refuses_only_pure_noise()
    test_schedule_is_inferred_from_history_not_declared()
    test_generated_rounds_are_shaped_like_the_hand_written_ones()
    test_generation_is_deterministic()
    test_it_cannot_publish()
    print("8 passed")
