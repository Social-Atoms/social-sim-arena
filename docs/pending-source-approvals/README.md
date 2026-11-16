# Source activation decisions awaiting approval

The sources still listed in the table below wait for evidence or permission;
nothing here alters the inventory, collectors, task status or season manifest
for them. The sources cleared on 2026-11-15 are listed after the table. The
approved launch is described in `docs/market-urban-rollout.md`.

| Source | Existing implementation | Required decision or evidence |
| --- | --- | --- |
| MTA subway weekly | Adapter, manual ingest and round templates on dev | Granted 2026-11-15 (automated route cleared); activated on the Market/Urban release branch, not here |
| TSA checkpoints | Adapter and round templates on dev | Granted 2026-11-15; activated on the Market/Urban release branch, not here |
| Michigan inflation | Parser and production contract on dev | Granted 2026-11-15 (see below); a reviewed round with an official release date is the remaining step |
| Netflix Top 10 | Inactive task registration; no live collector/target | Written authorization covering research benchmarking, retrieval and retention |
| ACSI | Inactive task registration; no live collector/target | Written permission, forward release calendar, first-print completeness |
| NYT Books | API adapter, private evidence and ordered-list contract on dev; key configured | Exact sales/publication window evidence and a reviewed future round |
| Steam weekly | API adapter, private evidence and ordered-list contract on dev | Production key, authenticated response verification and measurement boundaries |
| US Mint | Manual importer and increment contract on dev | Eligible fixed-supply product evidence, adjacent reports and collection owner |
| OpenTable | Parked specification card attached; no collector | Parked (decision of 2026-11-15 unchanged): explicit authorization for the proposed use before reopening |

## Cleared on 2026-11-15

The project lead set a new rights standard: a source is usable unless its terms
prohibit SSA's fetch route, SSA keeping an archived copy of what it scored, or
publishing scores; attribution, disclaimer and indemnity clauses are conditions,
not prohibitions, and maintainers confirm. Under it, three cards left this
folder for `docs/data-cards/` with their `archive_policy` rewritten as granted
(approver Haoyang Shang, project lead, 2026-11-15):

| Source | Verdict | Conditions | Where it is now |
| --- | --- | --- | --- |
| Texas mixed beverage (naix-2893) | approved, automated | Credit "Texas Comptroller of Public Accounts" | `texas-mixed-beverage-austin@v3`, `ssa/adapters/tx_mixbev.py`, three scored rounds |
| Chicago CTA (6iiy-9s97) | approved, automated | City of Chicago Data Terms accepted: verbatim City disclaimer on every page, stop on request, indemnity | `chicago-cta-rail-monthly@v3`, `ssa/adapters/cta_daily.py`, three scored rounds |
| Zillow ZORI | approved-no-redistribution, manual only | No automated query; raw CSV only as age ciphertext on `sealed`; "Data Provided by Zillow Group" | `zillow-zori-metro@v3`, `tools/ingest_zori.py`; rounds wait on the first file and the Market Report dates |

Michigan inflation expectations (`consumer_michigan_inflation`) was granted
the same day under the same standard, the way the Michigan party series already
is: the University of Michigan usage agreement
(https://data.sca.isr.umich.edu/agreement.php) lets the data "be displayed,
reformatted, and printed for your organization's use" and reserves consent for
republishing the data themselves. Verdict approved-no-redistribution: the two
official CSVs stay in the private consumer archive (ciphertext on `sealed`),
derived values, hashes and scores are public, credit "University of Michigan
Surveys of Consumers". The inventory row is `integrated` and the website task
is `preparing` until a reviewed round is added; no round was added with the
grant.

MTA and TSA were granted the same day on the Market/Urban release branch. The
credit and disclaimer text for every source lives once, in
`ssa/source_notices.py`.

## Approval procedure

Record the source, evidence URL or permission artifact, permitted access route,
retention/display conditions, attribution, approver and date. A card's `approved`
status approves its proposed question specification; it is not publisher permission.
The attached original cards preserve that distinction and are not source admission.

Once one source clears its requirements, make a focused activation commit with
its inventory decision, operational setup, future question and offline validation.
No silent fallback, retroactive question or automatic merge. Sources can be
split into individual activation PRs as permission arrives.

Existing AAII/Michigan sentiment calendar PR #197 is separate and remains open.
No email or message is sent by this PR.
