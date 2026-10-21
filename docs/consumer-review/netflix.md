# Netflix Top 10

**What the number is.** Proposed targets: the US weekly film and TV top-ten
rankings. The raw country export supplies ranks, show titles, and season titles;
it does not justify country-level views. The population is Netflix viewing in the
named country. Global metrics are not substituted for US metrics.

**Where it is published.** Official downloadable file:
`https://www.netflix.com/tudum/top10/data/all-weeks-countries.tsv`.
The acquired file contains 277 complete US film lists and 277 complete US TV
lists, from week ending 2021-07-04 to 2026-10-18. The research's publication
contract is Tuesday following a Monday-Sunday measurement week; confirm exact
publication time before scheduling.

**How the arena reads it.** The offline TSV parser requires exact field names,
filters US explicitly, validates Sunday labels and full ranks 1-10, and refuses
duplicates or missing titles. A JSON pair `[show_title, season_title]` is used as
a collision-resistant local key because this export lacks stable title IDs.
Raw and normalized snapshots are immutable and local.

**How a question resolves.** Proposed ranking bridge only. Freeze submissions
and entrant-visible history before Monday's measurement starts, then capture the
Tuesday weekly release and resolve from that pinned artifact. Daily charts can
leak the result if deadlines merely precede publication by 48 hours. New shows
and seasons require a candidate policy decided before outcomes are read.

**What to watch.** Terms, title/season renames, tied/changed ranks, publisher
methodology changes, missing weeks, candidate sets, and redistribution need
review. Today's all-history export is not the original publication vintage for
every past week. Initial TLS failures were resolved using the system trust store;
no TLS bypass was used. Acquisition is local/on demand, with production owners
and cadence pending.
