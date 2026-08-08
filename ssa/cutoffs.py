"""Training-data cutoffs for the model entrants.

A backtest is only contamination-free over releases the model could not have
memorized, so the boundary that matters is the training-data cutoff. It is also
the weakest link in the whole design, because no vendor publishes a verifiable
one. What the public record actually supports, as of 2026-08:

  claude-opus-5   2026-05  consistent between Anthropic's model overview and
                           third-party trackers
  gpt-5.5         2025-12  reported; users have also seen the model self-report
                           2024-06, so the true boundary is disputed
  gemini-2.5-pro  2025-01  trackers disagree (2025-01 vs 2025-06)
  grok-4          2024-12  third-party only
  deepseek-chat   2024-07  never published; extracted from a system prompt
  qwen-max        unknown  Qwen maintainers state self-reported dates are
                           unreliable, since models are not trained to identify
                           themselves

So this table is evidence, not ground truth. Two consequences are encoded here:

1. Every row carries `confidence`. Anything below "declared" is an upper bound
   on what we know, not a fact, and the paper has to say so.
2. `MARGIN_DAYS` pushes the usable window past the stated date. Cutoffs leak in
   one direction: a document crawled two months late can still describe the
   month before the cutoff, so the final weeks of a stated range are the least
   trustworthy part of it. Starting exactly at the cutoff would be the one
   choice guaranteed to be wrong.

Every entry here needs re-checking whenever an entrant's model id changes: the
table is keyed by *entrant*, not by model, so upgrading `grok` from 4 to 4.5
silently keeps the older model's cutoff and would date the backtest window from
a boundary that no longer applies.
"""
import datetime

# Push the usable window this far past the stated cutoff. Late-crawled
# documents describe earlier months, so the weeks just before a stated cutoff
# are the likeliest to be contaminated.
MARGIN_DAYS = 30

# date: ISO day, or None when nothing credible is published.
# confidence: "declared" (vendor documentation) > "reported" (consistent
#             third-party) > "disputed" (sources disagree) > "unknown".
CUTOFFS = {
    "gpt-5.5": {
        "date": "2025-12-01", "confidence": "disputed",
        "source": "third-party trackers; model has also self-reported 2024-06",
    },
    "claude-opus": {
        "date": "2026-05-01", "confidence": "reported",
        "source": "Anthropic model overview; consistent across trackers",
    },
    "gemini-pro": {
        "date": "2025-01-31", "confidence": "disputed",
        "source": "trackers split between 2025-01 and 2025-06",
    },
    "grok": {
        "date": "2024-12-01", "confidence": "reported",
        "source": "third-party model spec pages",
    },
    "deepseek": {
        "date": "2024-07-01", "confidence": "disputed",
        "source": "extracted from system prompts; DeepSeek publishes nothing",
    },
    "qwen": {
        "date": None, "confidence": "unknown",
        "source": "unpublished; maintainers call self-reported dates unreliable",
    },
}


def _parse(day):
    return datetime.date(*(int(x) for x in day.split("-")))


def cutoff(entrant):
    """Stated cutoff day, or None when nothing credible is published."""
    return (CUTOFFS.get(entrant) or {}).get("date")


def usable_start(entrant, margin_days=MARGIN_DAYS):
    """First release date this entrant can be scored on, cutoff + margin.

    None when the cutoff is unknown: the caller has to decide explicitly, since
    silently assuming a date is how a contaminated number reaches a table.
    """
    day = cutoff(entrant)
    if not day:
        return None
    return (_parse(day) + datetime.timedelta(days=margin_days)).isoformat()


def partition(entrants, margin_days=MARGIN_DAYS):
    """Split into (scorable, unknown). An entrant with no credible cutoff is
    not scorable at all: there is no window we can argue it did not memorize,
    so excluding it from the *window* but leaving it in the *table* would be
    the worst of both -- a contaminated row sitting under a clean start date.
    """
    known = [e for e in entrants if usable_start(e, margin_days)]
    return known, [e for e in entrants if e not in known]


def common_start(entrants, margin_days=MARGIN_DAYS, on_unknown="raise"):
    """Latest usable start across `entrants` -- the only window on which their
    scores are comparable.

    Scoring each model from its own cutoff would put them on different release
    sets, and a mean CRPS over a different set is not a comparable number. So
    the leaderboard window is bounded by the most recently trained entrant.

    on_unknown: "raise" (default), "exclude" (drop entrants with no cutoff), or
    an ISO day to assume for them.
    """
    starts = []
    for e in entrants:
        s = usable_start(e, margin_days)
        if s is None:
            if on_unknown == "raise":
                raise ValueError(
                    f"no published cutoff for '{e}': pass on_unknown='exclude' "
                    "to drop it, or an ISO date to assume one")
            if on_unknown == "exclude":
                continue
            s = (_parse(on_unknown) + datetime.timedelta(days=margin_days)).isoformat()
        starts.append(s)
    if not starts:
        raise ValueError("no entrant has a usable cutoff")
    return max(starts)


def describe(entrant):
    """One-line provenance string, for the notes field and the paper appendix."""
    c = CUTOFFS.get(entrant) or {}
    if not c.get("date"):
        return f"{entrant}: cutoff unknown ({c.get('source', 'no source')})"
    return (f"{entrant}: cutoff {c['date']} [{c['confidence']}] "
            f"-> usable from {usable_start(entrant)} ({c['source']})")
