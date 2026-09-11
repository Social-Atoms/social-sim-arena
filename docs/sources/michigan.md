# University of Michigan Surveys of Consumers

**What the number is.** The Index of Consumer Sentiment (ICS), a monthly index of US adults, published twice a month: a preliminary print mid-month and the final at month end. Sentiment by party is the same index split three ways, Democrats, Independents and Republicans, from the survey's party addenda.

**Where it is published.** [sca.isr.umich.edu](https://www.sca.isr.umich.edu/), at 10:00 ET on release days. Two files carry the index: `tbmics.csv`, the long history of finals back to 1952, and `tbcics.csv`, the table printed on the release itself, which is the only place the preliminary appears. The party split is a five-page PDF addendum ("Tables Addenda of Political Party Variable").

**How the arena reads it.** [`ssa/adapters/umich.py`](https://github.com/Social-Atoms/social-sim-arena/blob/main/ssa/adapters/umich.py) reads both tables from the survey's own site and nothing else. There is no FRED fallback, on purpose: FRED carries the series a month behind at Michigan's request, and on the one run where the official table was briefly unreachable the fallback answered with a history ending a month early, which is how the arena's first live resolution went wrong (the August 2026 preliminary question was resolved against July's final, a number public before the lock). A silently stale source is worse than none, so the module now raises. [`ssa/adapters/umichparty.py`](https://github.com/Social-Atoms/social-sim-arena/blob/main/ssa/adapters/umichparty.py) converts the addenda PDF with `pdftotext` and parses one row per surveyed month, an unbroken monthly run since February 2017; a month the survey did not ask the party question stays a gap. Rows are dated by the reference month, first of the month.

**How a question resolves.** On the official value published on the site at 10:00 ET for the named print (preliminary or final); the party question on the addenda PDF published with that print.

**What to watch.** A label-dated monthly series cannot be frozen by a date filter, so a question on it relies on the lock snapshot of the history the entrants were handed.
