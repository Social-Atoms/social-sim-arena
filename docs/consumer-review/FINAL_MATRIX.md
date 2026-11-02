# Consumer and Market Handoff

This table follows the source survey's order. The [pipeline](PIPELINE.md)
implements ingestion through production scoring; all tests use synthetic data.
Source rights and live season entries still require maintainer review.

| Section | Item | Decision and delivered result |
| --- | --- | --- |
| Main data | NYT Best Sellers | Official API, private raw archive, exact sales/publication dates, ISBN edition identity and ordered-list RBO. No live round added. |
| Main data | Michigan inflation | One-year preliminary/final parser and source contract merged; offline settlement sandbox passed. No live question added. |
| Main data | U.S. Mint | Manual CSV import and two-report increment parser merged; offline settlement sandbox passed. No product admitted without fixed-supply and buyer checks. |
| Main data | Steam weekly top sellers | Official Web API, fixed country/week, typed product IDs, private raw archive and ordered-list RBO. No Valve email needed for this design. |
| Main data | Netflix Top 10 | Automated benchmark use declined under official terms; source PR closed and raw data removed from the Release. No round. |
| Main data | NY Fed SCE extensions | Three prioritized fields (spending, stock prices, unemployment) tested by exact headers; November 9 release sandbox passed. No live question added. |
| Main data | ACSI industry studies | Incorrect quarterly shorthand corrected in inventory and issue #76; activation declined without rights, forward dates and first-print evidence. |
| Section 1 rules | Supply-side | Firm data is input by default or a separate board; Mint exception must pass buyer, batch, supply and sellout checks. Rule added to `docs/sources.md`. |
| Section 1 rules | Live weekly lists | Lock before measurement starts and freeze candidate/new-item rules. Rule added. |
| Section 1 rules | Population | Same standings and subject domains for non-US questions; state geography/population and use appropriate personas. |
| Section 1 rules | Biggest movers | Forecast future membership and rank order, including new entries; no trivial dropout target or fixed candidate roster. |
| Section 1 rules | Running totals | Score period increments, keep cumulative totals as diagnostics. Rule added and Mint parser tested. |
| Section 5 decisions | Reopen ACSI | Reopen evidence review, but do not activate the target. Inventory and issue #76 updated. |
| Section 5 decisions | Cadence | Annual multi-item release counts as one large round or separate board, not many main-board rounds. Proposal recorded. |
| Section 5 decisions | Non-US populations | Existing standings; skip US persona weighting when inappropriate. Implemented. |
| Section 5 decisions | Supply-side data | MARTS-like firm indicators stay inputs or a separate board. Proposal recorded. |
| Section 5 decisions | Class B streams | Protocol written for window, lock, sampling, missing, late, revision and settlement; TSA/Steam live streams not admitted. |
| Section 5 decisions | Prediction-market baseline | Kalshi comparison declined under official data terms; no main-board change. |
| Section 5 decisions | New YouGov products | Brand Movers/Ratings do not inherit crosstab permission; no automatic retrieval. Inventory/source policy and terms audit updated. |
| Existing sources | YouGov crosstabs | Manual retrieval retained; expected-wave, subgroup and SHA256 archive checks merged. Commercial API/snapshot delivery evaluated; no public automation grant established. |
| Existing sources | Civiqs | Stable-machine plan and read-only stale-archive preflight merged; no deployment or push from another person's machine. |
| Section 2 | Other candidates | Each source has a disposition and one reason in [Other Candidates](OTHER_CANDIDATES.md). No new source activated. |
| Section 7 | Exploratory question designs | Each design has a seasonal, annual, Class B or defer decision in [Other Candidates](OTHER_CANDIDATES.md). None is activated. |

The [Release attachment](https://github.com/Social-Atoms/social-sim-arena/releases/tag/consumer-data-review-2026-10-22)
contains Michigan, SCE and Mint only. The closed data PR's remote branch was
deleted; GitHub may retain historical PR commit objects. Source code and
sandbox tests are in `dev`, while permissions and source admission remain gated
by `ssa/inventory.py`.
