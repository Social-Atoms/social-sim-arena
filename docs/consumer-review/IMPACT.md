# Document and Integration Impact

This PR is documentation only. Implementation details below describe a separate
local prototype; source adapters, inventory changes and deployment configuration
will be reviewed in follow-up PRs. No runtime changes are included here.

| Surface | Assessment | Action in this change |
| --- | --- | --- |
| Source documentation/index | Needed: seven evidence cards and truthful implementation state | Added review cards and an index link |
| Inventory | Needed: correct ACSI's factual quarterly/paywall shorthand | Evidence recorded here; inventory and admission states unchanged in this PR |
| Source admission rules | Pending decisions, not permission to rewrite policy | Decision memo only |
| Scalar history shape | Reuse existing `{date, value}` contract | New parsers emit the same records |
| Ranking history shape | Reuse existing `{week_end, items}` contract with explicit date semantics | New parsers emit the same records; rich provenance remains separate |
| Site data | Local review copy needed; public snapshot must retain its meaning | Append candidate histories into an ignored local copy; original fields/rounds remain intact |
| SERIES / PUBLICATION / task registry | Formal targets and forward contracts not yet reviewed | No active registration or schedule changes |
| Templates / official round JSON / resolver | Need source-specific time, candidate, missing-answer and first-print review | No official round changes; no locked-question edits |
| Baselines / score | Existing code can measure compatible histories | Offline persistence/movement diagnostics only; no new aggregation |
| Participant examples / API schemas | No participant I/O contract change | No changes needed |
| Deployment artifacts | Private raw data must remain private even in local deployment builds | Explicitly exclude `.local` in `.vercelignore`; existing Git ignore already covers it |
| Operational documentation | Needed: real code paths, failures, ownership gaps | Added operations review; no deployment claim |

## Suggested PR Boundaries

1. Source evaluation documentation to `dev`, including these seven
   cards, evidence, decisions, reviewer records and impact assessment. No source
   approval is bundled into this change.
2. One reviewed source adapter/maintenance change at a time, with authorized raw
   fixtures, precise dates/units, archive rules and failures. Prefer SCE/Michigan
   reuse where appropriate; do not drop ACSI's research priority.
3. A separate activation change after offline sandbox and research review,
   containing prospective round JSON, freeze-visible history, resolver/baseline
   examples and the candidate/new-item/missing-answer contract.

This PR requests organizer review. Source approval and all seven policy
decisions remain pending. Main publication remains with the maintainers,
including Chance as described by the handoff.
