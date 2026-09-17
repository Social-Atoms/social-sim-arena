"""Refresh ordering for sealed platform and participant-pushed forecasts."""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest import mock

from ssa import refresh, seal


def test_finalize_reveals_both_paths_before_crowd_and_stamp():
    calls = []
    rounds = [{"round_id": "round-one"}]
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    with mock.patch.object(refresh, "reveal_locked_forecasts",
                           side_effect=lambda *_: calls.append("platform") or 1), \
            mock.patch.object(refresh, "reveal_signed_forecasts",
                              side_effect=lambda *_: calls.append("signed") or 2), \
            mock.patch.object(refresh, "file_crowd_forecasts",
                              side_effect=lambda *_: calls.append("crowd") or 1), \
            mock.patch.object(refresh, "count_forecasts",
                              side_effect=lambda *_: calls.append("count")), \
            mock.patch.object(refresh, "stamp_locked_rounds",
                              side_effect=lambda *_: calls.append("stamp") or ["stamp"]):
        assert refresh.finalize_locked_forecasts(rounds, now) == (1, 2, 1, ["stamp"])
    assert calls == ["platform", "signed", "crowd", "count", "stamp"]


def test_signed_reveal_is_flagged_fail_closed_and_uses_protected_tip():
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    with mock.patch.dict(os.environ, {"SSA_SIGNED_REVEAL_ENABLED": "0"}):
        assert refresh.reveal_signed_forecasts(now) == 0
    with mock.patch.dict(os.environ, {"SSA_SIGNED_REVEAL_ENABLED": "1"}, clear=False):
        os.environ.pop("SSA_AGE_IDENTITIES", None)
        try:
            refresh.reveal_signed_forecasts(now)
        except seal.SealError:
            pass
        else:
            raise AssertionError("enabled signed reveal ran without identities")
    with mock.patch.dict(os.environ, {"SSA_SIGNED_REVEAL_ENABLED": "1",
                                      "SSA_AGE_IDENTITIES": '["AGE-SECRET"]'}), \
            mock.patch("ssa.forecast_reveal.reveal", return_value=3) as run:
        assert refresh.reveal_signed_forecasts(now) == 3
    run.assert_called_once_with(
        refresh.ROOT, "refs/remotes/origin/sealed", ["AGE-SECRET"], now=now,
        base="HEAD", sealed_tip="refs/remotes/origin/sealed")


def _forecast(root, entrant, mean):
    directory = os.path.join(root, "round-one")
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, entrant + ".json"), "w") as f:
        json.dump({"round_id": "round-one", "entrant": entrant,
                   "topline": {"mean": mean, "sd": 1.0}}, f)


def test_rollout_crowd_waits_for_reveals_then_includes_signed_answers_once():
    deadline = datetime(2030, 1, 2, tzinfo=timezone.utc)
    round_def = {"round_id": "round-one", "status": "open",
                 "lock_at": deadline.isoformat().replace("+00:00", "Z")}
    with tempfile.TemporaryDirectory() as directory, \
            mock.patch.object(refresh, "FORECASTS", directory), \
            mock.patch.object(seal, "eligible", return_value=True):
        _forecast(directory, "platform-answer", 10)
        assert refresh.file_crowd_forecasts(
            [round_def], deadline - timedelta(seconds=1)) == 0
        assert not os.path.exists(os.path.join(directory, "round-one", "crowd.json"))

        # This file stands for an answer materialized by signed POST reveal.
        _forecast(directory, "signed-answer", 14)
        assert refresh.file_crowd_forecasts([round_def], deadline) == 1
        with open(os.path.join(directory, "round-one", "crowd.json")) as f:
            crowd = json.load(f)
        assert abs(crowd["topline"]["mean"] - 12) < 0.05
        assert "pool of the 2 forecasts" in crowd["notes"]

        _forecast(directory, "too-late-to-rewrite-final", 100)
        assert refresh.file_crowd_forecasts(
            [dict(round_def, status="locked")], deadline + timedelta(hours=1)) == 0
        with open(os.path.join(directory, "round-one", "crowd.json")) as f:
            assert json.load(f) == crowd


def test_count_forecasts_publishes_only_signed_receipt_status_prelock():
    rounds = [{"round_id": "round-one"}]
    public = {"round-one": {"signed-answer": {
        "filed": "2030-01-01T00:10:00Z", "sealed": True}}}
    with tempfile.TemporaryDirectory() as directory, \
            mock.patch.object(refresh, "FORECASTS", directory), \
            mock.patch.object(refresh, "first_commit_times", return_value={}), \
            mock.patch.dict(os.environ, {"SSA_SIGNED_REVEAL_ENABLED": "1"}), \
            mock.patch("ssa.forecast_reveal.public_status", return_value=public) as status:
        refresh.count_forecasts(rounds)
    status.assert_called_once_with(refresh.ROOT, rounds)
    assert rounds[0]["forecasts"] == public["round-one"]
    assert rounds[0]["n_forecasts"] == 1
    assert set(rounds[0]["forecasts"]["signed-answer"]) == {"filed", "sealed"}


if __name__ == "__main__":
    test_finalize_reveals_both_paths_before_crowd_and_stamp()
    test_signed_reveal_is_flagged_fail_closed_and_uses_protected_tip()
    test_rollout_crowd_waits_for_reveals_then_includes_signed_answers_once()
    test_count_forecasts_publishes_only_signed_receipt_status_prelock()
    print("all signed refresh tests passed")
