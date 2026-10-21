# Decision Memo

Status: concrete proposals for organizer review. These recommendations are
implemented in the source-evaluation documents and are not formal task activation.
Existing source gates and the Pairwise main board remain authoritative.

| Decision | Current constraint | Evidence and gap | Proposed position | Decision owner |
| --- | --- | --- | --- | --- |
| Reopen ACSI | Inventory rejected it with quarterly shorthand | Free company scores verified; true company cadence, first prints and forward dates still need work | Reopen evaluation; retain existing nonapproved state until a recorded decision | Organizers and research team |
| Cadence exceptions | Monthly scalar sources fit the existing gate better than quarterly/annual values | Many items in one release are not independent releases | Evaluate one profile/round per annual event; avoid mechanically multiplying its main-board weight | Research team / PI |
| Non-US populations | Current persona panel models US adults | Main-source country choices remain US; broader-country instruments/weights untested | Separate domain/display proposal; do not apply the US persona panel indiscriminately | Research team / PI |
| Supply side | Ordinary establishment outputs are outside the citizen target | Mint has unresolved buyer mix, replenishment, net corrections and limits | Assess ordinary firm supply as input; target exceptions require evidence and explicit approval | Organizers / PI |
| Continuous streams | No reviewed Class B technical protocol established here | Issue #17 is a taxonomy discussion, not that protocol | Fix sampling/window/freeze/missing/late/revision rules prospectively before adding streams | Maintainers and research team |
| Prediction-market baseline | Not required for collection or the existing board | Kalshi's [Data Terms](https://kalshi-public-docs.s3.amazonaws.com/kalshi-data-terms-of-service.pdf) limit use to personal/noncommercial website access and require prior written consent for software development or sharing archived/cached datasets | Do not implement a Kalshi baseline in this repository under current terms; leave the Pairwise score unchanged | Research team |
| New YouGov products | Crosstab position is product- and retrieval-specific | No blanket authorization for Brand Movers/ Ratings or scheduled extraction | Audit retrieval, use and redistribution independently; keep unrelated products out of this collection | Maintainers and rights reviewer |

## Ranking Contract

The acquired rankings retain actual publisher identities, country and period.
They do not select candidates after observing the outcome. Review must decide
between open lists and a prospective candidate universe, define lock-known
releases versus unforeseen new entities, choose loss/missing-answer treatment,
and show actual example comparisons. A fixed list of 20-30 candidates remains
a proposal. Steam includes more than just paid base games; Netflix identifies
seasons separately; NYT editions require a book/version decision.

Weak persistence, low signal, and a denominator close to zero are different
phenomena. This collection reports raw diagnostics rather than importing the
research PDF's old skill-normalization argument into the current Pairwise board.

## Approval Record

All seven decisions are pending. No external review request or issue comment was
sent, no organizer response was invented, and no source state was changed to
approved. Reviewers should record dates, names, scope and conditions before a
formal activation PR is prepared.

The recommended positions are: reopen ACSI evaluation but defer activation until
forward-calendar, first-print and rights evidence is complete; permit one annual
multi-item round or a separate board without multiplying main-board weight; use
separate non-US domains and skip US persona weighting where inappropriate; treat
firm supply as input or a separate board and use Mint only after fixed-supply
checks; require a Class B protocol before TSA or Steam player streams; decline
a Kalshi baseline under the current published data terms; and
audit Brand Movers/Ratings separately from YouGov crosstabs.
