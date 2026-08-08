"""The registry of forecastable series -- one declaration per tracker.

Before this, series names were spelled out in five scattered places and their
filters were implicit in adapter code, which had two consequences worth naming:

- **Questions were thrown away.** The Silver Bulletin approval file carries
  issue-specific approval -- economy, immigration, trade, cost -- in the same
  download as overall approval. The pipeline used overall and discarded the
  rest, while the backtest starved for releases. Registering two of them adds
  159 observations and 19 post-cutoff backtest points at no extra fetch.

- **The model was told less than the resolver knew.** A wave's number depends
  on who was asked (adults, registered voters, likely voters) and on the exact
  question wording, and those differ by several points. Any of that the
  resolution uses and the prompt omits is a question the entrant cannot see but
  is graded on. So each row carries the `question` and `methodology` text the
  harness puts in front of the model, and they state the population explicitly.

Adding a tracker means adding a row here, and nothing else.
"""
from .adapters import fredcsv
from .adapters import silverbulletin as sb
from .adapters import umich as umich_adapter

# Set by michigan_history() to whichever source answered.
MICHIGAN_SOURCE = "not yet fetched"


def michigan_history():
    """Michigan sentiment, official table first, FRED second.

    The survey's own file carries the current month; FRED republishes it a
    month late. The official file is also the less reliable of the two: on
    2026-08-08 it served the full 676-row table and then began returning 404
    within the hour, and the site's own download links carry per-request
    tokens, so they cannot be automated. Hence a fallback rather than a swap --
    take the extra month when it is there, never go dark when it is not.

    The lesson this cost: fetches must be mirrored into the repository, which
    the paper already promises and the code does not yet do. Had the earlier
    successful pull been archived, July would still be available now.

    Sets MICHIGAN_SOURCE to whichever source answered, so the site can label the
    number with where it actually came from. A page crediting FRED for a value
    FRED does not carry is wrong in exactly the direction that matters here.
    """
    global MICHIGAN_SOURCE
    try:
        rows = umich_adapter.umich_sentiment()
        MICHIGAN_SOURCE = ("Surveys of Consumers, University of Michigan "
                           "(sca.isr.umich.edu), the survey's own monthly table")
        return rows
    except Exception as e:                         # noqa: BLE001 - reported
        print(f"  official Michigan table unavailable ({type(e).__name__}); "
              "using FRED, which lags one month")
        MICHIGAN_SOURCE = ("FRED (UMCSENT), which republishes the Michigan "
                           "index one month late; the survey's own table was "
                           "unreachable on this run")
        return fredcsv.umich_sentiment()

# source: which adapter and which filters. value: the column to score.
SERIES = {
    "umich_sentiment": {
        "label": "Michigan consumer sentiment",
        "tracker": "umich_sentiment",
        "source": "umich", "value": "value",
        "unit": "index points",
        "cadence": "monthly, preliminary mid-month and final end-month",
        "question": ("University of Michigan Index of Consumer Sentiment "
                     "(ICS, all households), the headline index"),
        "methodology": ("Surveys of Consumers, University of Michigan; roughly "
                        "600-1,000 US adults per month by telephone and web; "
                        "index normalized so 1966 = 100"),
    },
    "yougov_approval": {
        "label": "Economist/YouGov Trump approval",
        "tracker": "economist_yougov",
        "source": "sb_approval",
        "filters": {"subgroup": "All polls", "pollster": "YouGov", "population": "A"},
        "value": "approve", "unit": "% approve",
        "cadence": "weekly, fielded over a weekend and published midweek",
        "question": ("Economist/YouGov weekly tracker: percent of US adult "
                     "citizens who approve of Donald Trump's job performance"),
        "methodology": ("online panel, roughly 1,400-1,700 US adult citizens "
                        "per wave, weighted to adults; approve and disapprove "
                        "exclude 'not sure'"),
    },
    "mc_approval": {
        "label": "Morning Consult Trump approval",
        "tracker": "morning_consult",
        "source": "sb_approval",
        "filters": {"subgroup": "All polls", "pollster": "Morning Consult",
                    "population": "RV"},
        "value": "approve", "unit": "% approve",
        "cadence": "weekly",
        "question": ("Morning Consult weekly tracker: percent of US registered "
                     "voters who approve of Donald Trump's job performance"),
        "methodology": ("online panel, roughly 2,200 registered voters per "
                        "wave; note the registered-voter base reads several "
                        "points different from the same survey's adult base"),
    },
    "yougov_generic_margin": {
        "label": "Economist/YouGov generic ballot margin",
        "tracker": "economist_yougov",
        "source": "sb_generic",
        "filters": {"subgroup": "All polls", "pollster": "YouGov"},
        "value": "net", "unit": "net points, Democratic minus Republican",
        "cadence": "weekly",
        "question": ("Economist/YouGov generic congressional ballot: the "
                     "Democratic margin (Democratic percent minus Republican "
                     "percent) for the 2026 US House elections"),
        "methodology": ("online panel, roughly 1,300-1,600 respondents per "
                        "wave; positive means Democrats lead"),
    },

    # --- issue-specific trackers that share the approval file ---------------
    "yougov_econ_approval": {
        "label": "Economist/YouGov Trump approval on the economy",
        "tracker": "economist_yougov",
        "source": "sb_approval",
        "filters": {"subgroup": "Economy", "pollster": "YouGov"},
        "value": "approve", "unit": "% approve",
        "cadence": "weekly",
        "question": ("Economist/YouGov weekly tracker: percent who approve of "
                     "Donald Trump's handling of the economy"),
        "methodology": ("same waves as the overall approval tracker, asked as a "
                        "separate issue-specific item"),
    },
    "yougov_immig_approval": {
        "label": "Economist/YouGov Trump approval on immigration",
        "tracker": "economist_yougov",
        "source": "sb_approval",
        "filters": {"subgroup": "Immigration", "pollster": "YouGov"},
        "value": "approve", "unit": "% approve",
        "cadence": "weekly",
        "question": ("Economist/YouGov weekly tracker: percent who approve of "
                     "Donald Trump's handling of immigration"),
        "methodology": ("same waves as the overall approval tracker, asked as a "
                        "separate issue-specific item"),
    },
}


def describe(series_id):
    """The question and methodology text an entrant is entitled to see."""
    s = SERIES[series_id]
    return {"question": s["question"], "unit": s["unit"],
            "methodology": s["methodology"], "cadence": s["cadence"]}


def build_all(sources=None):
    """Every registered series as [{date, value}] oldest first.

    `sources` lets callers inject already-fetched payloads (tests, or a run that
    wants one fetch shared across series).
    """
    src = dict(sources or {})
    need = {s["source"] for s in SERIES.values()}
    if "sb_approval" in need and "sb_approval" not in src:
        src["sb_approval"] = sb.fetch(sb.APPROVAL_URL)
    if "sb_generic" in need and "sb_generic" not in src:
        src["sb_generic"] = sb.fetch(sb.GENERIC_URL)
    if "umich" in need and "umich" not in src:
        src["umich"] = michigan_history()

    out = {}
    for sid, spec in SERIES.items():
        f = spec.get("filters") or {}
        if spec["source"] == "umich":
            out[sid] = list(src["umich"])
        elif spec["source"] == "sb_approval":
            recs = sb.approval_polls(rows=src["sb_approval"], **f)
            out[sid] = sb.to_series(recs, spec["value"])
        elif spec["source"] == "sb_generic":
            recs = sb.generic_ballot_polls(rows=src["sb_generic"], **f)
            out[sid] = sb.to_series(recs, spec["value"])
        else:
            raise ValueError(f"{sid}: unknown source {spec['source']}")
        if not out[sid]:
            raise RuntimeError(
                f"{sid} built to zero points -- refusing to publish an empty "
                "series (check the filters against the upstream file)")
    return out
