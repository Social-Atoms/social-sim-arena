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
from . import crosstab
from .adapters import aaii as aaii_adapter
from .adapters import civiqs as civiqs_adapter
from .adapters import confboard as confboard_adapter
from .adapters import hhpoll as hhpoll_adapter
from .adapters import pentaesi as pentaesi_adapter
from .adapters import sce as sce_adapter
from .adapters import silverbulletin as sb
from .adapters import trends as trends_adapter
from .adapters import umich as umich_adapter
from .adapters import umichparty as umichparty_adapter
from .adapters import wikipedia as wikipedia_adapter
from .adapters import yougov_xtab as yougov_xtab_adapter

# Set by michigan_history() to whichever source answered, plus the URL that
# answered and the body it returned. The body is what ssa/provenance.py
# archives: a page crediting a source for a value it does not carry is wrong in
# exactly the direction that matters here, and so is a vintage reconstructed
# from parsed rows rather than from the file.
MICHIGAN_SOURCE = "not yet fetched"
MICHIGAN_URL = umich_adapter.URL
MICHIGAN_RAW = ""


def michigan_history():
    """Michigan sentiment, from the survey's own tables. No fallback.

    There used to be one, to FRED, "so the arena never goes dark". It went dark
    in a worse way instead. FRED carries UMCSENT a month behind at Michigan's
    request, so on the one run where the official table was briefly unreachable
    the fallback answered with a history ending a month early -- and nothing
    downstream could tell.

    What that cost, concretely. The 2026-08-12 lock snapshot for
    `umich-2026-08-prelim` froze a history ending in June instead of July, so
    the round's baselines were anchored on a level the series had already left;
    and because `resolve.candidate` takes the first release after the frozen
    history, "the next release" silently became July's final rather than
    August's preliminary. The round resolved against 55.2, a number published in
    July and public well before the lock, instead of 51.0. The arena's one
    claim -- that at lock time the answer does not exist -- failed on its first
    round, quietly.

    **A source that is silently a month behind is worse than no source.** So
    this raises, and the run is loudly broken, which is the same rule
    `build_trackers` already follows for an empty VoteHub response.

    Sets MICHIGAN_SOURCE, MICHIGAN_URL and MICHIGAN_RAW so the site can credit
    the file that actually answered and ssa/provenance.py can archive it.
    """
    global MICHIGAN_SOURCE, MICHIGAN_URL, MICHIGAN_RAW
    MICHIGAN_RAW = umich_adapter.fetch_text()
    finals = umich_adapter.parse(MICHIGAN_RAW)
    prelim = umich_adapter.parse_prelim(umich_adapter.fetch_prelim_text())
    rows = umich_adapter.merge(finals, prelim)
    if not rows:
        raise RuntimeError(
            "Michigan tables parsed to zero rows; refusing to publish an empty "
            "series (check whether the files moved)")
    MICHIGAN_URL = umich_adapter.URL
    newest = rows[-1]["date"]
    MICHIGAN_SOURCE = (
        "Surveys of Consumers, University of Michigan (sca.isr.umich.edu): "
        "tbmics.csv for finals and tbcics.csv for the current preliminary, "
        f"newest reading {newest}")
    return rows


# source: which adapter and which filters. value: the column to score.
SERIES = {
    "umich_sentiment": {
        "label": "Michigan consumer sentiment",
        "tracker": "umich_sentiment",
        "publisher": 'University of Michigan Surveys of Consumers: a monthly telephone and web survey of US households, published by the university as an official statistic with a preliminary and a final reading each month.',
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
        "publisher": "The Economist and YouGov, jointly: a weekly survey wave of YouGov's own online panel of US adults. The published number is what respondents said that week, not a model's estimate.",
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
        "publisher": 'Morning Consult: a continuously fielded online survey of US registered voters, run by the firm itself. The arena scores a house-effect-adjusted average of the published polls rather than a single wave.',
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
        "publisher": "The Economist and YouGov, jointly: a weekly survey wave of YouGov's own online panel of US adults. The published number is what respondents said that week, not a model's estimate.",
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
        "publisher": "The Economist and YouGov, jointly: a weekly survey wave of YouGov's own online panel of US adults. The published number is what respondents said that week, not a model's estimate.",
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
        "publisher": "The Economist and YouGov, jointly: a weekly survey wave of YouGov's own online panel of US adults. The published number is what respondents said that week, not a model's estimate.",
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
        "publisher": 'Morning Consult: a continuously fielded online survey of US registered voters, run by the firm itself. The arena scores a house-effect-adjusted average of the published polls rather than a single wave.',
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
        "publisher": 'Civiqs, an independent research firm running its own rolling online panel of registered voters (about 123,000 cumulative interviews). Unlike a survey wave, the published number is a modeled daily estimate (MRP), smoothed and revised nightly. Civiqs is unrelated to YouGov: same subject, different organisation, different instrument.',
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
        "publisher": 'Civiqs, an independent research firm running its own rolling online panel of registered voters (about 123,000 cumulative interviews). Unlike a survey wave, the published number is a modeled daily estimate (MRP), smoothed and revised nightly. Civiqs is unrelated to YouGov: same subject, different organisation, different instrument.',
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


# --- Civiqs sentiment ------------------------------------------------------
#
# Four economic-sentiment trackers and one emotion share, all registered voters,
# all read as the Friday value the same way approval is. They are here for two
# reasons beyond the topic.
#
# First, history. Three of them start 2015-01-16 and carry over six hundred
# Friday readings, against the 339 releases the whole LLM backtest currently
# spans. A baseline backtest gains far more from these than from another weekly
# approval slice.
#
# Second, they are *sentiment* rather than approval, and the arena had exactly
# one such series (Michigan, monthly). These are weekly and they move: measured
# on the Friday series, mean week-over-week change is 1.03, 1.14, 0.45 and 0.80
# points, over ranges of 118, 92, 62 and 30 points. That is the check that
# decided the set -- a series whose null barely moves cannot be scored, because
# the arena score's denominator is the persistence error and everything divides
# by it.
#
# `scoring.noise_floor` is the usual tool for that check and it is the wrong one
# here: it returns ~0 for all of these, because Civiqs publishes a smoothed
# model fit rather than a survey wave, so the published series carries no
# sampling noise to find. The gate used instead is the persistence error
# directly.
#
# Six of the ten emotions in `describe_feeling_us` failed it -- Proud,
# Overwhelmed, Satisfied, Ambivalent and Unsure move 0.03 to 0.10 points a week
# against ranges under 8 -- so only Angry is registered, and Hopeful, Depressed,
# Scared and Excited (0.17-0.23) are left out as borderline rather than
# published and quietly unscoreable.

_CIVIQS_METHOD = (
    "Civiqs is a modeled tracker, not a survey wave: an MRP model over a "
    "rolling online panel of registered voters, publishing a smoothed daily "
    "estimate. Two things follow. The published history is revised nightly, so "
    "the arena scores against its own dated snapshot of what the dashboard "
    "displayed, not against whatever Civiqs says later. And the smoothing means "
    "almost all of a week's movement is real signal rather than sampling noise, "
    "so last Friday's number is a strong guess. The dashboard runs about a day "
    "behind: the value shown on Friday is the model's estimate for Thursday. "
    "Shares do not sum to 100; every instrument carries an explicit unsure "
    "option, which is excluded from both sides of the net.")

_CIVIQS_CADENCE = ("daily model output, read and archived every day; the series "
                   "scored here is the Friday reading")


def _civiqs_net(sid, tracker, label, question, unit, net, method_extra):
    SERIES[sid] = {
        "label": label,
        "tracker": "civiqs",
        "publisher": 'Civiqs, an independent research firm running its own rolling online panel of registered voters (about 123,000 cumulative interviews). Unlike a survey wave, the published number is a modeled daily estimate (MRP), smoothed and revised nightly. Civiqs is unrelated to YouGov: same subject, different organisation, different instrument.',
        "source": "civiqs",
        "civiqs": {"name": tracker, "net": net, "weekday": 4},
        "value": "value",
        "unit": unit,
        "cadence": _CIVIQS_CADENCE,
        "question": question,
        "methodology": _CIVIQS_METHOD + " " + method_extra,
        # No `survey`, so `series.survey()` returns None and the persona arm
        # refuses these by name rather than guessing an instrument -- the same
        # discipline `civiqs_net_approval_rep` already follows.
        #
        # Nothing is lost by waiting: the exact wording and option list are in
        # every archived snapshot's `question_body` and `choices`. What is
        # missing is an aggregator. Entries in `personas.AGGREGATORS` are called
        # as `fn(answers, weights)` and hardcode their own option names, so a
        # net with two options on each side has none to name, and writing five
        # bespoke aggregators for an arm that is being redesigned would be work
        # thrown away. It belongs with the panel rebuild.
    }


_civiqs_net(
    "civiqs_net_econ_now", "economy_us_now",
    "Civiqs national economy, net good",
    ("Civiqs daily tracker: net rating of the condition of the national economy "
     "among US registered voters (very or fairly good, minus very or fairly "
     "bad), as the dashboard shows it on Friday"),
    "net points (good minus bad)",
    {"minuend": ["Very good", "Fairly good"],
     "subtrahend": ["Very bad", "Fairly bad"]},
    ("604 Friday readings from 2015-01-16, over a range of 118 points, mean "
     "week-over-week change 1.03. Both sides of the net carry two options, "
     "which is why the quantity is written out here rather than inferred."))

_civiqs_net(
    "civiqs_net_econ_direction", "economy_us_direction",
    "Civiqs national economy, net getting better",
    ("Civiqs daily tracker: net direction of the nation's economy among US "
     "registered voters (getting better minus getting worse), as the dashboard "
     "shows it on Friday"),
    "net points (better minus worse)",
    {"minuend": ["Getting better"], "subtrahend": ["Getting worse"]},
    ("604 Friday readings from 2015-01-16, over a range of 92 points, mean "
     "week-over-week change 1.14. 'Staying about the same' is offered and is "
     "on neither side of the net, so the two sides do not sum to 100."))

_civiqs_net(
    "civiqs_net_family_finances", "economy_family_retro",
    "Civiqs family finances over the last year, net better",
    ("Civiqs daily tracker: net change in the respondent's own family finances "
     "over the last year among US registered voters (gotten better minus "
     "gotten worse), as the dashboard shows it on Friday"),
    "net points (better minus worse)",
    {"minuend": ["Gotten better"], "subtrahend": ["Gotten worse"]},
    ("605 Friday readings from 2015-01-16, over a range of 62 points, mean "
     "week-over-week change 0.45 -- the least volatile of the four, and the "
     "one where beating persistence is hardest. It is retrospective and "
     "personal rather than prospective and national, which is what makes it "
     "worth carrying next to the other three."))

_civiqs_net(
    "civiqs_net_inflation_concern", "inflation_impact",
    "Civiqs inflation concern, net concerned",
    ("Civiqs daily tracker: net concern about the impact of inflation on "
     "consumer goods among US registered voters (very or somewhat concerned, "
     "minus a little or not at all concerned), as the dashboard shows it on "
     "Friday"),
    "net points (concerned minus not concerned)",
    {"minuend": ["Very concerned", "Somewhat concerned"],
     "subtrahend": ["Not concerned at all", "A little concerned"]},
    ("166 Friday readings from 2023-06-09, over a range of 30 points, mean "
     "week-over-week change 0.80. The shortest history of the four because the "
     "tracker itself is newer."))

SERIES["civiqs_angry_share"] = {
    "label": "Civiqs share angry about the country",
    "tracker": "civiqs",
        "publisher": 'Civiqs, an independent research firm running its own rolling online panel of registered voters (about 123,000 cumulative interviews). Unlike a survey wave, the published number is a modeled daily estimate (MRP), smoothed and revised nightly. Civiqs is unrelated to YouGov: same subject, different organisation, different instrument.',
    "source": "civiqs",
    # A share, not a net: the tracker declares no net and offers ten emotions,
    # so any net over it would be this repository's construction rather than
    # the source's published number.
    "civiqs": {"name": "describe_feeling_us", "choice": "Angry", "weekday": 4},
    "value": "value",
    "unit": "percent",
    "cadence": _CIVIQS_CADENCE,
    "question": ("Civiqs daily tracker: percent of US registered voters who "
                 "describe themselves as angry about the way things are going "
                 "in the United States, as the dashboard shows it on Friday"),
    "methodology": _CIVIQS_METHOD + (
        " Ten emotions are offered and the respondent picks one, so this is a "
        "share of a ten-way choice rather than of a binary. Angry is the only "
        "one of the ten registered: 166 Friday readings from 2023-06-09 over a "
        "range of 16 points, mean week-over-week change 0.31. Proud, "
        "Overwhelmed, Satisfied, Ambivalent and Unsure move 0.03 to 0.10 points "
        "a week over ranges under 8, which is not a forecasting question, and "
        "Hopeful, Depressed, Scared and Excited sit between at 0.17 to 0.23 and "
        "are left out rather than published as marginal."),
    # No `survey`, for the same reason as the four nets above: a ten-way choice
    # has no aggregator in `personas.AGGREGATORS`, and the wording is preserved
    # in the archived snapshots until the panel rebuild gives it one.
}


# --- Wikipedia pageviews -----------------------------------------------------
#
# The first *behavioral* target in the registry. Every series above measures
# stated opinion: someone asked a question and someone answered it. These count
# an action -- how many times human readers loaded an article -- published
# daily by the Wikimedia Pageviews API and summed by the adapter into
# Monday-Sunday weeks. A count is a census of what it measures: no sampling
# error, no house effect, no nightly re-modelling, and a day's number is final
# once the logs are aggregated and never revised. All forecast error on these
# series is therefore about the future, none of it about measurement.
#
# They also sit in the opposite regime from Civiqs. Measured on the 188
# complete weeks from 2023-01-08 to 2026-08-09: the Trump article's weekly
# total ranged 127 thousand to 5.43 million views with a mean absolute
# week-over-week change of 31.7%, and Taylor Swift's ranged 76 thousand to
# 1.71 million at 31.3%. Where the Civiqs registrations worry that persistence
# is nearly unbeatable, here last week's number misses by nearly a third of
# the level on an average week: attention is spiky, the spikes are
# event-driven, and a model that reads the calendar and the news cycle has
# real room to beat the null.
#
# Why these two articles, specifically:
#
# - **Donald_Trump** is the same subject as the approval trackers above,
#   measured as attention rather than opinion. The pairing is the point: an
#   indictment or a debate can multiply the week's pageviews severalfold while
#   moving approval by a point or less, so a model that has learned the news
#   cycle should forecast this series, and a model that has only learned the
#   level of opinion should not.
#
# - **Taylor_Swift** is the non-political control at a comparable scale of
#   fame, moved by album cycles and tours rather than by anything else this
#   registry tracks. Skill on both articles says a model understands pageview
#   dynamics; skill on Trump alone says it understands the political news
#   cycle; skill on neither localises the failure to the behavioral target
#   itself rather than to politics.
#
# Deliberately NO `survey` instrument on either row. A persona panel cannot be
# polled for a pageview count: there is no question a simulated respondent
# could answer whose honest aggregate is "how many times will everyone load
# this article next week" -- a respondent does not know their own future
# pageviews, let alone everyone else's. `series.survey()` returning None makes
# the persona arm refuse these by name, the same discipline as
# civiqs_net_approval_rep.

_WIKI_METHOD = (
    "Wikimedia Pageviews REST API (wikimedia.org/api/rest_v1), en.wikipedia "
    "only -- the English edition, not other language editions. Counts use the "
    "source's own agent=user split, which excludes traffic its classifier "
    "marks as spiders or automated: the question is about human attention, "
    "and a scraper re-crawling the wiki moves the raw count without a single "
    "person having cared. Daily counts across desktop, mobile web and the "
    "apps are summed into Monday-Sunday weeks and reported in thousands of "
    "views; a day's count is a census computed once from the request logs and "
    "never revised, so unlike a poll there is no sampling error and no house "
    "effect. ")

SERIES["wiki_views_trump"] = {
    "label": "Wikipedia weekly pageviews, Donald Trump",
    "tracker": "wikipedia",
        "publisher": 'The Wikimedia Foundation, from its own server logs: not a survey, but a count of how many people actually opened a page, published through a public API.',
    "source": "wikipedia",
    "wikipedia": {"article": "Donald_Trump"},
    "value": "value",
    "unit": "thousand pageviews (Mon-Sun week)",
    "cadence": "weekly, data final ~2 days after the week ends",
    "question": ("total en.wikipedia pageviews by human readers of the "
                 "article 'Donald Trump', in thousands, for the "
                 "Monday-to-Sunday week ending the Sunday the round names"),
    "methodology": _WIKI_METHOD + (
        "The series is attention, not opinion: it spikes severalfold on "
        "indictments, elections and inaugurations regardless of which way "
        "approval moves. Measured over the 188 complete weeks from "
        "2023-01-08: median 337 thousand views a week, range 127 thousand to "
        "5.43 million, mean absolute week-over-week change 31.7% -- last "
        "week's number is a genuinely beatable baseline here."),
}

SERIES["wiki_views_taylor_swift"] = {
    "label": "Wikipedia weekly pageviews, Taylor Swift",
    "tracker": "wikipedia",
        "publisher": 'The Wikimedia Foundation, from its own server logs: not a survey, but a count of how many people actually opened a page, published through a public API.',
    "source": "wikipedia",
    "wikipedia": {"article": "Taylor_Swift"},
    "value": "value",
    "unit": "thousand pageviews (Mon-Sun week)",
    "cadence": "weekly, data final ~2 days after the week ends",
    "question": ("total en.wikipedia pageviews by human readers of the "
                 "article 'Taylor Swift', in thousands, for the "
                 "Monday-to-Sunday week ending the Sunday the round names"),
    "methodology": _WIKI_METHOD + (
        "The non-political control next to the Trump pageview series: "
        "attention here is moved by album releases, tours and award shows "
        "rather than by the news cycle the rest of this registry lives in. "
        "Measured over the 188 complete weeks from 2023-01-08: median 203 "
        "thousand views a week, range 76 thousand to 1.71 million, mean "
        "absolute week-over-week change 31.3%."),
}


# --- Michigan consumer sentiment by political party --------------------------
#
# The same Index of Consumer Sentiment as `umich_sentiment`, cut three ways by
# how the respondent identifies: Democrat, Independent, Republican. Published on
# the *preliminary* release day in an addenda PDF and parsed by
# `ssa/adapters/umichparty.py`, which is the whole reason this exists --
# `docs/sources.md` section 6 recorded the cut as not viable, and Route A
# through `pdftotext` reopened it.
#
# **This is the registry's first target whose null is structurally wrong on a
# known calendar.** Everything else here moves by points a month; the partisan
# gap *inverts* at presidential transitions, and the date is set years ahead.
# October 2016 read Dem 102.1 / Rep 74.4; February 2017, Dem 77.5 / Rep 115.7.
# October 2024 read Dem 91.4 / Rep 53.6; two months later, Dem 69.6 / Rep 85.4 --
# and November 2024 caught it mid-swap at Dem 81.3 / Rep 69.1, the two lines
# crossing inside a single monthly reading. A persistence null cannot see either
# event coming. A society simulated from real people's partisanship should.
#
# What the three series carry, measured on the 115 unbroken monthly readings
# from 2017-02 (the archive also holds 41 sporadic rows back to 1980-06, a
# 46-year span, in fifteen stretches where the party question was asked at all):
#
# - **Levels are far apart and stay apart.** August 2026 is Dem 39.1 / Ind 48.5
#   / Rep 78.7 -- a 39.6-point Dem-Rep gap, wider than the entire range the
#   national index has moved in over the same decade. Forecasting three numbers
#   is not forecasting one number three times.
# - **Volatility is comparable across the three** (mean absolute month-over-
#   month change 4.4 / 3.8 / 4.6 index points), so no cut is a free win.
# - **Ranges over the modern run**: Dem 32.4-107.5, Ind 40.6-102.8, Rep
#   33.0-127.2. The out-party floor is the interesting region and both parties
#   have now visited it.
#
# The newest row is the *preliminary* reading and is revised at the final, which
# needs no handling here: `resolve.candidate` keys releases on (date, value), so
# a month's preliminary and its final are two releases and each settles its own
# round -- exactly as `umich_sentiment` already works.
_UMICH_PARTY_METHOD = (
    "Surveys of Consumers, University of Michigan; the same roughly 600-1,000 "
    "US adults a month, by telephone and web, that produce the headline index, "
    "split by the respondent's answer to 'Generally speaking, do you usually "
    "think of yourself as a Republican, a Democrat, an Independent, or what?'. "
    "The subgroup index is computed by the same published formula over the same "
    "five items and normalized to the same 1966 = 100 base, so it is directly "
    "comparable in level to the national number and to the other two parties. "
    "Subgroup samples are roughly a third of the national one, so month-to-"
    "month sampling noise is correspondingly larger. Published as an addenda "
    "table with the preliminary release; the newest month is a preliminary "
    "reading and is revised when the final lands at the end of the month. ")


def _umich_party(sid, party, label, who):
    SERIES[sid] = {
        "label": label,
        "tracker": "umich_party",
        "publisher": "University of Michigan Surveys of Consumers, party breakdown: the same monthly survey, reported separately for self-identified Democrats, Independents and Republicans in the release's own addendum table.",
        "source": "umichparty",
        "umichparty": {"party": party},
        "value": "value",
        "unit": "index points (1966=100)",
        "cadence": "monthly, published with the national preliminary release",
        "question": ("University of Michigan Index of Consumer Sentiment (ICS) "
                     f"among US adults who identify as {who}, from the Tables "
                     "Addenda of Political Party Variable published with the "
                     "monthly preliminary release"),
        "methodology": _UMICH_PARTY_METHOD + f"This is the {who} cut.",
        # No `survey` instrument, deliberately, and for exactly the reason
        # `civiqs_net_approval_rep` gives: `personas.weights_for` reweights the
        # panel by population (adults, registered, likely voters) and has no way
        # to express "Democrats only", so a persona run would put the five ICS
        # items to a national panel and report the answer as a party subgroup.
        # `series.survey()` returning None makes the persona arm refuse the
        # series by name, which is the honest outcome until the panel can be cut
        # by party -- and this tracker is the strongest argument yet for doing
        # that, since party is the axis the whole series is about.
    }


_umich_party("umich_party_dem", "dem",
             "Michigan consumer sentiment, Democrats", "Democrats")
_umich_party("umich_party_ind", "ind",
             "Michigan consumer sentiment, Independents", "Independents")
_umich_party("umich_party_rep", "rep",
             "Michigan consumer sentiment, Republicans", "Republicans")


# --- AAII investor sentiment -------------------------------------------------
#
# The first market-sentiment series in the registry, and the first weekly
# tracker that is neither political nor a Civiqs model. The headline is the
# bull-bear spread: percent of AAII members bullish on stocks over the next
# six months, minus percent bearish. Published weekly since 1987, which makes
# it one of the oldest sentiment series in existence -- though the machine-
# readable route only reaches the results page's ~22-week rolling window; the
# full history lives in an .xls this repository cannot read without a
# dependency (see ssa/adapters/aaii.py for that trade, stated in full).
#
# Two facts about the target that entrants will run into:
#
# - **The long-run mean spread is about +6.5 points** (members lean bullish on
#   average) **and the series is famously mean-reverting** -- extreme readings
#   are widely used as contrarian signals precisely because they decay. That
#   makes persistence a strong null here: at a one-week horizon the level
#   carries, mean reversion operates over months, and the week-over-week noise
#   punishes anyone who reaches for the long-run mean too eagerly.
# - The three shares are exhaustive (bullish + neutral + bearish = 100), so
#   the spread moves two-for-one with any bull<->bear flow but not at all with
#   flows into neutral. A forecast of the spread is implicitly a forecast of
#   which side the fence-sitters fall off.
SERIES["aaii_bull_bear_spread"] = {
    "label": "AAII bull-bear spread",
    "tracker": "aaii",
        "publisher": 'The American Association of Individual Investors: a weekly poll of its own members, who are self-selected active individual investors rather than a sample of the public.',
    "source": "aaii", "value": "spread",
    "unit": "percentage points (bullish minus bearish)",
    "cadence": ("weekly; voting runs Thursday through Wednesday, rows are "
                "dated by the closing Wednesday, results publish Thursday"),
    "question": ("AAII Investor Sentiment Survey: percent of AAII members "
                 "bullish about the stock market's direction over the next "
                 "six months, minus percent bearish (the bull-bear spread)"),
    "methodology": (
        "weekly online poll of American Association of Individual Investors "
        "members, running since 1987; one vote per member per weekly voting "
        "period (Thursday through Wednesday), results published Thursday. "
        "Bullish, neutral and bearish shares sum to 100. The long-run mean "
        "spread is roughly +6.5 points and the series is famously "
        "mean-reverting, which is why extreme readings are watched as "
        "contrarian signals. Respondents are self-selected active individual "
        "investors, not a probability sample of any general population."),
    # No `survey` instrument, deliberately. The persona panel is a
    # general-population demographic panel; AAII members are a self-selected
    # population of active individual investors (older, wealthier, far more
    # market-engaged than any demographic cell approximates). Putting this
    # question to the panel would answer "what do simulated US adults think",
    # score it against "what AAII members said", and call the gap model error.
    # `series.survey()` returning None makes the persona arm refuse the series
    # by name -- the same discipline as civiqs_net_approval_rep -- until a
    # panel with an AAII-member population definition exists.
}

# Penta-CivicScience Economic Sentiment Index: the second sentiment tracker
# with a sub-monthly cadence. Biweekly Wednesdays, free and keyless via the
# publisher's own WordPress feed, and self-checking (every release states its
# own delta and the parser reconciles it -- see the adapter, which is where
# every hard decision about this source is documented). Like AAII it is a
# self-selected online population, so no `survey` instrument here either, and
# for the same reason: the persona panel approximates US adults, not
# CivicScience respondents.
# Conference Board CCI: the market-research track's monthly anchor. The free
# page shows only the current release and the paid file holds the *revised*
# history; what rounds resolve against is the first print, recovered from the
# Internet Archive by tools/backfill_cci.py and grown one release at a time by
# the adapter's own write-once capture. Rows are dated by the month measured
# (Michigan-style label dates), so the lock snapshot, not the date filter, is
# what freezes this series for a round.
SERIES["cci_headline"] = {
    "label": "Conference Board Consumer Confidence Index",
    "tracker": "conference_board",
        "publisher": 'The Conference Board, a business membership and research organisation: a monthly survey of US households, published as an index. Each release restates the previous month; the arena scores the first print.',
    "source": "confboard",
    "unit": "index points (1985=100)",
    "cadence": "monthly; released the last Tuesday of the month, 10:00 ET",
    "question": ("Conference Board Consumer Confidence Index (1985=100), "
                 "first print of the monthly release"),
    "methodology": (
        "monthly online survey of US households conducted for the Conference "
        "Board (Toluna panel); the index is benchmarked to 1985=100. Each "
        "release restates the previous month, so the series here pins the "
        "first print of every release -- the number as the world first saw "
        "it -- which is what a forecast locked before the release can "
        "honestly be scored against."),
}

SERIES["esi_headline"] = {
    "label": "Penta-CivicScience Economic Sentiment Index",
    "tracker": "penta_esi",
        "publisher": "Penta and CivicScience: a biweekly index built from CivicScience's continuously running online polling, published by Penta as a press release.",
    "source": "pentaesi",
    "unit": "index points",
    "cadence": ("biweekly; released every other Wednesday, rows dated by "
                "the release day"),
    "question": ("Penta-CivicScience Economic Sentiment Index: the headline "
                 "ESI reading published in the biweekly release"),
    "methodology": (
        "CivicScience online panel, published every other Wednesday by Penta "
        "since 2013 (HPS-CivicScience before 2023). Five sub-indicators "
        "averaged into a headline index; the arena tracks the headline only. "
        "The series here starts where the release wording became "
        "machine-stable (2022); respondents are a self-selected online panel, "
        "not a probability sample."),
}


_SCE_METHODOLOGY = (
    "Survey of Consumer Expectations, Federal Reserve Bank of New York; "
    "rotating internet panel of about 1,300 US household heads. Each "
    "respondent assigns probabilities to inflation outcome bins; the "
    "published number is the median across respondents of the density "
    "means, in percent. Rows are dated by the reference month; the release "
    "lands early the following month, on a date preannounced on the NY Fed "
    "economic-indicators calendar. The workbook carries its own license "
    "sheet granting a worldwide, royalty-free right to reproduce and "
    "distribute the published data.")

SERIES["sce_inflation_1y"] = {
    "label": "NY Fed SCE 1-year inflation expectations",
    "tracker": "ny_fed_sce",
    "publisher": ("The Federal Reserve Bank of New York: a monthly internet "
                  "survey of a rotating panel of about 1,300 US household "
                  "heads, published by the bank as an official statistic "
                  "with release dates announced months ahead."),
    "source": "sce",
    "sce": {"horizon": "1y"},
    "unit": "percent, median expected inflation",
    "cadence": ("monthly; released in the first ten days of the following "
                "month, dates preannounced on the NY Fed CMD calendar"),
    "question": ("NY Fed Survey of Consumer Expectations: median one-year-"
                 "ahead expected inflation rate"),
    "methodology": _SCE_METHODOLOGY,
}

SERIES["sce_inflation_3y"] = {
    "label": "NY Fed SCE 3-year inflation expectations",
    "tracker": "ny_fed_sce",
    "publisher": SERIES["sce_inflation_1y"]["publisher"],
    "source": "sce",
    "sce": {"horizon": "3y"},
    "unit": "percent, median expected inflation",
    "cadence": SERIES["sce_inflation_1y"]["cadence"],
    "question": ("NY Fed Survey of Consumer Expectations: median three-year-"
                 "ahead expected inflation rate"),
    "methodology": _SCE_METHODOLOGY,
}

SERIES["hh_trump_approval"] = {
    "label": "Harvard-Harris Trump approval",
    "tracker": "harvard_harris",
    "publisher": ("Harvard CAPS and The Harris Poll, jointly: a roughly "
                  "monthly online survey of US registered voters, published "
                  "as PDF toplines with no preannounced calendar; some "
                  "months are skipped."),
    "source": "hhpoll",
    "unit": "% approve",
    "cadence": ("roughly monthly, no preannounced schedule; rows dated by "
                "the day the topline was published, never the fielding day"),
    "question": ("Harvard CAPS/Harris Poll: percent who approve (strongly "
                 "or somewhat) of the job Donald J. Trump is doing as "
                 "President, among surveyed US registered voters"),
    "methodology": (
        "Harvard CAPS/Harris Poll online survey, roughly 1,700-2,750 US "
        "registered voters per wave, weighted to the US general adult "
        "population; approve is the published strongly/somewhat net from "
        "the topline PDF (question code M3ALT). Rows are dated by the "
        "production stamp -- the day the number became public -- so a "
        "late-published wave can never slide into history a forecaster "
        "already locked against. The series reads only the committed "
        "vintages under sources/hhpoll/; a new wave enters when a "
        "maintainer fetches its PDF."),
}

# --- Google Trends: the market-research track -------------------------------
#
# The first two *behavioral* series in the registry: nobody was asked anything.
# The value is an index over what people typed into a search box, which is a
# different kind of quantity from every survey and model tracker above, and
# the reason the arena wants it: a simulated society that can only reproduce
# poll toplines has learned polls, not people. Search interest moves on product
# news, recalls, launches and price cuts -- events with public lead-ups an
# entrant can reason about -- while its measurement quirks (window
# renormalization, sampling jitter) are the arena's problem, solved by the
# archive, not the entrant's.
#
# Both series read `trends.as_archived`, so every registration decision that
# matters is documented once, on the adapter: the fixed 12-month window, the
# one-keyword-per-request rule, why the dated snapshot in `trends/` is the
# resolution truth, and why a completed week's value is frozen by the earliest
# snapshot that holds it. What belongs here is only what differs per series:
# the query string.
#
# Neither entry carries a `survey` block, deliberately and permanently -- not,
# as with the Civiqs cells, pending an aggregator. A persona can be asked how
# it feels about Tesla; it cannot be asked "how many times did people like you
# Google 'Tesla' this week, as a share of all searches, scaled to the busiest
# week of the year". The quantity only exists as an aggregate over behavior,
# so `series.survey()` returns None and the persona arm refuses these by name.

_TRENDS_UNIT = "search interest index (0-100, 12-month window)"

_TRENDS_CADENCE = ("weekly, Sunday through Saturday; the completed week "
                   "appears in the following days' snapshots, which are read "
                   "and archived daily")

_TRENDS_METHOD = (
    "Google Trends is a behavioral index, not a survey: no one was asked "
    "anything. Google counts searches containing the query, divides by total "
    "search volume, and scales the result so the busiest week of the "
    "requested window reads 100 -- the arena always requests the trailing 12 "
    "months, so the scale is relative to the past year's peak and can shift "
    "when a new peak enters the window or an old one leaves it. The index is "
    "computed from a sample of searches, so the same completed week can read "
    "a point or two differently on different days. The arena therefore "
    "archives a dated snapshot of every fetch and scores against its own "
    "archive: a completed week's value is whatever the earliest snapshot "
    "containing that week showed, and later re-reads do not move it. The "
    "in-progress week is never scored.")


def _trends(sid, query, asks):
    SERIES[sid] = {
        "label": f"Google Trends search interest: {query}",
        "tracker": "google_trends",
        "publisher": 'Google, from its own search logs: not a survey and not an opinion, but a measure of what people actually typed into a search box, published as a relative index.',
        "source": "trends",
        "trends": {"query": query, "geo": trends_adapter.GEO},
        "value": "value",
        "unit": _TRENDS_UNIT,
        "cadence": _TRENDS_CADENCE,
        "question": (
            f"Google Trends weekly search interest for the query "
            f"'{query}' in the United States (web search, all categories): "
            f"the 0-100 index for the most recent complete Sunday-to-Saturday "
            f"week, normalized within the trailing 12-month window, as "
            f"captured by the arena's archived snapshot. {asks}"),
        "methodology": _TRENDS_METHOD,
        # No `survey`: see the block comment above. This is a permanent
        # property of a behavioral target, not a missing feature.
    }


_trends("trends_tesla", "Tesla",
        "Interest tracks product and company news -- launches, recalls, "
        "earnings, Musk coverage -- so the week's public events are the "
        "signal to reason over.")
_trends("trends_iphone", "iPhone",
        "Interest is strongly seasonal around Apple's September announcement "
        "cycle and product rumors, so the calendar itself is informative.")


# --- the five-brand basket, as shares ---------------------------------------
#
# One Trends request measures up to five queries on a single shared scale, and
# the basket rounds forecast how that scale is divided: each brand's percent of
# the five-brand total for the week.
#
# **Why a share rather than the index itself.** The index is a sample, and the
# sample is redrawn on every fetch: measured over four fetch days, one settled
# week's whole series moved together by about four percent -- Tesla, iPhone,
# Samsung, Netflix and Disney all up on one day and all down on the next.
# That common factor is the sampling draw, not the world. Dividing by the
# basket total cancels it: across the same four fetches the raw values wobbled
# 2.0-3.2% while the shares wobbled 1.2-1.9%. What survives is what we want
# scored -- a launch week really does move iPhone's share of attention, and a
# share cannot be moved by a peak entering or leaving the 12-month window,
# which rescales every raw index in the archive.
#
# **Why not the ranking.** These five brands sit far apart (iPhone near 55,
# Tesla near 13), so their order barely changes: over 52 archived weeks,
# copying last week's order was exactly right 49% of the time and the leader
# never changed once. A round whose null is perfect half the time cannot
# separate anyone. Shares move every week and keep the magnitudes the ordering
# throws away.
BASKET = ("Tesla", "iPhone", "Samsung", "Netflix", "Disney")

_BASKET_METHOD = (
    "Google Trends, United States, web search, all categories, one comparison "
    "request covering all five queries so their weekly indices share a single "
    "scale. The arena archives a dated snapshot of every fetch and scores "
    "against its own archive; a completed week's values are whatever the "
    "earliest snapshot containing that week showed, and the share is that "
    "week's index for this query divided by the sum over the five, in percent. "
    "The five shares add to 100 by construction. The in-progress week is "
    "never scored.")


def _trends_share(sid, query, asks):
    SERIES[sid] = {
        "label": f"Trends share of the five-brand basket: {query}",
        "tracker": "google_trends",
        "publisher": 'Google, from its own search logs: not a survey and not an opinion, but a measure of what people actually typed into a search box, published as a relative index.',
        "source": "trends_basket",
        "trends_basket": {"query": query, "basket": BASKET,
                          "geo": trends_adapter.GEO},
        "unit": "percent of the five-brand basket",
        "cadence": ("weekly, Sunday through Saturday; the completed week "
                    "enters the archive the following week"),
        "question": (
            f"Google Trends, United States: '{query}' as a percentage of the "
            f"combined weekly search interest of {', '.join(BASKET)}, for the "
            f"most recent complete Sunday-to-Saturday week. {asks}"),
        "methodology": _BASKET_METHOD,
        # No `survey`, as for every behavioral target.
    }


_trends_share("trends_share_tesla", "Tesla",
              "Share moves on product and company news -- launches, recalls, "
              "earnings, Musk coverage.")
_trends_share("trends_share_iphone", "iPhone",
              "Share is strongly seasonal around Apple's September "
              "announcement cycle, which is the largest regular swing in "
              "this basket.")
_trends_share("trends_share_samsung", "Samsung",
              "Share moves on launches and on Apple's calendar, since the "
              "basket is a fixed pie.")
_trends_share("trends_share_netflix", "Netflix",
              "Share moves on release schedules and subscription news.")
_trends_share("trends_share_disney", "Disney",
              "Share moves on film releases, park and streaming news.")


# --- the Civiqs 16-cell population profile ----------------------------------
#
# Fifteen more cuts of the same modeled approval tracker, completing -- with
# civiqs_net_approval_rep above -- one cell per bucket of every demographic
# axis the dashboard exposes: party (3), age (4), race (4), education (3),
# gender (2). Sixteen numbers that together describe *which people* moved.
#
# These are the data layer of the joint population-profile task, and that is
# the only capacity in which most of them earn a place. As standalone scalar
# rounds the measured objections stand (see the block above
# civiqs_net_approval_rep): Democrats are floor-bound, independents and the
# young echo the national line at 0.97+. But a profile scored jointly with the
# energy score is exactly where a flat cell still carries information -- a
# model that believes Democrats might move books real loss against one that
# knows they will not -- so every cell is collected daily whether or not it
# also runs as its own round. Labels are byte-exact from the dashboard's
# own demographics list (fetched 2026-08-18); a typo'd label is a hard error
# in the adapter, never a silently-national series.
#
# Which cells *do* also stand alone as weekly scalar rounds is
# `_STANDALONE_CELLS` below. Republicans and independents came first; four
# more were added after a movement check on the archive as of 2026-08-26
# (mean absolute week-over-week change of the Friday net over the trailing
# 26 weeks, gate ~0.5 pts/wk, capped at the strongest movers):
#
#     kept:     men 0.92, adults 35-49 0.84, Hispanic/Latino 0.80,
#               adults 50-64 0.77 -- the last also has the lowest correlation
#               of weekly changes with the national series of any candidate
#               (0.75, Republicans' company as a cut that moves on its own)
#     cleared the gate, not kept: non-college 0.77 and White 0.76 echo the
#               national line at 0.94+; 18-34 0.71, postgrad 0.65, other-race
#               0.65, college 0.63, 65+ 0.59 and women 0.50 move less than
#               every kept cell
#     rejected: Black 0.29 and Democrats 0.09, floor-bound
_PROFILE_CELLS = [
    # id suffix          axis          label                        short
    ("dem",              "party",      "Democrat",                  "Democrats"),
    ("ind",              "party",      "Independent",               "independents"),
    ("age_18_34",        "age",        "18-34",                     "adults 18-34"),
    ("age_35_49",        "age",        "35-49",                     "adults 35-49"),
    ("age_50_64",        "age",        "50-64",                     "adults 50-64"),
    ("age_65_up",        "age",        "65+",                       "adults 65 and older"),
    ("race_white",       "race",       "White",                     "White registered voters"),
    ("race_black",       "race",       "Black or African-American", "Black registered voters"),
    ("race_hispanic",    "race",       "Hispanic/Latino",           "Hispanic/Latino registered voters"),
    ("race_other",       "race",       "Other",                     "registered voters of other races"),
    ("edu_noncollege",   "education",  "Non-College Graduate",      "non-college graduates"),
    ("edu_college",      "education",  "College Graduate",          "college graduates"),
    ("edu_postgrad",     "education",  "Postgraduate",              "postgraduates"),
    ("male",             "gender",     "Male",                      "men"),
    ("female",           "gender",     "Female",                    "women"),
]

# Cells that also run as their own single-number weekly rounds in
# `questions/season0.json` ("rep" is registered above the loop, listed here so
# the roster is complete in one place). Adding a cell here without adding its
# rounds -- or the reverse -- makes the methodology text lie to entrants about
# the question in front of them; the two change together.
_STANDALONE_CELLS = ("rep", "ind", "male", "age_35_49", "age_50_64",
                     "race_hispanic")

for _sfx, _axis, _label, _short in _PROFILE_CELLS:
    SERIES[f"civiqs_net_approval_{_sfx}"] = {
        "label": f"Civiqs Trump net approval, {_short}",
        "tracker": "civiqs",
        "publisher": 'Civiqs, an independent research firm running its own rolling online panel of registered voters (about 123,000 cumulative interviews). Unlike a survey wave, the published number is a modeled daily estimate (MRP), smoothed and revised nightly. Civiqs is unrelated to YouGov: same subject, different organisation, different instrument.',
        "source": "civiqs",
        "civiqs": {"name": _CIVIQS_APPROVAL, "filters": {_axis: _label},
                   "net": True, "weekday": 4},
        "value": "value",
        "unit": "net points (approve minus disapprove)",
        "cadence": _CIVIQS_CADENCE,
        "question": ("Civiqs daily tracker: Donald Trump's net job approval "
                     "(percent approve minus percent disapprove) among US "
                     f"registered voters, {_short} only, as the dashboard "
                     "shows it on Friday"),
        # Some cells (`_STANDALONE_CELLS`) also carry their own single-number
        # rounds, so the sentence about how a cell is scored is
        # written per cell rather than once for all sixteen. Saying "scored
        # jointly, not as its own round" on a cell that does have its own round
        # would tell an entrant something false about the question in front of
        # it, in the one field it is entitled to trust.
        "methodology": _CIVIQS_METHOD + (
            f" Filtered to the dashboard's {_axis} = {_label} subgroup. "
            "One cell of the sixteen-cell population profile, scored jointly "
            "with the other cells"
            + (" and also asked as its own single-number round."
               if _sfx in _STANDALONE_CELLS else ", not as its own round.")),
        # No `survey` instrument: personas.weights_for cannot express a
        # subgroup-only population -- same refusal as civiqs_net_approval_rep.
    }


# The profile in its scored order: party, age, race, education, gender, each
# axis in the dashboard's own order. Republicans are spliced back into the
# party block they belong to -- they are registered above, separately, because
# that cell also stands alone as a scalar round and the other fifteen do not.
#
# One published constant rather than a list per consumer. A profile round names
# its cells in `questions/season0.json`, the harness asks for exactly these keys
# and the scorer reads the outcome vector in exactly this order, so a roster
# that disagreed anywhere would silently score cell i against cell j's answer --
# an error no test of any single module could see. Everything checks against
# this tuple instead.
PROFILE_CELLS = tuple(
    ["civiqs_net_approval_dem", "civiqs_net_approval_ind",
     "civiqs_net_approval_rep"]
    + [f"civiqs_net_approval_{sfx}" for sfx, _a, _l, _s in _PROFILE_CELLS
       if sfx not in ("dem", "ind")])

# Fail at import, not at scoring time: a typo'd or dropped cell here would
# otherwise surface as a sixteen-cell round quietly resolving on fifteen.
assert len(PROFILE_CELLS) == 16, f"profile has {len(PROFILE_CELLS)} cells, not 16"
assert len(set(PROFILE_CELLS)) == 16, "a profile cell is registered twice"
for _cell in PROFILE_CELLS:
    assert _cell in SERIES, f"profile cell {_cell} is not a registered series"


# --- the Economist/YouGov crosstab: a second, independent population ---------
#
# Sixteen more subgroup cells of Trump approval, and the reason to carry a
# second set is that the first one is a *model*. Civiqs publishes an MRP
# estimate: its sixteen cells are what a statistical model says each subgroup
# thinks, smoothed, revised nightly, and correlated across cells by
# construction because one model produced all of them. The Economist/YouGov
# tracker publishes the survey's own crosstab: the cell labelled "Postgrad" is
# the answers of the roughly 190 postgraduates who were actually interviewed
# that week, and nothing links it to the "Hispanic" cell except the world.
#
# So the two profiles disagree about what a subgroup round even measures, and
# an entrant that scores well on both has done something a smoother cannot.
# Registering them keeps them separable: different `source`, different `unit`
# (percent approving against net points), different cadence, different rounds.
#
# **Why these series are monthly.** `ssa/crosstab.py`'s module docstring holds
# the measurement and the argument; the short version is that a weekly round on
# most of these cells is a round on a coin flip. Measured over the 82 waves
# published 2025-01-28 to 2026-08-17, nine of the sixteen cells have *no* real
# week-to-week movement at all -- the estimator puts every point of their
# weekly wobble in the measurement-noise term -- and the seven that do move
# move less than their own noise. Averaging the four waves dated in a calendar
# month cuts that noise by a median of 62 percent (Democrat 1.07 -> 0.43
# points, Republican 1.95 -> 0.58, College grad 2.56 -> 0.96; least improved is
# independents at 18 percent, most is Black voters, where it vanishes) and
# leaves fourteen of the sixteen with movement the arena can score. That is why
# the registered series is monthly and not weekly: it is the coarsest thing the
# data supports, not a scheduling preference.
#
# The monthly figures rest on eighteen complete months, which is thin: treat
# any single cell's number as indicative and the direction as settled. Both
# sets are quoted per cell in the rows below, because a reader given only the
# spread between entrants would otherwise read a noise floor as skill.
#
# **What a month's point is, and what it is dated.** The mean of the four
# weekly waves dated in that calendar month, dated by the last of those four.
# Six of the twenty months on this tracker carry five waves; those keep the
# last four, so the target's own noise floor is the same estimator every month
# and two rounds' scores stay comparable. A month with fewer than four waves
# publishes no point at all rather than a three-wave average wearing the same
# name -- `crosstab.month_target` raises, and `crosstab.monthly_coverage` says
# which months were dropped and why.
#
# **The consequence for a round's lock.** A month's point is dated by its last
# wave, and `profile_round.frozen_history` freezes on `date < lock_at[:10]`. So
# a round scoring month M must lock on or before the date of M's last wave: one
# day later and the strict comparison lets the answer into the history its own
# persistence null is built from. With the arena's release-minus-48h rule that
# fixes the release at the last wave's date plus exactly two days, which is
# where `questions/season0.json` puts it.
#
# Labels are byte-exact from `yougov_xtab.SCORED_CELLS`, and the assertion at
# the end of this block is what enforces it: the adapter's roster is the
# authority on what the workbook contains, and a series registered under a
# label the workbook does not carry is a round that can never resolve.

_YOUGOV_XTAB_MEASURE = "approve"

_YOUGOV_XTAB_PUBLISHER = (
    "The Economist and YouGov, published weekly as the tracker's own crosstab "
    "workbook: each cell is the percentage of the real respondents in that "
    "subgroup of that week's survey who said they approve -- an actual "
    "measured cell of the survey, not a modelled estimate for a subgroup. The "
    "arena scores the mean of the four weekly waves dated in a calendar month.")

# suffix, axis, the workbook's own label, how to say it in a sentence, median
# weighted base over 82 waves, measured weekly noise, measured noise of the
# four-wave monthly average -- both in points, both from the cell's own
# history via scoring.noise_floor, measured 2026-08-23 over waves
# 2025-01-28..2026-08-17.
_YOUGOV_XTAB_CELLS = [
    ("dem",             "party",     "Democrat",     "Democrats",              394, 1.07, 0.43),
    ("ind",             "party",     "Independent",  "independents",           384, 2.06, 1.69),
    ("rep",             "party",     "Republican",   "Republicans",            428, 1.95, 0.58),
    ("age_under_30",    "age",       "Under 30",     "voters under 30",        186, 2.85, 1.23),
    ("age_30_44",       "age",       "30-44",        "voters aged 30 to 44",   273, 2.26, 1.51),
    ("age_45_64",       "age",       "45-64",        "voters aged 45 to 64",   414, 2.16, 0.68),
    ("age_65_up",       "age",       "65+",          "voters aged 65 and over", 317, 2.09, 0.73),
    ("race_white",      "race",      "White",        "White voters",           842, 1.22, 0.75),
    ("race_black",      "race",      "Black",        "Black voters",           141, 1.99, 0.00),
    ("race_hispanic",   "race",      "Hispanic",     "Hispanic voters",        138, 3.31, 0.59),
    ("male",            "gender",    "Male",         "men",                    558, 1.42, 0.78),
    ("female",          "gender",    "Female",       "women",                  629, 1.21, 0.56),
    ("edu_hs_or_less",  "education", "HS or less",   "voters with a high-school education or less", 332, 2.52, 0.85),
    ("edu_some_college", "education", "Some college", "voters with some college",  356, 2.40, 0.90),
    ("edu_college_grad", "education", "College grad", "college graduates",        316, 2.56, 0.96),
    ("edu_postgrad",    "education", "Postgrad",     "postgraduates",          190, 3.49, 1.29),
]

_YOUGOV_XTAB_CADENCE = (
    "monthly, and derived rather than published as such: YouGov fields this "
    "tracker weekly and the workbook carries one column per wave (dated a "
    "Monday in 67 of 82 waves, a Tuesday in 13, a Sunday in 2). The arena's "
    "point for a calendar month is the mean of the four waves dated in it, "
    "dated by the last of those four, and it exists only once that fourth "
    "wave is in the workbook -- within a week of the wave's own date.")

_YOUGOV_XTAB_METHOD = (
    "Economist/YouGov weekly tracker of US registered voters, taken from "
    "YouGov's own public tracker workbook, one sheet per subgroup. This is the "
    "survey's crosstab, so a cell is the answer of the respondents actually "
    "interviewed in that subgroup that week and the cells are linked by "
    "nothing but the electorate -- unlike a modelled tracker, where one model "
    "produces every cell. YouGov publishes each cell rounded to a whole "
    "percentage point; approve, disapprove and not sure sum to 100. "
    "The arena scores the mean of the four weekly waves dated in the calendar "
    "month (the last four, in a month that carries five), which is the unit "
    "the noise in this file makes scoreable: at wave level, nine of the "
    "sixteen cells show no real week-to-week movement whatsoever, and the "
    "four-wave average cuts a cell's measurement noise by a median of 62 "
    "percent. Both figures are estimated from the series' own first "
    "differences, and the monthly one rests on only eighteen complete months, "
    "so a monthly figure of 0.00 does not mean a cell has no sampling error -- "
    "it means that over eighteen months the estimator could not separate any "
    "of its movement from signal. Read the small ones as a lower bound.")


def _yougov_xtab(sfx, axis, label, short, base, wave_noise, month_noise):
    """Register one crosstab cell.

    Every cell carries its own measured base size and noise, because those are
    the two numbers that decide whether its forecast can be better than a coin
    flip, and they differ across these cells by a factor of six. Withholding
    them would grade an entrant on a question it was not shown -- the registry
    docstring's rule, applied to the one field where these cells differ most.
    """
    sid = f"yougov_xtab_approve_{sfx}"
    SERIES[sid] = {
        "label": f"Economist/YouGov Trump approval, {short}",
        "tracker": "yougov_xtab",
        "publisher": _YOUGOV_XTAB_PUBLISHER,
        "source": "yougov_xtab",
        "yougov_xtab": {"cell": label, "axis": axis,
                        "measure": _YOUGOV_XTAB_MEASURE},
        "unit": "percent approving",
        "cadence": _YOUGOV_XTAB_CADENCE,
        "question": (
            "Economist/YouGov weekly tracker: Donald Trump's job approval "
            f"among US registered voters, {short} only -- the percentage "
            "saying they approve of the way he is handling his job as "
            "President, averaged over the four weekly waves dated in the "
            "calendar month"),
        "methodology": _YOUGOV_XTAB_METHOD + (
            f" This cell is the workbook's {axis} = '{label}' sheet. Its "
            f"median weighted base is {base:,} respondents per wave; measured "
            f"over the published history its weekly measurement noise is "
            f"{wave_noise:.2f} points and the four-wave monthly average's is "
            f"{month_noise:.2f}. One cell of the sixteen-cell Economist/YouGov "
            "population profile, scored jointly with the other cells, not as "
            "its own round."),
        # No `survey` instrument, for the same reason every Civiqs subgroup
        # cell omits one: `personas.weights_for` reweights the panel by
        # population and cannot express "postgraduates only", so a persona run
        # would put the question to a national panel and file the answer as a
        # subgroup. `series.survey()` returning None is the refusal, by name.
    }
    return sid


# The profile in its scored order -- party, age, race, gender, education, each
# axis in the workbook's own order. One published constant, for the reason
# PROFILE_CELLS gives: the round definition names these ids, the harness asks
# for exactly these keys and the scorer reads the outcome vector in this order,
# so a roster that disagreed anywhere would score cell i against cell j's
# answer.
YOUGOV_XTAB_CELLS = tuple(_yougov_xtab(*row) for row in _YOUGOV_XTAB_CELLS)

# Fail at import, not at scoring time. The adapter's roster is the authority on
# what the workbook actually contains; a cell registered here under a label the
# workbook does not carry is a round that can never resolve, and one missing
# from here is a sixteen-cell round quietly scored on fifteen.
assert [SERIES[c]["yougov_xtab"]["cell"] for c in YOUGOV_XTAB_CELLS] == \
    list(yougov_xtab_adapter.SCORED_CELLS), \
    "the registered crosstab cells no longer match yougov_xtab.SCORED_CELLS"
assert len(set(YOUGOV_XTAB_CELLS)) == 16, "a crosstab cell is registered twice"


def describe(series_id):
    """The question and methodology text an entrant is entitled to see."""
    s = SERIES[series_id]
    return {"question": s["question"], "unit": s["unit"],
            "methodology": s["methodology"], "cadence": s["cadence"],
            "publisher": s.get("publisher", s.get("tracker", ""))}


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
    # One PDF conversion shared by all three party series. `umichparty.load()`
    # reads the newest vintage under `sources/umichparty/` and never the
    # network -- the addenda has been published exactly once, so nothing here
    # can be made to depend on it appearing again on schedule. Tests inject
    # parsed rows here and stay off both the disk and `pdftotext`.
    if "umichparty" in need and "umichparty" not in src:
        src["umichparty"] = umichparty_adapter.load()
    # `src["aaii"]` holds *parsed* rows rather than the page body, because the
    # page's dates carry no year: parsing needs the `asof` from the response
    # that served it, and the two must never be separated (aaii.fetch_text
    # returns the pair, aaii.fetch keeps them together). Tests inject rows
    # here and stay off the network.
    if "aaii" in need and "aaii" not in src:
        src["aaii"] = aaii_adapter.fetch()
    # The ESI feed returns parsed [{date, value}] rows directly; the fetch is
    # one paginated keyless request cycle, shared by every caller of the map.
    if "pentaesi" in need and "pentaesi" not in src:
        src["pentaesi"] = pentaesi_adapter.history()
    # First prints from the committed archive; the fetch also captures a new
    # release the moment the page shows one (write-once, see the adapter).
    if "confboard" in need and "confboard" not in src:
        src["confboard"] = confboard_adapter.history()
    # One workbook download shared by both SCE horizons; write-once dated
    # capture on success, newest archived vintage (with a loud warning) on
    # fetch failure -- the confboard contract, on an xlsx.
    if "sce" in need and "sce" not in src:
        src["sce"] = sce_adapter.history()
    # Harvard-Harris publishes no calendar and no derivable URL, so the
    # archive is the source of truth and nothing here fetches on its own; a
    # new wave is a maintainer passing hhpoll.fetch a URL.
    if "hhpoll" in need and "hhpoll" not in src:
        src["hhpoll"] = hhpoll_adapter.load()
    # One workbook download carries every wave of every subgroup, so all
    # sixteen crosstab cells share a single request the way the five basket
    # series share one Trends comparison. `src["yougov_xtab"]` holds *parsed*
    # waves rather than the workbook bytes -- the same choice `src["aaii"]`
    # makes -- so a test injects a handful of waves and stays off both the
    # network and the zip parser, and the monthly aggregation below is still
    # the code under test rather than something the fixture pre-computed.
    if "yougov_xtab" in need and "yougov_xtab" not in src:
        src["yougov_xtab"] = yougov_xtab_adapter.waves()
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
    # Wikipedia is fetched once per *article*, not once per series or per
    # refresh of the map. `src["wikipedia"]` is a per-article override map --
    # {article: [{date, views}] daily rows} -- which tests inject to stay off
    # the network, and which the loop below fills on first use so two series
    # over one article would cost one request. The rows are daily on purpose:
    # the Monday-Sunday aggregation is this repository's step, and injecting
    # pre-aggregated weeks would let a test pass without ever exercising it.
    if "wikipedia" in need and "wikipedia" not in src:
        src["wikipedia"] = {}

    # Trends works the same way as Civiqs and for the same reason: no single
    # file to prefetch, one archived request cycle per query per day, so the
    # override is a per-series map -- `{series_id: [{date, value}]}` -- and
    # anything not in it is built from (or fetched into) `trends/`.
    if "trends" in need and "trends" not in src:
        src["trends"] = {}

    # The basket is one request serving five series: fetched (or read) once
    # here and shared, so registering all five costs what registering one does.
    if "trends_basket" in need and "trends_basket" not in src:
        src["trends_basket"] = {}

    out = {}
    # {measure: {cell label: monthly series}} -- the one derivation of the
    # YouGov workbook, memoised across the sixteen cells that read it. Local
    # rather than stashed in `src`, because `src` is the injection surface and
    # a caller has no business supplying a half-derived intermediate.
    xtab_monthly = {}
    for sid, spec in SERIES.items():
        f = spec.get("filters") or {}
        if spec["source"] == "umich":
            out[sid] = list(src["umich"])
        elif spec["source"] == "umichparty":
            out[sid] = umichparty_adapter.to_series(
                src["umichparty"], spec["umichparty"]["party"])
        elif spec["source"] == "sb_approval":
            recs = sb.approval_polls(rows=src["sb_approval"], **f)
            out[sid] = sb.to_series(recs, spec["value"])
        elif spec["source"] == "sb_generic":
            recs = sb.generic_ballot_polls(rows=src["sb_generic"], **f)
            out[sid] = sb.to_series(recs, spec["value"])
        elif spec["source"] == "aaii":
            out[sid] = aaii_adapter.to_series(src["aaii"], spec["value"])
        elif spec["source"] == "pentaesi":
            out[sid] = list(src["pentaesi"])
        elif spec["source"] == "confboard":
            out[sid] = list(src["confboard"])
        elif spec["source"] == "sce":
            out[sid] = sce_adapter.to_series(src["sce"],
                                             spec["sce"]["horizon"])
        elif spec["source"] == "hhpoll":
            out[sid] = hhpoll_adapter.to_series(src["hhpoll"])
        elif spec["source"] == "yougov_xtab":
            # Derived once for the whole roster, not once per cell. Sixteen
            # independent aggregations of one payload would be sixteen chances
            # for the cells to disagree about which four waves September had,
            # and a profile whose cells were averaged over different waves is
            # not a profile of anything.
            cfg = spec["yougov_xtab"]
            m = cfg["measure"]
            if m not in xtab_monthly:
                xtab_monthly[m] = crosstab.monthly_cell_series(
                    src["yougov_xtab"], yougov_xtab_adapter.SCORED_CELLS, m)
            out[sid] = list(xtab_monthly[m][cfg["cell"]])
        elif spec["source"] == "trends_basket":
            cfg = spec["trends_basket"]
            given = src["trends_basket"].get(sid)
            if given is not None:
                out[sid] = list(given)
            else:
                key = (tuple(cfg["basket"]), cfg.get("geo", trends_adapter.GEO))
                if key not in src["trends_basket"]:
                    src["trends_basket"][key] = trends_adapter.basket_weeks(
                        list(key[0]), key[1])
                out[sid] = trends_adapter.share_series(
                    src["trends_basket"][key], cfg["query"])
        elif spec["source"] == "civiqs":
            cfg = spec["civiqs"]
            given = src["civiqs"].get(sid)
            out[sid] = list(given) if given is not None else \
                civiqs_adapter.as_displayed(
                    cfg["name"], cfg.get("filters"),
                    choice=cfg.get("choice"), net=cfg.get("net", False),
                    weekday=cfg.get("weekday"))
        elif spec["source"] == "wikipedia":
            art = spec["wikipedia"]["article"]
            if art not in src["wikipedia"]:
                src["wikipedia"][art] = wikipedia_adapter.fetch_daily(art)
            out[sid] = wikipedia_adapter.weekly_series(
                art, daily=src["wikipedia"][art])
        elif spec["source"] == "trends":
            cfg = spec["trends"]
            given = src["trends"].get(sid)
            out[sid] = list(given) if given is not None else \
                trends_adapter.as_archived(cfg["query"],
                                           cfg.get("geo", trends_adapter.GEO))
        else:
            raise ValueError(f"{sid}: unknown source {spec['source']}")
        if not out[sid]:
            raise RuntimeError(
                f"{sid} built to zero points -- refusing to publish an empty "
                "series (check the filters against the upstream file)")
    return out
