"""Coverage and call health per entrant, from rounds and the replies tree."""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import entrant_status  # noqa: E402


def fixture_tree():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "r1", "failures"))
    os.makedirs(os.path.join(d, "r2"))
    with open(os.path.join(d, "r1", "alpha.abc.json"), "w") as fh:
        json.dump({"entrant": "alpha", "logged_at": "2026-09-01T10:00:00Z", "reply": "..."}, fh)
    with open(os.path.join(d, "r2", "alpha.def.json"), "w") as fh:
        json.dump({"entrant": "alpha", "logged_at": "2026-09-08T10:00:00Z", "reply": "..."}, fh)
    with open(os.path.join(d, "r1", "failures", "beta.abc.json"), "w") as fh:
        json.dump({"entrant": "beta", "attempts_total": 2, "last_failed_at": "2026-09-09T10:00:00Z",
                   "attempts": [{"error_type": "Timeout", "error": "read timed out", "logged_at": "2026-09-09T10:00:00Z"}]}, fh)
    return d


def test_coverage_counts_only_questions_whose_window_closed():
    rounds = [
        {"round_id": "r1", "status": "resolved", "domain": "public-opinion", "forecasts": {"alpha": {"mean": 1}}},
        {"round_id": "r2", "status": "locked", "domain": "consumer", "forecasts": {"alpha": {"mean": 1}, "beta": {"mean": 2}}},
        {"round_id": "r3", "status": "open", "domain": "consumer", "forecasts": {}},
    ]
    entrants = [{"entrant_id": "alpha"}, {"entrant_id": "beta"}]
    out = entrant_status.build(rounds, entrants, root=fixture_tree())
    assert out["alpha"]["asked"] == 2 and out["alpha"]["answered"] == 2
    assert out["beta"]["asked"] == 2 and out["beta"]["answered"] == 1
    assert out["beta"]["by_domain"]["public-opinion"] == {"asked": 1, "answered": 0}
    print("ok test_coverage_counts_only_questions_whose_window_closed")


def test_health_reads_successes_and_failures():
    out = entrant_status.build([], [{"entrant_id": "alpha"}, {"entrant_id": "beta"}, {"entrant_id": "gamma"}], root=fixture_tree())
    assert out["alpha"]["calls"] == 2 and out["alpha"]["last_call_ok"] is True and out["alpha"]["last_call_at"] == "2026-09-08T10:00:00Z"
    assert out["beta"]["failures"] == 2 and out["beta"]["last_call_ok"] is False
    assert out["beta"]["recent_failures"][-1]["error"].startswith("Timeout: read timed out")
    assert out["gamma"]["last_call_at"] is None and out["gamma"]["last_call_ok"] is None
    print("ok test_health_reads_successes_and_failures")


def test_the_real_tree_builds():
    with open(os.path.join(ROOT, "site", "data.json")) as fh:
        data = json.load(fh)
    out = entrant_status.build(data.get("rounds", []), data.get("entrants", []))
    assert len(out) == len(data.get("entrants", []))
    print(f"ok test_the_real_tree_builds ({len(out)} entrants)")


if __name__ == "__main__":
    test_coverage_counts_only_questions_whose_window_closed()
    test_health_reads_successes_and_failures()
    test_the_real_tree_builds()
    print("3 passed")
