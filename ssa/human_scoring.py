"""Scoring rules for Human Wisdom point answers.

Human answers deliberately do not pass through the agent forecast contract.
They are point estimates, choices, profiles, or rankings and live on separate
boards. Lower loss is better; skill uses the same persistence comparison as
the agent boards without pretending that the underlying losses are identical.
"""
import math

from ssa import ranking_round


def absolute_error(answer, outcome):
    """Loss for one continuous Human Wisdom point estimate."""
    return abs(float(answer) - float(outcome))


def zero_one_loss(answer, outcome):
    """Loss for Yes/No and ordinary multiple-choice answers."""
    return 0.0 if str(answer).strip().casefold() == str(outcome).strip().casefold() else 1.0


def profile_rmse(answer, outcome):
    """Root mean squared error over one complete declared profile."""
    if not answer or set(answer) != set(outcome):
        raise ValueError("human profile and outcome must contain the same cells")
    squared = [(float(answer[cell]) - float(outcome[cell])) ** 2 for cell in sorted(outcome)]
    return math.sqrt(sum(squared) / len(squared))


def ranking_loss(answer, outcome, kind="fixed", persistence=0.9):
    """Use the arena's existing ranking rules without mixing leaderboards."""
    if kind == "fixed":
        return ranking_round.kendall_loss(answer, outcome)[0]
    if kind == "open":
        return ranking_round.rbo_loss(answer, outcome, persistence, len(outcome))
    raise ValueError(f"unknown human ranking kind: {kind}")


def skill(loss, persistence_loss):
    """Positive means the human answer beat the declared persistence answer."""
    if persistence_loss == 0:
        return 0.0
    return 1.0 - float(loss) / float(persistence_loss)
