# Consumer Source Review

This is a proposal to `dev` for two market-track organizers. Reviewer feedback,
source admission and formal task activation remain pending.

Code state on `dev`: offline parsers and a local review collector exist for
the three candidate sources only, Michigan inflation, NY Fed SCE extensions and
US Mint (manual import): `ssa/adapters/consumer.py`, `ssa/consumer.py`,
`tools/consumer_data.py` and `tests/test_consumer_*.py`. None of it schedules a
round, and their inventory rights stay `unresolved` until maintainer sign-off.
Prototype code for NYT, Netflix, Steam and ACSI was merged and then removed
pending source terms; their cards below are evaluation records only.

See the [evidence summary](EVIDENCE.md) for coverage, observed checks and limits.
See the [terms audit](TERMS.md) and [final response matrix](FINAL_MATRIX.md)
for source permissions and handoff decisions.
Follow-up implementation will be split by source.

Prepared on 2026-10-21 and extended on 2026-10-22 for local review. This implements the evidence-collection
stage of the 2026-10-17 handoff. Facts, proposals, and open decisions are separated
below. No organizer feedback or source approval is implied.

## Verified Collection

| Source | Recommendation | Main obstacles |
| --- | --- | --- |
| [NYT](nyt.md) | More evidence | API terms and attribution; edition/new-entry policy; publication versus sales window |
| [Michigan inflation](michigan-inflation.md) | Continue | Preliminary/final vintage separation; release-day archive; exact product rights |
| [US Mint](us-mint.md) | More evidence | Only two weekly reports; buyer/dealer and fixed-batch eligibility; manual collection ownership |
| [Steam](steam.md) | More evidence | Retrieval terms; Pacific week boundary; open-list/new-game contract |
| [Netflix](netflix.md) | More evidence | TSV rights; season/title identity; premeasurement lock and new releases |
| [SCE](sce.md) | Continue | Select stable indicators; release-day vintages; complete monthly wave checks |
| [ACSI](acsi.md) | Defer | Forward release calendar; incomplete first prints; use and redistribution terms |

These recommendations are preparer proposals, not organizer approvals.

| Source | Acquired data | Remaining collection work |
| --- | --- | --- |
| [NYT](nyt.md) | 52 consecutive Hardcover Fiction top-ten lists, publication labels 2025-11-02 through 2026-10-25 | Historical first-print evidence, edition policy, rights review, and other list selection |
| [Michigan inflation](michigan-inflation.md) | Official final CSV and current preliminary CSV; two horizons through October 2026 preliminary | Historical preliminary vintages and release-calendar evidence |
| [US Mint](us-mint.md) | Two manually downloaded official CSVs, 2026-10-11 and 2026-10-18; 698 matched SKUs and weekly net changes | Longer weekly history, eligible-product selection, limits, and buyer metadata |
| [Steam](steam.md) | 52 consecutive US weekly top-10 revenue lists, starting 2025-10-21 | Retrieval/redistribution review and exact boundary confirmation |
| [Netflix](netflix.md) | 277 US film lists and 277 US TV lists, ending 2026-10-18 | Rights, entity/candidate policy, and original historical vintages |
| [SCE](sce.md) | Nine extra monthly series, June 2013 through September 2026 | Review which indicators have adequate signal and task value |
| [ACSI](acsi.md) | 106 official release pages; 47 benchmark pages; 1,084 verified benchmark cells plus release-prose histories | Remaining historical company extraction, 82 undated benchmark cells, and historical first-print evidence |

The original research priority increases down this table. Engineering readiness
does not change that priority. All seven remain proposed targets for review.

The current totals and per-industry coverage are generated in the local reports
below. Appending the candidate histories preserves every existing site-data
record. ACSI instruments include industry and smaller-company aggregates as well
as company/brand scores; instrument counts are not counts of distinct companies.
Many histories remain sparse, and retrospective first-print status is unverified.

## Evidence Access

The [evidence summary](EVIDENCE.md) documents data shapes, coverage, tests and
limitations. The Michigan, SCE and Mint data, original artifacts, schema and
provenance are in the [review snapshot release](https://github.com/Social-Atoms/social-sim-arena/releases/tag/consumer-data-review-2026-10-22)
attachment. NYT, Netflix, Steam and ACSI remain excluded after the terms audit.
Credentials are not included. The snapshot does not establish
release-day first prints or grant formal source admission.

## Review Records

| Source | Organizer 1 | Organizer 2 |
| --- | --- | --- |
| NYT | Pending | Pending |
| Michigan inflation | Pending | Pending |
| US Mint | Pending | Pending |
| Steam | Pending | Pending |
| Netflix | Pending | Pending |
| SCE extensions | Pending | Pending |
| ACSI | Pending | Pending |

Reviewers should independently record proceed / return for evidence / defer,
with reasons. See [decisions](DECISIONS.md), [operations](OPERATIONS.md), and
[document impact](IMPACT.md). Source-level approval precedes live adapters,
sandbox round designs, and a separate formal activation change.
