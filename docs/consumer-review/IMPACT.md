# Document and Integration Impact

Code state on `dev`: offline parsers and a local review collector exist for
the three candidate sources only, Michigan inflation, NY Fed SCE extensions and
US Mint (manual import): `ssa/adapters/consumer.py`, `ssa/consumer.py`,
`tools/consumer_data.py` and `tests/test_consumer_*.py`. None of it schedules a
round, and their inventory rights stay `unresolved` until maintainer sign-off.
Prototype code for NYT, Netflix, Steam and ACSI was merged and then removed
pending source terms; their cards below are evaluation records only.
Inventory rows exist for all seven sources; none has generating rights. No
refresh, round, site or deployment change is included.

| Surface | Assessment | Action in this change |
| --- | --- | --- |
| Source documentation/index | Needed: seven evidence cards and truthful implementation state | Added review cards and an index link |
| Inventory | Needed: record each source's state; ACSI reopen proposal | Rows added as `unresolved`/`rejected`; ACSI row unchanged apart from a pointer to the proposal in [acsi.md](acsi.md) |
| Source admission rules | Pending decisions, not permission to rewrite policy | Decision memo only |
| Scalar history shape | Reuse existing `{date, value}` contract | Michigan/SCE/Mint parsers on `dev` emit it; no existing parser changed |
| Ranking history shape | Reuse existing `{week_end, items}` contract with explicit date semantics | No ranking parser remains on `dev`; the NYT/Netflix/Steam prototypes were removed pending terms |
| Site data | Review copy must preserve the public snapshot | `tools/consumer_data.py build` checks preservation; no site-data change |
| SERIES / PUBLICATION / task registry | Formal targets and forward contracts not yet reviewed | No active registration or schedule changes |
| Templates / official round JSON / resolver | Need source-specific time, candidate, missing-answer and first-print review | No official round changes; no locked-question edits |
| Baselines / score | Existing code can measure compatible histories | Offline persistence/movement diagnostics only; no new aggregation |
| Participant examples / API schemas | No participant I/O contract change | No changes needed |
| Deployment artifacts | Collection credentials and private working files must remain private | No deployment configuration changed in this PR; existing Git and Vercel exclusions remain in place |
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
