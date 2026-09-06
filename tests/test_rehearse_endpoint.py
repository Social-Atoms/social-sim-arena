"""tools/rehearse_endpoint.py: the cron's own path against one endpoint,
filing nothing. The transport is faked here; against a live endpoint the
tool is run by hand (see its docstring)."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import rehearse_endpoint as re_tool  # noqa: E402
from ssa import harness  # noqa: E402


def test_one_open_round_per_shape_earliest_first():
    data = {"rounds": [
        {"round_id": "b", "release_at": "2026-10-02", "status": "open", "target_type": "continuous_normal"},
        {"round_id": "a", "release_at": "2026-10-01", "status": "open", "target_type": "continuous_normal"},
        {"round_id": "old", "release_at": "2026-09-01", "status": "resolved", "target_type": "profile_energy"},
        {"round_id": "p", "release_at": "2026-10-03", "status": "open", "target_type": "profile_energy"},
    ]}
    picked = re_tool.open_rounds(data, re_tool.SHAPES)
    assert picked["continuous_normal"]["round_id"] == "a"
    assert picked["profile_energy"]["round_id"] == "p"
    assert "ranking_list" not in picked
    print("ok test_one_open_round_per_shape_earliest_first")


def test_the_tool_runs_the_real_filing_path_and_writes_nothing():
    """With only the socket faked, every shape on the site is filed through
    harness.forecast and validated; the repository is untouched."""
    def answer(url, **kw):
        rd = json.loads(kw["data"])["round"]
        if rd["target_type"] == "profile_energy":
            fc = {"profile": {c: {"mean": 1.0, "sd": 1.0} for c in rd["cells"]}}
        elif rd["target_type"] == "ranking_list":
            fc = {"ranking": [f"Item_{i}" for i in range(rd["ranking"]["length"])]}
        else:
            fc = {"mean": 1.0, "sd": 1.0}

        class R:
            status_code = 200
            content = json.dumps({"schema_version": "ssa-agent-api-v2", "forecast": fc}).encode()
        return R()

    before = set(os.listdir(os.path.join(ROOT, "entrants")))
    real_post = harness.requests.post
    harness.requests.post = answer
    try:
        code = re_tool.main(["--url", "https://fake.test/forecast"])
    finally:
        harness.requests.post = real_post
    assert code == 0
    assert set(os.listdir(os.path.join(ROOT, "entrants"))) == before
    assert not os.path.exists(os.path.join(ROOT, "forecasts", "x", "rehearsal.json"))
    print("ok test_the_tool_runs_the_real_filing_path_and_writes_nothing")


if __name__ == "__main__":
    test_one_open_round_per_shape_earliest_first()
    test_the_tool_runs_the_real_filing_path_and_writes_nothing()
    print("rehearse_endpoint tests passed")
