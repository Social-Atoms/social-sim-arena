# European Commission consumer survey (DG ECFIN)

**What the number is.** The consumer confidence indicator of the Joint Harmonised EU Programme of Business and Consumer Surveys, run by DG ECFIN with national partner institutes on one common questionnaire. It is the simple average of four seasonally adjusted balances (percent positive minus percent negative, in percentage points): the household's financial situation over the last 12 months and over the next 12 months, the general economic situation in the country over the next 12 months, and intentions to make major purchases over the next 12 months ([methodology](https://economy-finance.ec.europa.eu/economic-forecast-and-surveys/business-and-consumer-surveys/methodology-business-and-consumer-surveys/methodological-concepts_en)). About 32,000 consumers are surveyed every month across the EU; interviews run over roughly the first three weeks of the month (1 to 21 October in October 2026). The arena registers twelve series: the EU and euro-area aggregates (`ec_cci_eu`, `ec_cci_ea`), and ten countries in a fixed order, `series.EC_CCI_CELLS`: Germany, France, Italy, Spain, the Netherlands, Poland, Austria, Belgium, Sweden and Ireland (`ec_cci_de` ... `ec_cci_ie`). The ten are a cross-national profile on the existing `profile_energy` type: they move with a common European mood and they also move apart.

**Where it is published.** [economy-finance.ec.europa.eu, Business and consumer surveys](https://economy-finance.ec.europa.eu/economic-forecast-and-surveys/business-and-consumer-surveys_en), on a [calendar fixed a year ahead](https://ec.europa.eu/economy_finance/db_indicators/surveys/documents/calendar/Publication%20dates%202026.pdf). Two releases a month:

| release | what | time (Brussels) | 2026 dates still to come |
|---|---|---|---|
| Flash consumer confidence indicator | EU and euro area only, a PDF press release | 16:00 | 20 November, 21 December |
| Full Business and Consumer Survey release | every country, plus the time-series files | 11:00 | 27 November; the December survey on 7 January 2027 |

The flash comes on the deadline for national deliveries, about seven working days before month end; the full release comes at month end. The 2026 flash dates were 22 Jan, 19 Feb, 23 Mar, 22 Apr, 21 May, 22 Jun, 23 Jul, 21 Aug, 22 Sep, 22 Oct; the full releases 29 Jan, 26 Feb, 30 Mar, 29 Apr, 28 May, 29 Jun, 30 Jul, 28 Aug, 29 Sep, 29 Oct. There is no August or holiday break; December's full release moves into January. The 2027 calendar was not yet posted on 2026-10-29 (`Publication dates 2027.pdf` redirects).

The machine-readable release is one zip per month at a URL that names the release:

```
https://ec.europa.eu/economy_finance/db_indicators/surveys/documents/series/nace2_ecfin_{YYMM}/main_indicators_sa_nace2.zip
```

`{YYMM}` is the reference month (`2610` is the October 2026 survey, published 29 October 2026), so a round can name the file that will settle it before the file exists. A release that does not exist yet answers HTTP 301 to the survey's home page.

**How the arena reads it.** [`ssa/adapters/ecbcs.py`](https://github.com/Social-Atoms/social-sim-arena/blob/main/ssa/adapters/ecbcs.py) fetches the zip without following redirects, so "not published yet" can never be read as an HTML page, and parses the workbook inside with the standard library (an xlsx is a zip of XML, as in `ssa/adapters/sce.py`). The sheet is found by its name (`MONTHLY`) and every column by its exact header (`EU.CONS`, `DE.CONS`, ...), never by position. Rows are dated by the reference month, first of the month. Each release is archived write-once under `sources/ecbcs/<YYMM>.zip`, exactly as served; a month whose release is already archived costs no request. The parser raises if the aggregates do not run without a gap from January 1985 to the release's own month, if a required country column is missing, or if a value is not a balance; a dropped connection serves the archive with a diagnostic; an archive more than three months behind raises.

**How a question resolves.** On the value first published for the reference month, in that month's full release: `ecbcs.first_print(geo, month)`, read from `sources/ecbcs/<YYMM>.zip`. Later releases never overwrite it.

- A round on the EU or euro-area number has to lock 48 hours before the **flash** (16:00 Brussels time), not before the full release, because the flash publishes the same number about a week earlier. Over the thirteen months October 2025 to October 2026 the flash and the full release's first EU and euro-area print were identical in twelve. The exception was December 2025, when Spain's results came back into the aggregate in the full release, putting it 1.2 (EU) and 1.5 (euro area) points above the flash.
- A country profile round locks 48 hours before the full release (11:00 Brussels time) and resolves all ten cells from that release.

**Revision policy: pin the first print.** Every release recomputes seasonal adjustment and the aggregates, and the workbook's own release notes (its INFO sheet) record level shifts that rewrite whole histories. Comparing each month's first print with the next release's value for the same month, over the fourteen releases September 2025 to October 2026:

| series | mean absolute revision | largest | mean absolute month-on-month change of the first prints |
|---|---|---|---|
| EU | 0.18 | +1.2 (Nov 2025, Spain re-included) | 1.32 |
| euro area | 0.16 | +1.4 (Nov 2025) | 1.43 |
| Poland | 0.89 | -11.6 (Apr 2026: -4.9 became -16.5, new provider) | 2.52 |
| the other nine countries | 0.02 to 0.17 | 0.6 or less | 0.77 (Spain) to 2.89 (Ireland) |

So a revision is usually a tenth of a point, but a methodology change can move a month by more than its whole monthly range, which is why only the first print is scored.

**Rights.** Free, no key, nothing in `ec.europa.eu/robots.txt` covers `/economy_finance/`. The [Commission legal notice](https://commission.europa.eu/legal-notice_en), linked from the survey pages' footer:

> The Commission's reuse policy is implemented by the Commission Decision of 12 December 2011 on the reuse of Commission documents. Unless otherwise indicated (e.g. in individual copyright notices), content owned by the EU on this website is licensed under the Creative Commons Attribution 4.0 International (CC BY 4.0) licence. This means that reuse is allowed, provided appropriate credit is given and changes are indicated.

The decision is [2011/833/EU](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32011D0833). The survey's press releases carry their own notice: "© European Union, 2026. Reproduction is authorised provided the source is acknowledged." Retrieval, use and redistribution are all open, so `ssa/inventory.py` records `rights: approved` (not `approved-no-redistribution`), and the committed release zips need no blocklist entry. Credit: European Commission, DG ECFIN, Business and Consumer Surveys; the series methodology text carries it.

**Movement (docs/sources.md section 1.4).** From the October 2026 release, monthly values since January 2015: mean absolute month-on-month change 1.19 points for the EU and 1.25 for the euro area (0.89 and 0.93 since 2023), and 1.30 (Germany) to 2.70 (Ireland) for the ten countries. `scoring.noise_floor` puts real movement per release at 1.9 to 3.8 points against measurement noise of 0 to 1.4: the published aggregates are seasonally adjusted averages over large samples, so most month-to-month movement is signal.

**What to watch.**

- **A country can be missing from its own release.** Italy's September 2026 value was not in the September release (a methodological break between August and September 2026, per the October flash) and was first published in October's. Spain was suspended from October 2024 until the December 2025 release; Ireland from February 2023 until March 2025; Estonia has been suspended since May 2026. A profile round with a missing cell does not resolve (`ssa/profile_round.py` refuses a profile with a hole), so a maintainer scheduling one should check the INFO sheet of the latest release first.
- **Pre-release availability is not yet verified.** The Last-Modified stamps of the 2608, 2609 and 2610 zips are 3 to 5 days before their release days. The next month's URL (`nace2_ecfin_2611`) answered 301 on 2026-10-29, but nobody has yet probed a release URL in the days just before its release. That check (request `nace2_ecfin_2611` between 20 and 27 November 2026) should be done before the first country round is scheduled; if the file is reachable early, the lock moves to before the upload.
- **Aggregates change composition.** Bulgaria joined the euro area in January 2026 and the euro-area history was recomputed; since February 2020 the EU series is computed on the 27 current member states over its whole history. The aggregate a round names is the aggregate as published that month.
- Like Michigan and the NY Fed SCE, a month-labelled series cannot be frozen by a date filter; a round relies on the lock snapshot of the history the entrants were handed.

**Status.** The source and its twelve series are registered and the first two releases (`2609`, `2610`) are archived. No rounds are scheduled: the season is frozen up front and promoting a round into `questions/season0.json` is a maintainer's commit. `tools/generate_rounds.py` reports this family as `unsupported_family` until a template is written for it.
