# Consumer and Market Handoff

This table follows the source survey's order. “Sandbox passed” means a local
synthetic settlement test, not a live or approved forecast round.

| Section | Item | Decision and delivered result |
| --- | --- | --- |
| Main data | NYT Best Sellers | Offline list parser and 52-week private history; automated collection and public data excluded until API terms, attribution and edition policy are verified. No round. |
| Main data | Michigan inflation | One-year preliminary/final parser and source contract merged; offline settlement sandbox passed. No live question added. |
| Main data | U.S. Mint | Manual CSV import and two-report increment parser merged; offline settlement sandbox passed. No product admitted without fixed-supply and buyer checks. |
| Main data | Steam weekly top sellers | Offline US parser retained; automated chart access declined under current terms. No round. |
| Main data | Netflix Top 10 | Automated benchmark use declined under official terms; source PR closed and raw data removed from the Release. No round. |
| Main data | NY Fed SCE extensions | Three prioritized fields (spending, stock prices, unemployment) tested by exact headers; November 9 release sandbox passed. No live question added. |
| Main data | ACSI industry studies | Incorrect quarterly shorthand corrected in inventory and issue #76; activation declined without rights, forward dates and first-print evidence. |
| Section 1 rules | Supply-side | Firm data is input by default or a separate board; Mint exception must pass buyer, batch, supply and sellout checks. Rule added to `docs/sources.md`. |
| Section 1 rules | Live weekly lists | Lock before measurement starts and freeze candidate/new-item rules. Rule added. |
| Section 1 rules | Population | Separate non-US domain is allowed; US personas do not carry over automatically. Rule added. |
| Section 1 rules | Biggest movers | Prospective 20-30 item candidate probabilities rather than a naive repeat-list baseline. Rule added. |
| Section 1 rules | Running totals | Score period increments, keep cumulative totals as diagnostics. Rule added and Mint parser tested. |
| Section 5 decisions | Reopen ACSI | Reopen evidence review, but do not activate the target. Inventory and issue #76 updated. |
| Section 5 decisions | Cadence | Annual multi-item release counts as one large round or separate board, not many main-board rounds. Proposal recorded. |
| Section 5 decisions | Non-US populations | Separate domain; skip US persona weighting when inappropriate. Proposal recorded. |
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
