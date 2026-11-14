# Consumer Source Review

Website rollout: seven source tasks are registered, including explicit
permission/preparation states, and four SCE/Michigan questions are in the reviewed
season manifest. The normal refresh collects eligible releases and persists
private evidence as age ciphertext before publishing results. See
[Pipeline](PIPELINE.md) for dates, operating steps and remaining source setup.

The [pipeline](PIPELINE.md) connects SCE, Michigan inflation, manual Mint imports,
NYT Books API and Steam Web API to production input freezes, exact-period
settlement and CRPS/RBO scoring. Raw data and input snapshots remain private.
The working agreement forecasts the next chart's members and order; non-US
questions share the existing standings. Netflix and ACSI remain disabled.
The maintainer-controlled inventory gate and reviewed season entry still decide
whether a source can generate live questions; this PR does not grant permissions.

[Permission email drafts](PERMISSION_EMAIL_DRAFTS.md) cover Michigan, ACSI and
Netflix. No email has been sent. The collection tables below are the historical
October 2026 evidence record, not claims of live activation.

See the [evidence summary](EVIDENCE.md) for coverage, observed checks and limits.
See the [terms audit](TERMS.md) and [final response matrix](FINAL_MATRIX.md)
for source permissions and handoff decisions.
See [the implementation contract and tests](PIPELINE.md) for current code behavior.

Prepared on 2026-10-21 and extended on 2026-10-22 for local review. This implements the evidence-collection
stage of the 2026-10-17 handoff. Facts, proposals, and open decisions are separated
below. No organizer feedback or source approval is implied.

## Verified Collection

| Source | Recommendation | Main obstacles |
| --- | --- | --- |
| [NYT](nyt.md) | Continue via API | Private raw responses; exact sales/publication dates; operator API key |
| [Michigan inflation](michigan-inflation.md) | Continue | Preliminary/final vintage separation; release-day archive; exact product rights |
| [US Mint](us-mint.md) | More evidence | Only two weekly reports; buyer/dealer and fixed-batch eligibility; manual collection ownership |
| [Steam](steam.md) | Continue via API | Private raw responses; evidenced Pacific measurement window; operator API key |
| [Netflix](netflix.md) | Defer | Written permission for retrieval and research benchmark use |
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
