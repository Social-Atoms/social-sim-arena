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
        # The ICS is a published formula over five specific items, not a
        # question anyone is asked. So the panel answers the five real items
        # and the index is computed from them -- a respondent asked for "the
        # index" would be guessing at a normalisation they cannot see, and a
        # simulated value would not be on the same scale as the real one.
        #
        # se_scale carries the panel's share error into index points: five
        # relative scores, each roughly twice a share's error, over the 1966
        # base of 6.7558.
        "survey": {
            "population": "A",
            "se_scale": 5 * 2 / 6.7558,
            "items": [
                {"key": "pago",
                 "text": ("Would you say that you and your family are better "
                          "off or worse off financially than you were a year "
                          "ago?"),
                 "options": ["better", "same", "worse"]},
                {"key": "pexp",
                 "text": ("Looking ahead, do you think that a year from now "
                          "you and your family will be better off "
                          "financially, or worse off, or just about the same "
                          "as now?"),
                 "options": ["better", "same", "worse"]},
                {"key": "bus12",
                 "text": ("Turning to business conditions in the country as a "
                          "whole, do you think that during the next twelve "
                          "months we will have good times financially, or bad "
                          "times, or what?"),
                 "options": ["good", "uncertain", "bad"]},
                {"key": "bus5",
                 "text": ("Looking ahead, which would you say is more likely: "
                          "that in the country as a whole we will have "
                          "continuous good times during the next five years or "
                          "so, or that we will have periods of widespread "
                          "unemployment or depression, or what?"),
                 "options": ["good", "uncertain", "bad"]},
                {"key": "dur",
                 "text": ("About the big things people buy for their homes -- "
                          "furniture, a refrigerator, a stove, a television "
                          "and things like that. Generally speaking, do you "
                          "think now is a good or bad time for people to buy "
                          "major household items?"),
                 "options": ["good", "uncertain", "bad"]},
            ],
            "aggregate": "umich_ics",
        },
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
                        "do not sum to 100 -- there is a 'not sure' residual of "
                        "roughly three to four points"),
        "survey": {
            "population": "A",
            "items": [{
                "key": "approval",
                "text": "Do you approve or disapprove of the way Donald Trump is handling his job as president?",
                "options": ["approve", "disapprove", "not sure"],
            }],
            "aggregate": "approve_share",
        },
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
        "survey": {
            "population": "RV",
            "items": [{
                "key": "approval",
                "text": "Do you approve or disapprove of the way Donald Trump is handling his job as president?",
                "options": ["approve", "disapprove", "not sure"],
            }],
            "aggregate": "approve_share",
        },
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
        "survey": {
            "population": "RV",
            "items": [{
                "key": "vote",
                "text": ("If the election for US House of Representatives in "
                         "your district were held today, would you vote for "
                         "the Democratic candidate or the Republican "
                         "candidate?"),
                "options": ["Democrat", "Republican", "other", "not sure"],
            }],
            "aggregate": "party_margin",
        },
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
        "survey": {
            "population": "A",
            "items": [{
                "key": "approval",
                "text": "Do you approve or disapprove of the way Donald Trump is handling the economy?",
                "options": ["approve", "disapprove", "not sure"],
            }],
            "aggregate": "approve_share",
        },
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
        "survey": {
            "population": "A",
            "items": [{
                "key": "approval",
                "text": "Do you approve or disapprove of the way Donald Trump is handling immigration?",
                "options": ["approve", "disapprove", "not sure"],
            }],
            "aggregate": "approve_share",
        },
    },
}

# --- subgroup tasks -------------------------------------------------------
#
# The same download already on disk carries three more axes, and until now the
# pipeline read one value from each and discarded the rest. They are declared
# compactly rather than as full literal rows because they are genuinely
# regular -- one pollster, one cut, one population -- and twelve hand-copied
# blocks would drift from each other. The declaration is still one line per
# series, which is the property that matters.
#
# `Adults` is deliberately absent: for YouGov it is byte-identical to
# `All polls` over all 117 waves, so registering it would score one series
# twice under two names and quietly double its weight in every average.
#
# Trade is thin (29 YouGov waves) and is registered anyway: it costs nothing,
# it widens the backtest, and a series with few observations is visibly few
# rather than silently absent.
_CELLS = [
    # id                       pollster           subgroup       pop  what it asks
    ("yougov_rv_approval",     "YouGov",          "Voters",      "RV",
     "percent of US registered voters who approve of Donald Trump's job "
     "performance, from the same waves as the adult-base tracker"),
    ("yougov_strong_approval", "YouGov",          "Strong",      "A",
     "percent of US adults who strongly approve of Donald Trump's job "
     "performance"),
    ("yougov_weak_approval",   "YouGov",          "Weak",        "A",
     "percent of US adults who somewhat approve of Donald Trump's job "
     "performance"),
    ("yougov_cost_approval",   "YouGov",          "Cost",        "A",
     "percent who approve of Donald Trump's handling of the cost of living"),
    ("yougov_trade_approval",  "YouGov",          "Trade",       "A",
     "percent who approve of Donald Trump's handling of trade and tariffs"),
    ("mc_econ_approval",       "Morning Consult", "Economy",     "RV",
     "percent of registered voters who approve of Donald Trump's handling of "
     "the economy"),
    ("mc_immig_approval",      "Morning Consult", "Immigration", "RV",
     "percent of registered voters who approve of Donald Trump's handling of "
     "immigration"),
    ("mc_trade_approval",      "Morning Consult", "Trade",       "RV",
     "percent of registered voters who approve of Donald Trump's handling of "
     "trade and tariffs"),
    ("mc_strong_approval",     "Morning Consult", "Strong",      "RV",
     "percent of registered voters who strongly approve of Donald Trump's job "
     "performance"),
    ("mc_weak_approval",       "Morning Consult", "Weak",        "RV",
     "percent of registered voters who somewhat approve of Donald Trump's job "
     "performance"),
]

_POP_NAME = {"A": "US adults", "RV": "US registered voters", "LV": "likely voters"}

for _sid, _pollster, _sub, _pop, _asks in _CELLS:
    SERIES[_sid] = {
        "label": f"{_pollster} Trump approval, {_sub.lower()}",
        "tracker": "economist_yougov" if _pollster == "YouGov" else "morning_consult",
        "source": "sb_approval",
        "filters": {"subgroup": _sub, "pollster": _pollster, "population": _pop},
        "value": "approve", "unit": "% approve",
        "cadence": "weekly",
        "question": f"{_pollster} weekly tracker: {_asks}",
        "methodology": (
            f"the same waves as the {_pollster} headline approval tracker, "
            f"reported for {_POP_NAME[_pop]}; 'strongly' and 'somewhat' "
            "approve sum to the headline approval number to within rounding "
            "(mean difference 0.1 points over 113 waves), so a forecast of "
            "all three should be consistent with itself"),
        "survey": {
            "population": _pop,
            "items": [{
                "key": "approval",
                "text": ("Do you approve or disapprove of the way Donald Trump "
                         "is handling his job as president?"),
                "options": ["strongly approve", "somewhat approve",
                            "somewhat disapprove", "strongly disapprove",
                            "not sure"],
            }],
            # Every cell here uses the four-point instrument, so the plain
            # two-option `approve_share` would find no matching answer and
            # score zero. Strong and Weak read one point of the scale each;
            # everything else reads both approve points together.
            "aggregate": ("strong_share" if _sub == "Strong" else
                          "weak_share" if _sub == "Weak" else
                          "approve_share_4pt"),
        },
    }

# A second pollster on the generic ballot. The existing generic series is
# YouGov only; Morning Consult publishes more of them (106 polls against 80),
# and two independent reads of the same quantity is a sharper test than one --
# a model that is right about the level but wrong about the house gap between
# them has not understood either.
SERIES["mc_generic_margin"] = {
    "label": "Morning Consult generic ballot margin",
    "tracker": "morning_consult",
    "source": "sb_generic",
    "filters": {"subgroup": "All polls", "pollster": "Morning Consult"},
    "value": "net", "unit": "net points, Democratic minus Republican",
    "cadence": "weekly",
    "question": ("Morning Consult generic congressional ballot: the Democratic "
                 "margin (Democratic percent minus Republican percent) for the "
                 "2026 US House elections"),
    "methodology": ("online panel of registered voters; positive means "
                    "Democrats lead. Published alongside, and usually a few "
                    "points from, the Economist/YouGov read of the same "
                    "quantity"),
    "survey": {
        "population": "RV",
        "items": [{
            "key": "vote",
            "text": ("If the election for US House of Representatives in your "
                     "district were held today, would you vote for the "
                     "Democratic candidate or the Republican candidate?"),
            "options": ["Democrat", "Republican", "other", "not sure"],
        }],
        "aggregate": "party_margin",
    },
}


def describe(series_id):
    """The question and methodology text an entrant is entitled to see."""
    s = SERIES[series_id]
    return {"question": s["question"], "unit": s["unit"],
            "methodology": s["methodology"], "cadence": s["cadence"]}


def survey(series_id):
    """The instrument a simulated respondent answers, or None.

    A series without one cannot run the persona condition: there is no honest
    way to ask a person for a number that is only ever computed from other
    people's answers. Callers check for None rather than inventing a question.
    """
    return SERIES[series_id].get("survey")


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
