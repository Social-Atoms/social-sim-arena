"""One data-source notice per source: the credit line and any disclaimer its terms require.

The project lead's decision of 2026-11-15: one standardized attribution and
disclaimer claim, used everywhere. Before this file the credit lines lived in
three places -- `ssa/consumer_round.ATTRIBUTION`, the methodology strings in
`ssa/series.py`, and prose in `docs/sources/*.md` -- and only consumer rounds
showed one on the site. Now every notice is written once, here, keyed by the
source's row in `ssa/inventory.py`:

- `attribution`: the credit line, worded as the publisher's terms ask where
  they ask for specific words (FRBNY, Singapore Open Data Licence, MTA, ...),
  otherwise a plain "Source: ..." credit.
- `disclaimer`: text the terms require us to show verbatim (City of Chicago),
  or None. A disclaimer is copied exactly; never paraphrase one.
- `url`: the publisher's own page for the data.
- `details`: the repository page that records the terms we read, when one
  exists.

`tools/publish_source_notices.py` turns this table into `site/sources.json`,
which the site reads to print the same block under every question and forecast
page whose series comes from the source, and the full list under "Data sources
& terms" in the docs. `GENERAL_NOTICE` is printed with every block and at the
head of that list. `tests/test_source_notices.py` fails if the committed JSON
drifts from this table, if a notice names a source the inventory does not
know, or if a live round's source has no notice.

Rights verdicts stay in `ssa/inventory.py`; this file decides nothing about
whether a source may be used, only how it is credited once it is.
"""

GENERAL_NOTICE = (
    "Social Simulation Arena (SSA) is an independent, non-commercial research "
    "benchmark. It is not affiliated with, sponsored by or endorsed by any "
    "publisher whose data it uses. The numbers shown are derived by SSA from "
    "public sources as archived at the time stated, and may differ from the "
    "publisher's current figures. Names and trademarks belong to their owners.")

# Required verbatim by the City of Chicago Data Portal Terms of Use for any
# application that uses its data (decision of 2026-11-15: accepted).
CHICAGO_DISCLAIMER = (
    "This site provides applications using data that has been modified for use "
    "from its original source, www.cityofchicago.org, the official website of "
    "the City of Chicago. The City of Chicago makes no claims as to the "
    "content, accuracy, timeliness, or completeness of any of the data provided "
    "at this site. The data provided at this site is subject to change at any "
    "time. It is understood that the data provided at this site is being used "
    "at one's own risk.")

FRBNY_SCE = (
    "Source: Survey of Consumer Expectations, © 2013-2026 Federal Reserve Bank "
    "of New York (FRBNY). The SCE data are available without charge at "
    "http://www.newyorkfed.org/microeconomics/sce and may be used subject to "
    "license terms posted there. FRBNY disclaims any responsibility for this "
    "analysis and interpretation of Survey of Consumer Expectations data.")

MICHIGAN = ("Source: Surveys of Consumers. Copyright © 2026, The Regents of "
            "the University of Michigan. All Rights Reserved.")

_SG_ODL = ("made available under the terms of the Singapore Open Data Licence "
           "version 1.0 (https://data.gov.sg/open-data-licence).")

_DOCS = "https://github.com/Social-Atoms/social-sim-arena/blob/main/docs/sources/"


def _n(name, attribution, url, details=None, disclaimer=None):
    return {"name": name, "attribution": attribution, "disclaimer": disclaimer,
            "url": url, "details": (_DOCS + details) if details else None}


NOTICES = {
    # --- polls and opinion trackers ------------------------------------------
    "sb_approval": _n(
        "Silver Bulletin", "Source: Silver Bulletin polling database (Nate Silver).",
        "https://www.natesilver.net/", "silver-bulletin.md"),
    "sb_generic": _n(
        "Silver Bulletin", "Source: Silver Bulletin polling database (Nate Silver).",
        "https://www.natesilver.net/", "silver-bulletin.md"),
    "civiqs": _n(
        "Civiqs", "Source: Civiqs (civiqs.com), daily tracking results.",
        "https://civiqs.com/", "civiqs.md"),
    "yougov_xtab": _n(
        "The Economist/YouGov",
        "Source: The Economist/YouGov poll, YouGov public data, used under "
        "YouGov's public data licence (CC BY-NC 4.0).",
        "https://today.yougov.com/", "yougov.md"),
    "hhpoll": _n(
        "Harvard CAPS / Harris Poll", "Source: Harvard CAPS / Harris Poll toplines.",
        "https://harvardharrispoll.com/", "harvard-harris.md"),
    "aaii": _n(
        "AAII Sentiment Survey", "Source: AAII Investor Sentiment Survey.",
        "https://www.aaii.com/sentimentsurvey", "aaii.md"),
    "certified_election_results": _n(
        "US state election authorities",
        "Source: certified results published by US state election authorities.",
        "https://www.eac.gov/"),
    # --- consumer and economic surveys ---------------------------------------
    "umich": _n(
        "University of Michigan Surveys of Consumers", MICHIGAN,
        "https://www.sca.isr.umich.edu/", "michigan.md"),
    "umichparty": _n(
        "University of Michigan Surveys of Consumers", MICHIGAN,
        "https://www.sca.isr.umich.edu/", "michigan.md"),
    "consumer_michigan_inflation": _n(
        "University of Michigan Surveys of Consumers", MICHIGAN,
        "https://www.sca.isr.umich.edu/", "michigan.md"),
    "sce": _n("Federal Reserve Bank of New York, SCE", FRBNY_SCE,
              "https://www.newyorkfed.org/microeconomics/sce", "ny-fed-sce.md"),
    "sce_moving": _n("Federal Reserve Bank of New York, SCE", FRBNY_SCE,
                     "https://www.newyorkfed.org/microeconomics/sce", "ny-fed-sce.md"),
    "consumer_sce_extensions": _n(
        "Federal Reserve Bank of New York, SCE", FRBNY_SCE,
        "https://www.newyorkfed.org/microeconomics/sce", "ny-fed-sce.md"),
    "bls_cpi": _n(
        "US Bureau of Labor Statistics", "Source: U.S. Bureau of Labor Statistics.",
        "https://www.bls.gov/cpi/", "bls-cpi-rent.md"),
    "ecbcs": _n(
        "European Commission, DG ECFIN",
        "Source: European Commission, DG ECFIN, Business and Consumer Surveys. "
        "© European Union, 2026, reused under CC BY 4.0; SSA computes "
        "derived values from the published series.",
        "https://economy-finance.ec.europa.eu/economic-forecast-and-surveys/"
        "business-and-consumer-surveys_en", "ec-consumer-survey.md"),
    "consumer_mint": _n("United States Mint", "Source: United States Mint.",
                        "https://www.usmint.gov/about/production-sales-figures"),
    "consumer_nyt": _n("The New York Times", "Data provided by The New York Times.",
                       "https://developer.nytimes.com/"),
    "consumer_steam": _n("Steam", "Data from Steam. This project is not endorsed by Valve.",
                         "https://store.steampowered.com/"),
    # --- attention -----------------------------------------------------------
    "wikipedia": _n(
        "Wikimedia Foundation", "Source: Wikimedia Foundation pageview data "
        "(Wikimedia REST API).", "https://wikimedia.org/api/rest_v1/",
        "wikimedia.md"),
    "trends": _n("Google Trends", "Source: Google Trends.",
                 "https://trends.google.com/", "google-trends.md"),
    "trends_basket": _n("Google Trends", "Source: Google Trends.",
                        "https://trends.google.com/", "google-trends.md"),
    # --- urban: United States ------------------------------------------------
    "seattle_cad": _n(
        "City of Seattle",
        "Source: City of Seattle, Call Data (33kz-ixgy), Seattle Open Data; "
        "weekly totals computed by SSA from the snapshot dated on each question.",
        "https://data.seattle.gov/", "seattle-police-calls.md"),
    "nyc311": _n(
        "NYC Open Data",
        "Source: NYC 311 / NYC Open Data, 311 Service Requests from 2020 to "
        "Present (erm2-nwe9); weekly totals computed by SSA from the snapshot "
        "dated on each question.",
        "https://data.cityofnewyork.us/d/erm2-nwe9", "nyc-311.md"),
    # --- urban: Singapore ----------------------------------------------------
    "sg_coe": _n(
        "Land Transport Authority (data.gov.sg)",
        "Contains information from COE Bidding Results / Prices accessed on the "
        "date in each capture's name from data.gov.sg which is " + _SG_ODL,
        "https://data.gov.sg/", "singapore-coe.md"),
    "sg_taxi": _n(
        "Land Transport Authority (data.gov.sg)",
        "Contains information from Taxi Availability and Master Plan 2019 "
        "Planning Area Boundary (No Sea) accessed on the date in each file's "
        "path from data.gov.sg which is " + _SG_ODL,
        "https://data.gov.sg/", "singapore-taxis.md"),
    "sg_carpark_hdb": _n(
        "Housing & Development Board (data.gov.sg)",
        "Contains information from Carpark Availability, HDB Carpark Information "
        "and Master Plan 2019 Planning Area Boundary (No Sea) accessed on the "
        "date in each file's path from data.gov.sg which is " + _SG_ODL,
        "https://data.gov.sg/", "singapore-carparks.md"),
    "sg_lta_pv": _n(
        "Land Transport Authority (LTA DataMall)",
        "Contains information from LTA DataMall Passenger Volume by Train "
        "Stations / Bus Stops, accessed on the dates recorded with each file, "
        "made available under the Singapore Open Data Licence version 1.0 "
        "(https://datamall.lta.gov.sg/content/datamall/en/SingaporeOpenDataLicence.html).",
        "https://datamall.lta.gov.sg/", "singapore-passenger-volume.md"),
}

# Rounds whose series is not in `ssa/series.SERIES` (ranking, election and
# consumer rounds keep their own registries). Keyed by series id.
SERIES_EXTRA = {
    "wiki_top10_en": "wikipedia",
    "generic_ballot_margin": "certified_election_results",
    "house_seats": "certified_election_results",
}

# `ssa/consumer_round` names its sources by a short key; the notice is the
# inventory row's.
CONSUMER_SOURCE = {
    "sce": "consumer_sce_extensions",
    "mint": "consumer_mint",
    "nyt": "consumer_nyt",
    "steam": "consumer_steam",
    "michigan_current": "consumer_michigan_inflation",
    "michigan_finals": "consumer_michigan_inflation",
}


def notice_for(source):
    """The notice dict for an inventory source key, or None."""
    row = NOTICES.get(source)
    return dict(row) if row else None


def text(source):
    """Attribution and disclaimer as one line, for places that print text."""
    row = NOTICES[source]
    return row["attribution"] + (" " + row["disclaimer"] if row["disclaimer"] else "")


def series_sources():
    """{series id: inventory source} for every series a round can name."""
    from .series import SERIES
    from .consumer_round import SERIES_SOURCE
    out = {sid: m["source"] for sid, m in SERIES.items() if m.get("source")}
    out.update({sid: CONSUMER_SOURCE[s] for sid, s in SERIES_SOURCE.items()})
    out.update(SERIES_EXTRA)
    return dict(sorted(out.items()))


def site_payload():
    """What `site/sources.json` holds: the general notice, one entry per source
    with a notice, and the series -> source map the pages look rounds up by."""
    # In table order (grouped by kind), so the docs list reads by topic and a
    # new source lands beside its neighbours rather than wherever it sorts.
    sources = [dict(NOTICES[key], source=key) for key in NOTICES]
    series = {sid: src for sid, src in series_sources().items() if src in NOTICES}
    return {"general": GENERAL_NOTICE, "sources": sources, "series": series}
