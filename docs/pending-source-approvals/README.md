# Source activation decisions awaiting approval

This PR remains unmerged until the relevant evidence or permission arrives.
It does not alter the inventory, collectors, task status or season manifest.
The approved launch is described in `docs/market-urban-rollout.md` on dev.

| Source | Existing implementation | Required decision or evidence |
| --- | --- | --- |
| MTA subway weekly | Adapter, manual ingest and proposed round templates on dev | Rights admission and a weekly manual collection owner/backup; API automation is not authorized |
| TSA checkpoints | Adapter and proposed round templates on dev; automatic collection disabled | Rights admission and verified runner access or a manual courier |
| Michigan inflation | Disabled parser and production contract on dev | Publisher permission before any new inflation question |
| Netflix Top 10 | Inactive task registration; no live collector/target | Written authorization covering research benchmarking, retrieval and retention |
| ACSI | Inactive task registration; no live collector/target | Written permission, forward release calendar, first-print completeness |
| NYT Books | API adapter, private evidence and ordered-list contract on dev; key configured | Exact sales/publication window evidence and a reviewed future round |
| Steam weekly | API adapter, private evidence and ordered-list contract on dev | Production key, authenticated response verification and measurement boundaries |
| US Mint | Manual importer and increment contract on dev | Eligible fixed-supply product evidence, adjacent reports and collection owner |
| Texas mixed beverage | Specification card attached; no adapter | Written permission, then implementation and a reviewed round |
| Chicago CTA | Specification card attached; no adapter | Written permission, then implementation and a reviewed round; currently deferred by the owner |
| Zillow ZORI | Unapproved specification card attached; no adapter | Written permission and card review, then implementation |
| OpenTable | Parked specification card attached; no collector | Explicit authorization for the proposed use before reopening |

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
