# Other Candidates

These are disposition decisions for Section 2 of the source survey, not
additional collection work in this batch. “Seasonal special” means one
prospective question per season rather than weekly rounds.

## What the dispositions mean

Sources fall into two classes:

- **Class A**: a third party publishes the number on a schedule announced in
  advance. The round locks 48 hours before the release, so the answer does not
  exist at lock time, and someone outside the arena chose both the measure and
  the date.
- **Class B**: a continuous stream, such as a live chart, a daily count or a
  running rating, that the arena would read at times it chooses. The arena then
  sets the observation window as well as reading the answer, so it both writes
  the question and resolves it, and it must archive every reading itself
  because the publisher keeps no dated copy.

**Wait for Class B** means the source is not added until the arena adopts a
protocol for Class B sources (issue #17): when a reading is taken, how the
window is fixed before the lock, and how each reading is archived and hashed
so that it can be checked later. A question pinned to a date a third party
sets, such as a rating seven days after an announced release, reduces the
problem but still needs that protocol. "And terms" means the source's terms
of use must also clear first.

The other labels:

| Disposition | Meaning |
| --- | --- |
| Input only | Entrants may read it as context; it is never a scored target. Used for numbers firms decide, such as supply or inventories. |
| Separate board | May be scored, but on a leaderboard kept apart from the social-simulation ranking. |
| Next batch | A plausible Class A source; the open checks in its row come first. |
| Next domain batch | As next batch, but scored as its own domain, apart from U.S. rounds. |
| Defer | Blocked by something outside the arena's control, such as paid data, a licence or a missing archive. |
| Seasonal special | One prospective question per season rather than weekly rounds. |
| One large round or separate board | An annual release with many items counts once, not as dozens of rounds. |
| Wait for rights / terms | Usable in principle, but no round until the source's terms are cleared. |
| Wait for cadence / rule | Terms are fine; blocked only by publication frequency or by a question rule not yet adopted. |

| Candidate | Disposition | Reason |
| --- | --- | --- |
| Substack leaderboards | Wait for Class B | No declared release time; cited page returned 404. |
| Apple charts (apps/books/music) | Wait for Class B and terms | Continuous ranking with a publisher copyright notice. |
| Cloudflare Radar | Defer | CC BY-NC 4.0 needs a public benchmark use decision. |
| openFDA drug shortages | Input only | Establishment supply, not individual consumer choice. |
| NY Fed supply-chain pressure | Input only | Firm supply indicator. |
| U.S. Treasury auctions | Separate board | Bidders are mostly institutions, not simulated consumers. |
| Earnings user counts | Next batch after cadence | Quarterly publication and cross-company definition changes. |
| Nielsen The Gauge | Next batch | Recalibration and terms need a new coherent series. |
| Nielsen Streaming Top 10 | Next batch | Publication delay, candidate identity and terms not yet fixed. |
| Circana U.S. games | Defer | Underlying data is paid and digital methodology changed. |
| YouTube Shorts songs | Defer | No stable downloadable first-print archive. |
| YouGov Brand Movers | Wait for biggest-mover rule | Published on YouGov's public sites, so the same Public Data License as the crosstabs applies (CC BY-NC 4.0, manual pull). Needs the pre-lock candidate list first. |
| YouGov BrandIndex Lite | Wait for Class B and terms | Continuous dashboard; BrandIndex is a paid product outside the Public Data License, and no export is authorised. |
| YouGov Ratings | Wait for cadence | Same Public Data License as the crosstabs; scores update quarterly, below the weekly cadence. |
| Gallup Economic Confidence | Defer | Forward calendar and complete official history not established. |
| Bank of America Consumer Checkpoint | Defer | Release day and rights not fixed; PDF detail. |
| TSA checkpoint counts | Wait for Class B | Arena would define the weekly window and archive its samples. |
| Recreation.gov campsite status | Wait for Class B | Live availability, batching and instant sellout. |
| IMDb ratings/votes | Wait for Class B and terms | Daily evolving counts and noncommercial license. |
| Steam review score | Wait for Class B and terms | Release date is fixed, count window is arena-defined. |
| Steam concurrent players | Wait for Class B and terms | Live polling would define the observation window. |
| Non-U.S. confidence (EU/ANZ-Roy Morgan/Nanos) | Next domain batch | Keep populations separate; do not reuse U.S. personas. |
| Seattle Public Library checkouts | Next batch | Monthly update time and first-print archive need confirmation. |
| IRS filing statistics | Seasonal special | Published increments are largely predictable from last year's calendar. |
| CMS Marketplace snapshots | Seasonal special | Few releases and incompatible first-snapshot population. |
| NBA All-Star fan voting | Seasonal special | Few updates; lock before voting begins. |
| CDC FluVaxView | Seasonal special | Weekly increments near measurement noise; intent-versus-coverage may work once. |
| Annual events (Harris/Ad Meter/System1/Eurovision/names/GivingTuesday/Piper Sandler) | One large round or separate board | One release supplies many correlated items, not many independent rounds. |

The exploratory designs in Section 7 receive these decisions: CDC intent versus
coverage is one seasonal special; post-release Wikipedia attention is a Class B
pilot after the window is frozen; ad ratings and Eurovision votes are annual
multi-item rounds outside the U.S.-persona default; baby-name changes are one
fixed-candidate annual round; NYT versus Seattle borrowing waits for both source
rights and an aligned month. None is activated by the source-data PRs.
