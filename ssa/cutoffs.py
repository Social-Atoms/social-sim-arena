"""Training-data cutoffs for the model entrants.

A backtest is only contamination-free over releases the model could not have
memorized, so the boundary that matters is the training-data cutoff. It is also
the weakest link in the design, because no vendor publishes a verifiable one and
several publish nothing at all. Every row carries `confidence`; anything below
"declared" is an upper bound on what we know, not a fact, and the paper has to
say so.

`MARGIN_DAYS` pushes the usable window past the stated date. Cutoffs leak in one
direction: a document crawled two months late can still describe the month
before the cutoff, so the final weeks of a stated range are the least
trustworthy part of it. Starting exactly at the cutoff would be the one choice
guaranteed to be wrong.

Three entrants cannot be backtested at all right now, and that is arithmetic
rather than a configuration choice: their cutoffs fall at or after the right
edge of the data (2026-08-05). They run live only, which is precisely the case
the live arena exists to cover -- there is no other way to evaluate a
July-2026-cutoff model without contamination.

This table is keyed by *entrant*, not by model id. Changing an entrant's model
without revisiting its row silently dates the window from the previous model's
boundary, so upgrading a model and updating its cutoff are one change.
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
    "claude-sonnet": {
        "date": "2026-01-01", "confidence": "declared",
        "source": "Anthropic: trained on data up until January 2026",
    },
    "claude-fable": {
        "date": "2026-01-01", "confidence": "declared",
        "source": "Anthropic: trained on data up until January 2026",
    },
    "grok": {
        "date": "2026-02-01", "confidence": "declared",
        "source": "xAI: Grok 4.5 cutoff 2026-02-01 without search tools",
    },
    "gemini-pro": {
        "date": "2026-02-13", "confidence": "reported",
        "source": "Gemini 3.1 Pro, February 13 2026",
    },
    "gpt-5.6-luna": {
        "date": "2026-02-16", "confidence": "declared",
        "source": "OpenAI model page: GPT-5.6 family cutoff February 16 2026",
    },
    "gpt-5.6-sol": {
        "date": "2026-02-16", "confidence": "declared",
        "source": "OpenAI model page: GPT-5.6 family cutoff February 16 2026",
    },
    "gpt-5.6-terra": {
        "date": "2026-02-16", "confidence": "declared",
        "source": "OpenAI model page: GPT-5.6 family cutoff February 16 2026",
    },
    "glm": {
        "date": "2026-03-01", "confidence": "reported",
        "source": "GLM-5.2, March 2026; Zhipu publishes no exact day",
    },
    "claude-opus": {
        "date": "2026-05-01", "confidence": "declared",
        "source": "Anthropic: trained on data up until May 2026",
    },
    "qwen": {
        "date": "2026-05-20", "confidence": "reported",
        "source": "Qwen3.7-Max, 2026-05-20",
    },
    # --- cutoffs at or past the right edge of the data: live only ----------
    "minimax": {
        "date": "2026-06-01", "confidence": "unknown",
        "source": "MiniMax publishes no cutoff; 2026-06-01 assumed by the "
                  "maintainers and treated as live-only rather than backtested",
    },
    "kimi": {
        "date": "2026-07-13", "confidence": "reported",
        "source": "Kimi K3, 2026-07-13; the technical report states none",
    },
    "gemini-flash": {
        "date": "2026-07-21", "confidence": "reported",
        "source": "Gemini 3.6 Flash, July 21 2026",
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
    the worst of both -- a contaminated row under a clean start date.
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
