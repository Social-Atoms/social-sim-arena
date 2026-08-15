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
from .adapters import civiqs as civiqs_adapter
from .adapters import fredcsv
from .adapters import silverbulletin as sb
from .adapters import umich as umich_adapter

# Set by michigan_history() to whichever source answered, plus the URL that
# answered and the body it returned. The body is what ssa/provenance.py
# archives: a page crediting a source for a value it does not carry is wrong in
# exactly the direction that matters here, and so is a vintage reconstructed
# from parsed rows rather than from the file.
MICHIGAN_SOURCE = "not yet fetched"
MICHIGAN_URL = umich_adapter.URL
MICHIGAN_RAW = ""


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
    global MICHIGAN_SOURCE, MICHIGAN_URL, MICHIGAN_RAW
    try:
        MICHIGAN_RAW = umich_adapter.fetch_text()
        rows = umich_adapter.parse(MICHIGAN_RAW)
        MICHIGAN_URL = umich_adapter.URL
        MICHIGAN_SOURCE = ("Surveys of Consumers, University of Michigan "
                           "(sca.isr.umich.edu), the survey's own monthly table")
        return rows
    except Exception as e:                         # noqa: BLE001 - reported
        print(f"  official Michigan table unavailable ({type(e).__name__}); "
              "using FRED, which lags one month")
        MICHIGAN_SOURCE = ("FRED (UMCSENT), which republishes the Michigan "
                           "index one month late; the survey's own table was "
                           "unreachable on this run")
        MICHIGAN_URL = fredcsv.URL
        MICHIGAN_RAW = fredcsv.fetch_text()
        return fredcsv.parse(MICHIGAN_RAW)

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

# --- independent trackers -------------------------------------------------
#
# The cells above are more *cuts*, not more *tasks*: eight of them come out of
# one YouGov wave and six out of one Morning Consult wave, so their errors share
# a sample, a weighting scheme and a house effect. Scored as if independent they
# would inflate the effective number of observations and make every average look
# better resolved than it is.
#
# Independence comes from a different fielding operation, not a different slice
# of the same one. These four are separate houses asking the same question of
# their own samples, with their own populations and their own biases -- which is
# exactly the comparison worth making: a model that has learned the *construct*
# should track all of them, while a model that has memorised one series should
# not. House effects are already first-class here (resolution runs through
# average.adjusted_average), so disagreement between them is signal, not noise.
#
# Rasmussen is the sharpest of the four and the most awkward: daily, likely
# voters, and the strongest house lean in the file. Registering it is a
# deliberate stress test of whether the arena's house-effect handling survives
# contact with a house that really does differ.
_HOUSES = [
    # id                    pollster              pop   cadence         note
    ("rasmussen_approval",  "Rasmussen Reports",  "LV", "daily",
     "daily tracking poll of likely voters; consistently the most "
     "Republican-leaning house in the field, by several points"),
    ("rmg_approval",        "RMG Research",       "RV", "weekly",
     "weekly online survey of registered voters"),
    ("ipsos_approval",      "Ipsos",              "A",  "roughly weekly",
     "probability-based online panel of US adults"),
    ("navigator_approval",  "Global Strategy Group/GBAO (Navigator Research)",
     "RV", "roughly every three weeks",
     "Democratic-aligned research collaborative; registered voters"),
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

for _sid, _pollster, _pop, _cadence, _note in _HOUSES:
    SERIES[_sid] = {
        "label": f"{_pollster.split('/')[0]} Trump approval",
        "tracker": _sid,
        "source": "sb_approval",
        "filters": {"subgroup": "All polls", "pollster": _pollster,
                    "population": _pop},
        "value": "approve", "unit": "% approve",
        "cadence": _cadence,
        "question": (f"{_pollster.split('/')[0]}: percent of "
                     f"{_POP_NAME[_pop]} who approve of Donald Trump's job "
                     f"performance"),
        "methodology": (
            f"{_note}. An independent fielding operation from the other "
            "trackers here: its own sample, weighting and house effect, so its "
            "level differs from theirs by more than sampling error and the "
            "difference is a property of the house, not an error to be "
            "averaged away"),
        "survey": {
            "population": _pop,
            "items": [{
                "key": "approval",
                "text": ("Do you approve or disapprove of the way Donald Trump "
                         "is handling his job as president?"),
                "options": ["approve", "disapprove", "not sure"],
            }],
            "aggregate": "approve_share",
        },
    }

# Two more independent houses on the generic ballot, for the same reason.
for _sid, _pollster, _pop in (("ipsos_generic_margin", "Ipsos", "RV"),):
    SERIES[_sid] = {
        "label": f"{_pollster} generic ballot margin",
        "tracker": _sid,
        "source": "sb_generic",
        "filters": {"subgroup": "All polls", "pollster": _pollster},
        "value": "net", "unit": "net points, Democratic minus Republican",
        "cadence": "roughly every two weeks",
        "question": (f"{_pollster} generic congressional ballot: the Democratic "
                     "margin (Democratic percent minus Republican percent) for "
                     "the 2026 US House elections"),
        "methodology": ("probability-based online panel; positive means "
                        "Democrats lead. An independent house from the "
                        "Economist/YouGov and Morning Consult reads of the "
                        "same quantity"),
        "survey": {
            "population": _pop,
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


# --- Civiqs -----------------------------------------------------------------
#
# The first *modeled* tracker in the registry, and it does not behave like the
# others. Everything above is a survey wave: a fresh sample, weighted once,
# published once, true forever. Civiqs runs an MRP model over a rolling panel
# and republishes its whole daily history every night, so the number printed
# against a past date is not the number that was printed against it at the time.
# Three registration decisions follow from that, and none of them are cosmetic.
#
# **Read from the archive, never from the live page.** `civiqs.as_displayed`
# builds each point from `civiqs/`, the dated snapshots the adapter writes on
# every fetch. Taken live, every point of a round's frozen history would have
# changed by resolution time, and `ssa.resolve` -- which answers a round with
# the first (date, value) pair the freeze did not contain -- would hand back a
# revised *old* point as this week's release.
#
# **Sampled on Fridays, not daily.** Both season rounds ask for a Friday
# dashboard value against a Wednesday lock. On a daily series the first
# observation after the freeze is Thursday's, so the resolver would answer a
# Friday question with Thursday's number -- a wrong resolution, which the
# resolver's own docstring rates worse than a missing one. Sampling at the
# cadence the question asks about also puts the persistence null a week back
# rather than a day back, which is the only honest null for a week-ahead
# question. A round asking about some other weekday needs its own series id.
#
# **Persistence is nearly unbeatable here and that is a true fact about the
# target, not something to correct.** Measured on the Friday net series:
# `scoring.noise_floor` returns m = 0.10 points against 0.78 on the YouGov
# topline, because a smoother's output carries no publication noise. Mean
# week-over-week change is 0.58 points. Entrants will find it hard to beat
# "last Friday's number", and the arena's claim is that its null is honest.
_CIVIQS_APPROVAL = "approve_president_trump_2025"

SERIES["civiqs_net_approval"] = {
    "label": "Civiqs Trump net approval",
    "tracker": "civiqs",
    "source": "civiqs",
    "civiqs": {"name": _CIVIQS_APPROVAL, "net": True, "weekday": 4},
    "value": "value",
    "unit": "net points (approve minus disapprove)",
    "cadence": ("daily model output, read and archived every day; the series "
                "scored here is the Friday reading"),
    "question": ("Civiqs daily tracker: Donald Trump's net job approval "
                 "(percent approve minus percent disapprove) among US "
                 "registered voters, as the dashboard shows it on Friday"),
    "methodology": (
        "Civiqs is a modeled tracker, not a survey wave: an MRP model over a "
        "rolling online panel of registered voters (roughly 123,000 cumulative "
        "interviews), publishing a smoothed daily estimate. Two things follow. "
        "The published history is revised nightly, so the arena scores against "
        "its own dated snapshot of what the dashboard displayed, not against "
        "whatever Civiqs says later. And the series barely wobbles -- mean "
        "day-to-day change is 0.07 points and mean week-to-week change 0.58 -- "
        "so almost all of a week's movement is real signal rather than sampling "
        "noise, and last Friday's number is a very strong guess. The dashboard "
        "runs about a day behind: the value shown on Friday is the model's "
        "estimate for Thursday. Approve and disapprove do not sum to 100; "
        "roughly five percent say neither."),
    "survey": {
        "population": "RV",
        "items": [{
            "key": "approval",
            # Civiqs's own `question_body`, verbatim, including the third
            # option. Its "neither" share is small (5.3) but it moves, and a
            # two-option instrument would push those respondents into approve
            # or disapprove and bias the net by several points.
            "text": ("Do you approve or disapprove of the way Donald Trump is "
                     "handling his job as president?"),
            "options": ["approve", "disapprove",
                        "neither approve nor disapprove"],
        }],
        "aggregate": "net_approve_share",
    },
}

# --- which Civiqs subgroups are worth a series ------------------------------
#
# A subgroup is a separate ~2 MB page fetch every day, forever, from a free
# site that rate-limits -- so unlike the Silver Bulletin cuts, which are free
# once the file is on disk, each one here has a standing cost and has to earn
# it. Six candidates were measured on their Friday net series over 81 weeks
# before any of them were registered (mad = mean absolute week-over-week
# change; corr = correlation of weekly changes with the national series):
#
#     cell             m      mad    range    corr(national)
#     national       0.102   0.578    18.6      1.000
#     party=Rep      0.000   0.490    20.2      0.752
#     party=Dem      0.019   0.092     3.3      0.810
#     party=Ind      0.367   1.359    33.7      0.968
#     age=18-34      0.104   0.604    26.3      0.978
#     age=65+        0.189   0.517     9.6      0.877
#
# Every cell clears its own noise floor easily -- m is near zero everywhere,
# because this is a smoother's output rather than a sample -- so "signal above
# noise" does not discriminate here the way it does on real waves. Two other
# tests do, and between them they reject four of the five:
#
# - **Democrats are floor-bound.** 1.5 percent approve, 3.3 points of total
#   range in nineteen months, 0.09 points of weekly movement. A perfect
#   forecast beats persistence by less than a tenth of a point. There is no
#   question there to ask.
# - **Independents and under-35s are the national series in a wig.** Their
#   weekly changes correlate with the national number at 0.968 and 0.978.
#   Independents swing the national figure, so registering them scores one
#   quantity twice and doubles its weight in every average -- the same reason
#   `Adults` is absent from the YouGov cells above. 65+ at 0.877 is a milder
#   case of it, and is a second cut of the same model besides.
#
# Republicans are the exception and the only cell registered: real amplitude
# (20.2 points of range, 0.49 a week) and the *lowest* correlation with the
# national series of the six, 0.752 -- it is the one cut that moves on its own
# rather than tracking the topline. That is the structural test worth paying a
# daily fetch for: a model that has learned the level but not the coalition
# gets the national number right and this one wrong.
#
# Note also what is *not* here: the other 23 Civiqs trackers (the economy,
# abortion, guns, four AI questions). They are one fetch each and genuinely
# independent constructs, so they are the better place to spend the next fetch
# budget -- but no round asks for them yet, and a series nobody forecasts is
# archive churn.
SERIES["civiqs_net_approval_rep"] = {
    "label": "Civiqs Trump net approval, Republicans",
    "tracker": "civiqs",
    "source": "civiqs",
    "civiqs": {"name": _CIVIQS_APPROVAL, "filters": {"party": "Republican"},
               "net": True, "weekday": 4},
    "value": "value",
    "unit": "net points (approve minus disapprove)",
    "cadence": "daily model output, read and archived every day; scored Fridays",
    "question": ("Civiqs daily tracker: Donald Trump's net job approval "
                 "(percent approve minus percent disapprove) among US "
                 "registered voters who identify as Republicans, as the "
                 "dashboard shows it on Friday"),
    "methodology": (
        "the same modeled tracker as the national Civiqs series, filtered to "
        "self-identified Republicans. It is the one demographic cut here that "
        "does not simply echo the national line: its weekly changes correlate "
        "with the national series at 0.75, against 0.97 for independents. It "
        "sits far higher in level (net +70 against -24 nationally) and it can "
        "fall while the national number holds, because the national number is "
        "moved mostly by independents."),
    # No `survey` instrument, deliberately. `personas.weights_for` reweights the
    # panel by population (adults, registered, likely voters) and has no way to
    # express "Republicans only", so a persona run on this series would put the
    # question to a national panel and report the answer as a party subgroup.
    # `series.survey()` returning None makes the persona arm refuse the series
    # by name, which is the honest outcome until the panel can be cut by party.
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
    # Civiqs is the one source with no single file to prefetch: every tracker
    # and every subgroup is its own ~2 MB page. So `src["civiqs"]` is not a
    # payload but a per-series override map -- `{series_id: [{date, value}]}` --
    # which is what tests inject to stay off the network. Anything not in it is
    # built by the adapter, which serves the day's archive when one exists and
    # fetches at most once per key per day when it does not. Registering more
    # Civiqs series therefore costs at most one request each per day, not one
    # per series per refresh.
    if "civiqs" in need and "civiqs" not in src:
        src["civiqs"] = {}

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
        elif spec["source"] == "civiqs":
            cfg = spec["civiqs"]
            given = src["civiqs"].get(sid)
            out[sid] = list(given) if given is not None else \
                civiqs_adapter.as_displayed(
                    cfg["name"], cfg.get("filters"),
                    choice=cfg.get("choice"), net=cfg.get("net", False),
                    weekday=cfg.get("weekday"))
        else:
            raise ValueError(f"{sid}: unknown source {spec['source']}")
        if not out[sid]:
            raise RuntimeError(
                f"{sid} built to zero points -- refusing to publish an empty "
                "series (check the filters against the upstream file)")
    return out
