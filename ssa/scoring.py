"""Scoring rules. All strictly proper (Gneiting and Raftery 2007).

- CRPS for continuous toplines, submitted as a normal (mean, sd).
  Plain words: absolute error, generalized to distributions. Confident and
  right scores best, confident and wrong scores worst. Lower is better.
- RPS for ordered categorical bins.
- Skill: 1 - CRPS(entrant) / CRPS(persistence). Positive means better than
  copying the last release, the same move weather forecasting makes against
  its persistence and climatology nulls.
"""
import math

SQRT_PI = math.sqrt(math.pi)


def _phi(z):  # standard normal pdf
    return math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)


def _Phi(z):  # standard normal cdf
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def crps_normal(mean, sd, outcome):
    """Closed form CRPS for a normal predictive distribution."""
    if sd <= 0:
        return abs(outcome - mean)
    z = (outcome - mean) / sd
    return sd * (z * (2 * _Phi(z) - 1) + 2 * _phi(z) - 1 / SQRT_PI)


def rps(probs, outcome_index):
    """Ranked probability score for ordered bins. probs sums to 1."""
    total = 0.0
    cum_f = 0.0
    for i, p in enumerate(probs):
        cum_f += p
        cum_o = 1.0 if i >= outcome_index else 0.0
        total += (cum_f - cum_o) ** 2
    return total / (len(probs) - 1) if len(probs) > 1 else total


def skill(entrant_crps, persistence_crps):
    """Positive = better than persistence. Undefined if persistence is perfect."""
    if persistence_crps == 0:
        return 0.0
    return 1.0 - entrant_crps / persistence_crps
