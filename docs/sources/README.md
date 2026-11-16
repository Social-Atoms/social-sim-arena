# Sources

One page per source the arena scores against. Each says the same five things, in the same order: what the number is, where it is published, how the arena reads it, how a question on it resolves, and what to watch out for. The page is the reader's entry; the adapter it links is the code that does the reading.

**Credit and disclaimers.** Each source's credit line, and any disclaimer its terms require word for word, is written once in `ssa/source_notices.py` (decision of 2026-11-15: one standardized notice everywhere). `tools/publish_source_notices.py` publishes it as `site/sources.json`; every question and forecast page ends with that source's block and the general SSA notice, and the docs list all of them under [Data sources & terms](https://social-simulation-arena.com/docs.html#data-sources). Edit the notice there, never on a page.

| Source | Page | Tasks on the site | Reader |
| --- | --- | --- | --- |
| Economist / YouGov | [yougov.md](yougov.md) | Economist / YouGov approval, YouGov measured crosstabs, the YouGov half of Generic ballot & midterm | `ssa/adapters/silverbulletin.py`, `ssa/adapters/yougov_xtab.py` |
| Morning Consult | [morning-consult.md](morning-consult.md) | Morning Consult approval | `ssa/adapters/silverbulletin.py` |
| Silver Bulletin poll database | [silver-bulletin.md](silver-bulletin.md) | Generic ballot & midterm; the poll-level records behind the two approval tasks above | `ssa/adapters/silverbulletin.py`, `ssa/average.py` |
| Civiqs | [civiqs.md](civiqs.md) | Civiqs approval, the five Civiqs sentiment trackers, Civiqs 16-cell profile | `ssa/adapters/civiqs.py` |
| Harvard CAPS / Harris | [harvard-harris.md](harvard-harris.md) | Harvard / Harris approval | `ssa/adapters/hhpoll.py` |
| University of Michigan | [michigan.md](michigan.md) | Michigan consumer sentiment, Sentiment by party | `ssa/adapters/umich.py`, `ssa/adapters/umichparty.py` |
| New York Fed SCE | [ny-fed-sce.md](ny-fed-sce.md) | Household inflation expectations | `ssa/adapters/sce.py` |
| New York Fed SCE, moving expectations | [ny-fed-sce.md](ny-fed-sce.md#moving-expectations-ten-cell-profile) | None yet: ten-cell profile registered (Urban Track, housing), first round proposed for the 2026-12-05 lock | `ssa/adapters/sce.py` |
| European Commission (DG ECFIN) | [ec-consumer-survey.md](ec-consumer-survey.md) | None yet: twelve series registered (EU, euro area, ten countries), no rounds scheduled | `ssa/adapters/ecbcs.py` |
| Seattle Police Department (City of Seattle) | [seattle-police-calls.md](seattle-police-calls.md) | None yet: archived daily for the Urban Track pilot; series `seattle_calls_weekly` registered and wired (`ssa/urban.py`), no rounds scheduled | `ssa/adapters/seattle_cad.py` |
| NYC 311 (NYC Open Data) | [nyc-311.md](nyc-311.md) | None yet: archived daily for the Urban Track; series registered and wired (`ssa/urban.py`); proposed rounds in [nyc-311-requests.rounds.json](../data-cards/nyc-311-requests.rounds.json) | `ssa/adapters/nyc311.py` |
| MTA, on NY Open Data (data.ny.gov) | [mta.md](mta.md) | None yet: downloaded weekly by a person for the Urban Track; series registered and wired (`ssa/urban.py`), rights unresolved, no rounds scheduled | `ssa/adapters/mta_daily.py`, `tools/ingest_mta_daily.py` |
| TSA (tsa.gov passenger volumes) | [tsa.md](tsa.md) | None yet: archived daily for the Urban Track; series registered and wired (`ssa/urban.py`), rights unresolved, no rounds scheduled | `ssa/adapters/tsa_volumes.py` |
| US Bureau of Labor Statistics (CPI rent) | [bls-cpi-rent.md](bls-cpi-rent.md) | None yet: one series registered (Urban Track, housing), pilot and first scored rounds proposed | `ssa/adapters/bls_cpi.py` |
| Land Transport Authority (via data.gov.sg) | [singapore-coe.md](singapore-coe.md) | None yet: COE Category A premium, captured around each lock and result; proposed rounds only | `ssa/adapters/sg_coe.py` |
| Land Transport Authority (via data.gov.sg) | [singapore-taxis.md](singapore-taxis.md) | None yet: free taxis in Downtown Core, Friday 23:00, captured weekly; proposed rounds only | `ssa/adapters/sg_taxi.py` |
| Housing & Development Board (via data.gov.sg) | [singapore-carparks.md](singapore-carparks.md) | None yet: Clementi HDB carpark occupancy, Saturday 20:00, captured weekly; proposed rounds only | `ssa/adapters/sg_carpark.py` |
| Land Transport Authority (DataMall) | [singapore-passenger-volume.md](singapore-passenger-volume.md) | None: blocked until a maintainer registers for a DataMall key; adapter written, not wired | `ssa/adapters/sg_lta_pv.py` |
| AAII | [aaii.md](aaii.md) | Investor sentiment | `ssa/adapters/aaii.py` |
| Google Trends | [google-trends.md](google-trends.md) | Brand search-interest mix | `ssa/adapters/trends.py` |
| Wikimedia | [wikimedia.md](wikimedia.md) | Wikipedia weekly top 10 | `ssa/adapters/wikipedia.py` |
| Texas Comptroller (Texas Open Data, naix-2893) | [card](../data-cards/texas-mixed-beverage-austin.json) | Austin mixed-beverage receipts index: rights granted 2026-11-15; daily automated capture; three scored rounds, locks 2027-01-31 to 2027-03-31 | `ssa/adapters/tx_mixbev.py` |
| Chicago Transit Authority (City of Chicago Data Portal, 6iiy-9s97) | [card](../data-cards/chicago-cta-rail-monthly.json) | CTA rail boardings, monthly: rights granted 2026-11-15 with the City's Data Terms conditions (verbatim disclaimer on every page, stop on request, indemnity); daily automated capture; three scored rounds, locks 2027-01-31 to 2027-03-31 | `ssa/adapters/cta_daily.py` |
| Zillow Research (ZORI metro file) | [card](../data-cards/zillow-zori-metro.json) | None yet: rights granted 2026-11-15 for a person's monthly browser download; raw file only as ciphertext on `sealed`, sha256 and derived changes public, 'Data Provided by Zillow Group'; rounds wait on the first file and the Market Report dates | `ssa/adapters/zillow_zori.py`, `tools/ingest_zori.py` |

Two rules hold across every page. A number is read from an archive the arena keeps, never from whatever the publisher shows at the moment of scoring, so every resolution can be checked later. And a source that goes quiet fails loudly rather than serving something stale; [sources.md](../sources.md) is the process for adding a source and says why.

Seven proposed consumer/market sources have a separate
[review index](../consumer-review/README.md) and
[evidence summary](../consumer-review/EVIDENCE.md). Their collection prototypes
do not make them active arena tasks; organizer review is pending.
