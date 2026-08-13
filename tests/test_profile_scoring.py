"""Hand-checked unit tests for profile scoring.

Run: python -m tests.test_profile_scoring
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa import scoring
from ssa.adapters import yougov_xtab


def close(a, b, tol=1e-3):
    assert abs(a - b) < tol, f"{a} != {b}"


def test_energy_reduces_to_crps():
    # In one dimension the energy score is CRPS, which is the whole reason it
    # is the right generalization: crosstab rounds and topline rounds stay on
    # the same scale.
    xs = [1.0, 2.0, 3.0, 7.0]
    close(scoring.energy_score([[x] for x in xs], [4.0]),
          scoring.crps_samples(xs, 4.0))


def test_energy_degenerate():
    # A point mass scores its own distance to the outcome, no spread credit.
    close(scoring.energy_score([[3.0, 4.0]] * 5, [0.0, 0.0]), 5.0)


def test_energy_rewards_the_right_joint():
    # Same marginals, different dependence. The outcome moved both cells the
    # same way, so the entrant who believed they move together must score
    # better, and averaged per-cell CRPS must not see the difference.
    together = [[-2.0, -2.0], [2.0, 2.0]]
    apart = [[-2.0, 2.0], [2.0, -2.0]]
    outcome = [2.0, 2.0]
    assert scoring.energy_score(together, outcome) < scoring.energy_score(apart, outcome)
    per_cell = lambda s: sum(
        scoring.crps_samples([x[k] for x in s], outcome[k]) for k in range(2))
    close(per_cell(together), per_cell(apart))


def test_profile_samples_are_deterministic_and_calibrated():
    means, sds = [50.0, 30.0], [2.0, 3.0]
    a = scoring.profile_samples(means, sds)
    b = scoring.profile_samples(means, sds)
    assert a == b, "scoring must not depend on randomness"
    for k, (m, s) in enumerate(zip(means, sds)):
        col = [x[k] for x in a]
        close(sum(col) / len(col), m, tol=0.05)
        sd = math.sqrt(sum((v - m) ** 2 for v in col) / len(col))
        close(sd, s, tol=0.05)


def test_profile_decomposition():
    # An entrant that nails the shape but is 5 points low everywhere should
    # carry its error in the level term, not the structure term.
    truth = [80.0, 40.0, 10.0]
    shifted = [[v - 5.0 for v in truth]] * 3
    r = scoring.profile_scores(shifted, truth)
    close(r["level"], 5.0)
    close(r["structure"], 0.0)
    # And the mirror image: right level, wrong shape.
    flat = [[sum(truth) / 3] * 3] * 3
    r2 = scoring.profile_scores(flat, truth)
    close(r2["level"], 0.0)
    assert r2["structure"] > 20.0


def test_noise_floor_recovers_a_known_process():
    # Level plus independent noise, built so the answer is known: a level that
    # drifts by 0.5 points a week under noise of 2 points. Fixed seed, so the
    # expected values below are the ones this test will always see.
    import random
    rng = random.Random(11)
    level, series = 50.0, []
    for _ in range(4000):
        level += rng.gauss(0, 0.5)
        series.append(level + rng.gauss(0, 2.0))
    m, s = scoring.noise_floor(series)
    close(m, 2.0, tol=0.1)
    # s is only weakly identified when noise dominates: here it is 3% of the
    # variance being decomposed, so a 1% error in m costs 20% of s. That is
    # the regime every tracker in the arena sits in, and it is why the score
    # scale depends on m alone.
    close(s, 0.5, tol=0.2)


def test_noise_floor_stays_admissible():
    # A mean-reverting level pushes the lag-1 autocorrelation past -0.5, which
    # no level-plus-noise process can produce. The estimate must stop at the
    # edge rather than inflate, or arena scores run past 100.
    series = [50.0 + (5.0 if i % 2 else -5.0) * (1 + 0.3 * (i % 3)) for i in range(60)]
    d = [series[i + 1] - series[i] for i in range(len(series) - 1)]
    var = sum((x - sum(d) / len(d)) ** 2 for x in d) / len(d)
    m, s = scoring.noise_floor(series)
    assert m * m <= var / 2 + 1e-9


def test_arena_score_endpoints():
    # Persistence sits at 0 and the noise floor sits at 100 by construction.
    noise, per = 1.0, 2.0
    close(scoring.arena_score(per, per, noise), 0.0)
    close(scoring.arena_score(noise / scoring.SQRT_PI, per, noise), 100.0)
    assert scoring.arena_score(per * 2, per, noise) < 0


def test_yougov_roster_is_coherent():
    cells = yougov_xtab.SCORED_CELLS
    assert len(cells) == len(set(cells)), "a cell is registered twice"
    assert yougov_xtab.TOPLINE not in cells, "the topline is not a subgroup"
    for name in yougov_xtab.EXCLUDED:
        assert name not in cells, f"{name} is excluded and scored"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
