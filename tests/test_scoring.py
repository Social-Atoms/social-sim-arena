"""Hand-checked unit tests. Run: python -m tests.test_scoring"""
import math

from ssa import scoring, baselines, average


def close(a, b, tol=1e-3):
    assert abs(a - b) < tol, f"{a} != {b}"


def test_crps():
    # At the mean, CRPS of a standard normal is sd * (2/sqrt(2*pi) - 1/sqrt(pi)) ~ 0.23370
    close(scoring.crps_normal(0, 1, 0), 0.23370)
    # Scales linearly with sd
    close(scoring.crps_normal(0, 2, 0), 0.46739)
    # Far from the mean it approaches absolute error
    close(scoring.crps_normal(0, 1, 10), 10 - 1 / math.sqrt(math.pi), tol=1e-2)
    # sd=0 degenerates to absolute error
    close(scoring.crps_normal(40, 0, 42), 2.0)


def test_rps():
    # Perfect confident forecast scores 0
    close(scoring.rps([0, 1, 0], 1), 0.0)
    # Uniform over 3 ordered bins, outcome in the middle
    close(scoring.rps([1 / 3, 1 / 3, 1 / 3], 1), ((1 / 3) ** 2 + (1 / 3) ** 2) / 2)


def test_skill():
    close(scoring.skill(0.5, 1.0), 0.5)
    close(scoring.skill(1.0, 1.0), 0.0)
    close(scoring.skill(2.0, 1.0), -1.0)


def test_baselines():
    hist = [{"date": f"2026-06-{d:02d}", "value": 40 + d * 0.1} for d in range(1, 11)]
    p = baselines.persistence(hist)
    close(p["mean"], 41.0)
    t = baselines.trend(hist, "2026-06-12")
    assert 40.5 < t["mean"] < 41.6, t
    # clamped horizon: a far target must not run away
    t_far = baselines.trend(hist, "2026-12-01")
    assert abs(t_far["mean"] - 41.0) < 4, t_far


if __name__ == "__main__":
    test_crps()
    test_rps()
    test_skill()
    test_baselines()
    print("all scoring tests pass")
