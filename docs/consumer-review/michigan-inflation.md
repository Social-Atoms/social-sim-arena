# Michigan Inflation Expectations

**What the number is.** Median expected inflation for the next year and the next
5-10 years among the survey's US household population, in percent. This extends
the Michigan source; it is different from the existing consumer sentiment index.
Rows use the survey reference month, not a publication timestamp.

**Where it is published.** The official tables page links
`https://www.sca.isr.umich.edu/files/tbmpx1px5.csv` (monthly finals) and
`https://www.sca.isr.umich.edu/files/tbcpx1px5.csv` (current release table).
Captured finals end in September 2026; the current table adds October preliminary
values of 4.7 and 3.5. The 1-year history begins January 1978. The long-horizon
history is sparse in earlier years; missing months are explicitly recorded.

**How the arena reads it.** The offline parser checks the named final columns,
current-table headings, dates, duplicates, finite values, and preliminary marker.
The bridge appends a current preliminary only if a final for that month is absent.
An immutable snapshot retains both original files independently. Existing
Michigan retrieval/rights evidence should be reused and checked for these exact
files; this change does not issue a new approval.

**How a question resolves.** Proposed: resolve a preliminary question from its
captured preliminary and a final question from its named final release. Timeline:
declared deadline and history freeze, survey release, acquisition/archive,
resolution. Survey-month labels alone cannot freeze history; the actual official
release calendar and a lock snapshot are required. Current-vintage history is not
evidence of past preliminary prints.

**What to watch.** Check historical preliminary availability, revisions,
release-calendar/timezone evidence, survey wording/population, signal on a
consistent final-only window, and candidate acceptance. Parser failures retain
the previous validated vintage with an explicit failure state. Collection is
local and on demand; operational owners and production frequency await review.
