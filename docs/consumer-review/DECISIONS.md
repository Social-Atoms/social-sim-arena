# Decision Memo

Working agreement updated from the team discussion on 2026-11-02. These design
choices are reflected in `docs/sources.md` and the consumer pipeline. Source
admission still uses the existing inventory and season-review gates.

| Decision | Current constraint | Evidence and gap | Proposed position | Decision owner |
| --- | --- | --- | --- | --- |
| Reopen ACSI | Inventory rejected it with quarterly shorthand | Free company scores verified; true company cadence, first prints and forward dates still need work | Reopen evaluation; retain existing nonapproved state until a recorded decision | Organizers and research team |
| Cadence exceptions | Monthly scalar sources fit the existing gate better than quarterly/annual values | Many items in one release are not independent releases | Evaluate one profile/round per annual event; avoid mechanically multiplying its main-board weight | Research team / PI |
| Non-US populations | Current persona panel models US adults | Country and population must be explicit | Use existing subject domains and standings; do not apply US persona weights to other populations | Research team / PI |
| Supply side | Ordinary establishment outputs are outside the citizen target | Mint has unresolved buyer mix, replenishment, net corrections and limits | Assess ordinary firm supply as input; target exceptions require evidence and explicit approval | Organizers / PI |
| Continuous streams | No reviewed Class B technical protocol established here | Issue #17 is a taxonomy discussion, not that protocol | Fix sampling/window/freeze/missing/late/revision rules prospectively before adding streams | Maintainers and research team |
| Prediction-market baseline | Not required for collection or the existing board | Kalshi's [Data Terms](https://kalshi-public-docs.s3.amazonaws.com/kalshi-data-terms-of-service.pdf) limit use to personal/noncommercial website access and require prior written consent for software development or sharing archived/cached datasets | Do not implement a Kalshi baseline in this repository under current terms; leave the Pairwise score unchanged | Research team |
| New YouGov products | Crosstab position is product- and retrieval-specific | No blanket authorization for Brand Movers/ Ratings or scheduled extraction | Audit retrieval, use and redistribution independently; keep unrelated products out of this collection | Maintainers and rights reviewer |

## Ranking Contract

Forecast the next list's members and rank order. New entries are eligible, even
when absent from every past list. The pipeline uses open-list RBO with parameters
fixed before the measurement starts. It does not ask which prior entries drop
out, or restrict answers to a 20-30 item candidate roster. Steam keeps typed
app/package/bundle IDs, including hardware and DLC; NYT uses primary ISBN-13
edition identities. Netflix remains inactive without written permission.

Weak persistence, low signal, and a denominator close to zero are different
phenomena. This collection reports raw diagnostics rather than importing the
research PDF's old skill-normalization argument into the current Pairwise board.

## Approval Record

This is the implementation of the reported working agreement, submitted for PR
review. No rights field was changed to approved and no live question was added.
The three permission emails are drafted for review and have not been sent.

The recommended positions are: reopen ACSI evaluation but defer activation until
forward-calendar, first-print and rights evidence is complete; permit one annual
multi-item round without multiplying main-board weight; use
existing standings for non-US populations and skip US persona weighting where inappropriate; treat
firm supply as input or a separate board and use Mint only after fixed-supply
checks; require a Class B protocol before TSA or Steam player streams; decline
a Kalshi baseline under the current published data terms; and
audit Brand Movers/Ratings separately from YouGov crosstabs.
