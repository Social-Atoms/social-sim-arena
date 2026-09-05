# Adding a data source

Every published number must answer three questions: **which URL was it fetched
from, when, and where is the saved raw response.** This file is the checklist a
new source has to pass, and the rules are not style preferences — each one is
here because something broke.

---

## 1. The four requirements

A candidate is not a source until all four hold. Three of the four are about the
publisher, not about us, so they are settled before any code is written.

### 1.1 A third party publishes on a schedule announced in advance

This is the whole contamination argument. Forecasts lock at `release − 48h`, and
"the answer does not exist yet at lock time" is only true if a third party has
committed to a release moment we did not choose.

A continuously-updated stream does not qualify on its own. We can still score
one, but then **we are both the question setter and the resolver**, and nothing
outside this repository vouches for why that metric and that window. If a source
is in that class, say so explicitly in its round definition rather than letting
it read like Michigan or YouGov, and commit the metric definition with a
timestamp *before* the window opens.

**Record the schedule precisely**: day, time, and timezone, plus what happens on
holidays. "Monthly" is not a schedule. "Last Tuesday of the month, 10:00 ET" is.
Link the publisher's forward calendar if one exists.

### 1.2 It is addressable as of a past date, or it can never be backtested

Two different failures hide here.

**Revision.** If the publisher rewrites history, a resolution computed last week
cannot be reproduced today. Conference Board CCI revises the prior month in
every release (June 2026 printed 91.2 and later showed 92.2); Census MARTS
revises the advance figure by about ±0.1pp and then rewrites years of it at the
annual benchmark. Both are usable — **but only if the resolution rule pins the
first print** and ignores every later revision. Say which it is. §8 measures
what that looks like on MARTS across 36 releases.

**Retroactive rescaling.** Google Trends returns *relative* values that change
when the query window changes, so the same request answers differently at
different times. That is not a revision policy to work around.

This paragraph used to end "and it is why that source is rejected", and that
was overturned the same way §6 was. The rescaling is unfixable *at the
endpoint*, and it is answered *at the archive*: `ssa/adapters/trends.py` fixes
the request window as a module constant and writes every fetch to `trends/`, so
a completed week's value is whatever the earliest snapshot containing it
showed, forever. The resolution source is the archive, not the endpoint, which
is the same move `civiqs` makes against nightly re-modelling. Delete the
archive and the rejection is correct again — which is why the archive is
committed and why nothing in the pipeline resolves a Trends round from a live
request.

Ask the publisher's own documentation, then verify by fetching two vintages.

### 1.3 Free, and free without a key if possible

A key is acceptable (it becomes a repository secret) but it is a dependency to
be justified. A subscription with a redistribution clause is not: we commit the
raw bodies, and that would be a licence violation as well as a fragility.

### 1.4 It moves enough to be scored

**This is the one that gets skipped, and it is the one that puts unscoreable
rounds on the board.**

The arena score is

    100 x (persistence − entrant) / (persistence − noise/sqrt(pi))

The denominator is the persistence error. A series that barely moves drives it
toward zero, so any entrant error however small becomes a huge negative score
and the ranking is noise. It is not that we care about movement for its own
sake — it is that the metric divides by it, and more deeply, **a value that is
essentially known at lock time cannot distinguish forecasters on any metric.**

So measure before registering:

```python
from ssa import scoring
noise, drift = scoring.noise_floor(values)     # measurement noise vs real movement
```

`ssa/crosstab.py` has already recorded what happens when this is skipped: over
83 published YouGov waves, **ten of sixteen subgroup cells carry no weekly
signal at all**. Those cells are still asked -- the profile is scored jointly
and a season of weekly rounds is where sampling noise averages out of a score
-- but each cell's noise and movement are quoted to the entrant in its
methodology text, and `crosstab.noise_by_cell` is what to publish beside any
board on them. (The family was monthly on a four-wave average for one round;
`ssa/crosstab.py` says why that did not survive: the lock sat after three of
the four waves were public.)

And know when `noise_floor` is the wrong tool. It returns ~0 for every Civiqs
tracker, because Civiqs publishes a smoothed model fit rather than a survey wave
and there is no sampling noise in the published series to find. For a smoothed
source, gate on the **persistence error** directly. `civiqs_angry_share` passed
at 0.31 points a week; five of the other nine emotions in the same tracker moved
0.03 to 0.10 over ranges under eight points and were left out.

---

## 2. What the adapter must do

Copy `ssa/adapters/newsdigest.py` or `ssa/adapters/civiqs.py`. Both carry the
reasoning in their module docstrings; read one before writing a new one.

### 2.1 Separate fetching from parsing

```python
def fetch_text(...):  ...   # the body exactly as served
def parse(text):      ...   # -> [{"date": ..., "value": ...}]
```

`ssa/provenance.py` archives what arrived. **A vintage rebuilt from parsed rows
is our reading of the file, not the file**, and the difference is exactly what
an audit is checking. Every adapter here now has this split;
`silverbulletin.fetch()` remains as a convenience that composes the two.

### 2.2 Address by timestamp, never by "now"

`newsdigest` fetches Wikipedia by revision id as of the lock, not the page as it
reads today. `civiqs` archives a dated snapshot because the tracker rewrites its
own daily history nightly. If a source cannot be addressed as of a past moment,
§1.2 applies.

### 2.3 Archive on first fetch, and serve the archive after

A rerun must cost no requests and a reader must be able to regenerate any prompt
or resolution from the repository. This is also the rate-limit answer: Civiqs
returns `SSLZeroReturnError` on bursts, Wikipedia returns 429.

### 2.4 Fail loud on empty

`build_trackers` raises rather than publish a plausible-looking zero, and
`build_all` raises when any series builds to zero points. Match that. A source
that silently returns nothing looks exactly like a quiet week, which is the
failure mode that costs the most to discover late.

### 2.5 Retry a dropped connection, not an HTTP error

A reset connection is a flake; a 404 means the file moved and repeating the
request will not change that. Measured: a first full pass over the news archive
lost **67 of 589 days** to `ConnectionResetError` and TLS EOF and none to
anything the server meant to say. The same pass with one retry lost one.

---

## 3. What the registry entry must carry

One row in `ssa/series.py`, and nothing else:

| field | why |
|---|---|
| `label`, `tracker`, `source` | which adapter, which upstream |
| `unit` | a number without a unit cannot be scored against anything |
| `cadence` | **and which reading is scored.** `civiqs_net_approval` says "daily model output, read and archived every day; the series scored here is the Friday reading" |
| `question` | the exact quantity, in words: which denominator, which answer options, net or share, which population |
| `methodology` | what the entrant is entitled to know. **Anything the resolution uses and the prompt omits is a question the entrant cannot see but is graded on** |
| `survey` | the real instrument, verbatim, if the persona arm can run on it. **Leave it out rather than paraphrase** — `series.survey()` returning None makes that arm refuse the series by name, which is the honest outcome |

The `question` and `methodology` text is not documentation; `harness.build_prompt`
puts it in front of the model.

---

## 4. Provenance, concretely

`ssa/provenance.py` records every upstream body:

```
sources/manifest.json            url, fetched_at, sha256, bytes, changed_at
sources/<name>/<YYYY-MM-DD>.csv  the body, as it arrived
```

Three things worth knowing before you touch it:

**Bodies are stored uncompressed on purpose.** These files are append-mostly — a
day adds a handful of rows to five thousand. Git delta-compresses similar text
blobs, so a daily vintage of a 1.5 MB CSV costs a few kilobytes in the pack.
Gzipping first would look thriftier in `ls` and defeat delta compression
entirely.

**A day is the unit, not a run.** The refresh runs every six hours; four
identical vintages of one file are three files of noise.

**`changed_at` is separate from `fetched_at`.** "When did we last confirm this"
and "when did the content last move" are different questions, and a stale
upstream is the failure that hides — the pipeline keeps running, the site keeps
rendering, the numbers quietly stop. `provenance.unchanged_since()` is what
makes that visible.

`site/data.json` then carries `sources` (one block per upstream) and
`series_provenance` (which upstream each series came from), so a page can say
"this figure came from that file, fetched at that time" instead of crediting a
brand.

---

## 5. The failure this is all guarding against

**Twenty-one of the twenty-four registered series arrive through one published
Google Sheet.** If it moves, or its share link is revoked, or a column is
renamed, seven eighths of the arena goes dark at once — and it presents as "no
new polls this week", not as an outage.

Two consequences for anything added from here:

- Prefer a source with its own path over one more series behind the same pipe.
  Adding an eighteenth series to that sheet adds releases and no independence.
- Where a pollster publishes cleanly on its own site, a **direct adapter as a
  redundancy check** is worth more than the series it duplicates: it alerts when
  the two disagree. `series.MICHIGAN_SOURCE` is the pattern — it records which
  of two sources actually answered, so the site credits the one that replied.

---

## 6. A worked rejection, and its reversal: Michigan sentiment by party

**Status: built.** `ssa/adapters/umichparty.py`, series `umich_party_dem` /
`_ind` / `_rep`, first rounds `umich-party-2026-09-{dem,ind,rep}`.

This section was written on 2026-08-17 as a rejection — *not viable without a
new dependency, and two readings stale even with one* — and it was wrong. It is
kept in full below, because the survey of what does **not** work here cost real
hours and is still the map anyone extending this should read. What follows is
the correction; the original verdict starts at "The target".

**What reopened it (2026-08-18, Route A).** Step 5 below establishes that the
one timely party artifact is a PDF and then stops, treating "PDF" as a synonym
for "unparseable". It is not. `pdftotext -layout` — poppler, a *system* package
(`apt-get install poppler-utils`, one line in `refresh.yml`), not a Python
dependency this repository has to carry — renders that document as a
fixed-column table:

```
DATE OF SURVEY              Dem      Ind     Rep    ...
August        2026       39.1    48.5   78.7        ...
```

A regex of `MonthName YYYY` plus nine floats reads all 156 rows, 1980-06 through
2026-08. So the argument in step 3 — "parsing it means a new dependency, which
this repository does not take for one series" — is answered on its own terms:
shelling out to a system binary is the same contract `ssa/stamps.py` already has
with `ots`, and no `requirements.txt` line was added.

That also disposes of the staleness objection in step 4 and in the closing
paragraph. The blocker there was the free **Table 5b**, which runs about two
releases behind; the addenda PDF is stamped with the preliminary's own release
day and carries the preliminary row. Nothing about it is stale, so the frozen
lock history is not missing anything an entrant can read.

**What did not change: recurrence.** Step 5's last sentence still stands and is
the live risk. August 2026 is the first month this addenda has ever appeared,
and the URL is an opaque docid (`fetchdoc.php?docid=81624`) that gives no way to
derive September's. The adapter is therefore built for absence: it parses the
newest PDF committed under `sources/umichparty/` and **never fetches on its
own**, so a month with no addenda leaves the series where it is instead of
breaking a refresh. Pointing it at a new month is a maintainer running
`fetch_latest(docid=...)` — manual until the publication recurs often enough to
show a pattern worth automating.

**One correction to the probe that opened the route.** It reported 153 rows and
two holes in the modern run (2019-11, 2023-06). There are 156 rows and no holes
since 2017-02. `pdftotext` separates pages with a form feed, `^` under
`re.MULTILINE` does not match after one, and the three rows that sit at the top
of a page were invisible to a whole-text scan — which reported them as months
the survey never asked about. The adapter reads line by line and raises on any
line that opens like a data row and fails to parse, so the same event is now an
exception rather than a quiet gap.

---

*The original 2026-08-17 rejection follows, unedited.*

**The target.** The ICS among Democrats / Republicans / Independents
(`umich_sentiment_dem` / `_rep` / `_ind`), monthly since February 2017,
sporadic before that back to June 1980. The structure is exactly what a
social simulation should know and a persistence null cannot: the partisan gap
flips sign at presidential transitions. October 2016 read Dem 102.1 /
Rep 74.4; February 2017, Dem 77.5 / Rep 115.7. October 2024 read Dem 91.4 /
Rep 53.6; December 2024, Dem 69.6 / Rep 85.4. A forty-point swap inside two
months, twice, on a known calendar.

**What was tried, in order:**

1. **The subset tool** (`data.sca.isr.umich.edu/subset/subset.php`; the form
   POSTs to `/subset/output.php` and does emit CSV). Its demographic
   checkboxes are age, region, sex, income, education. There is no party
   field anywhere in the form — the tool cannot express the cut at all.

2. **The demographic tables** (`/demographic-tables.php`) offer age, income,
   education, region, gender. `?demographic=political+party` and
   `?demographic=party` return the default page byte-for-byte. The party
   table lives instead on the all-households page (`/tables.php`) as
   **Table 5b**, "The Index of Consumer Sentiment, Current, and Expected
   Within Political Party".

3. **Table 5b's formats.** PDF and `.xls` only, behind HMAC-keyed URLs
   (`get-table.php?c=RB&y=2026&m=6&n=5b&f=xls&k=<hex>`). The `.xls` is a
   genuine BIFF Composite Document (checked by magic bytes), not an HTML
   table wearing the extension; the key covers `f`, so substituting `f=csv`
   returns `Not Found`; no table on the page offers CSV at all. Parsing it
   means a new dependency, which this repository does not take for one
   series.

4. **And the free Table 5b is stale.** On 2026-08-17 the public tables page
   is headed "Monthly: June 2026"; the Historical edition of 5b ends at
   June 2026 (read from its PDF twin) and its `.xls` was last saved
   2026-06-24 — while the August preliminary, released 2026-08-14, was
   already public. The site has a sponsor login; the free page runs about
   two releases behind it.

5. **The only timely party artifact is a PDF.** "Tables Addenda of Political
   Party Variable", `data.sca.isr.umich.edu/fetchdoc.php?docid=81624` →
   `demopoliticalparty202608p.pdf`, stamped 8/14/2026 — the preliminary's
   release day — carrying the full history through the August 2026
   preliminary row. So the party cut **does publish on the preliminary
   schedule**, which answers the round-calendar question if a route ever
   opens: party rounds could share the national prelim/final calendar, with
   the prelim row revising at the final exactly as `resolve`'s (date, value)
   keying already handles. But it is a PDF; docids 81618–81631 were scanned
   and no CSV/XLS twin exists (81623 is the stock-ownership addenda, also
   PDF); and August 2026 is the first month it has ever appeared —
   `reports.php?year=2025` lists no addenda at all — one data point of a
   publishing habit, not a schedule.

6. **Everything else checked, and empty.** The CSV time-series archive
   (`/data-archive/mine.php`) exports Tables 1–47 only; 5b is not among its
   options. The party charts (chart 5b on `/charts.php`; a one-off
   `get-special-chart.php?n=75085`) are keyed PDF/XLS at the same June
   vintage. `www.sca.isr.umich.edu/files/` — where the national `tbmics.csv`
   lives — holds exactly the six national tables as CSV and no party file.
   `data.sca.isr.umich.edu/files/` answers HTTP 200 with the site homepage
   for *any* path, so nothing can be discovered or verified there. FRED
   carries no party subseries.

Why the near-miss fails even if an `.xls` reader were allowed: at any lock
the frozen history would end two readings before what every entrant can read
in the addenda PDF. The persistence null would be missing public information
— the same silent staleness that mis-resolved `umich-2026-08-prelim`, as
§1.2 and `ssa/adapters/umich.py` already record. A source that is public but
unparseable to us is contamination in one direction only.

**Revisit when** the addenda gains a non-PDF twin, or 5b joins the CSV tables
(`tbmics.csv` proves they publish CSV when they choose to), or a sponsor
arrangement makes the current `.xls` worth a dependency argument. Until then
this stays unbuilt. A hand-keyed resolution would make the maintainer the
resolver, which is what §1.1 exists to prevent — and a scraper presented as
sturdier than it is would fail in the quietest possible way, mid-season.

*(End of the original rejection. The dependency argument in step 3 was the load-
bearing one and it did not survive: a system binary is not a Python dependency.
The last sentence above is still the standing instruction for this source — it
is why `umichparty.fetch_latest` refuses a non-PDF body outright and why nothing
in the pipeline fetches it unasked.)*

---

## 7. The inventory: where a source's state is written down

`ssa/inventory.py` holds one row per source — every adapter in this
repository, every `source` key `ssa/series.py` registers, and every candidate
that was surveyed and turned down — carrying:

| field | what it answers |
|---|---|
| `state` | `integrated`, `permission-needed`, or `rejected` — this repository's relationship to the source |
| `rights` | the publisher's terms: `approved`, `approved-no-redistribution`, `permission-needed`, `rejected`, `unresolved`. **Only the two approved verdicts generate rounds** (`inventory.GENERATING_RIGHTS`); see §9 for why the second exists |
| `role` | `target` (we ask questions about it) or `input` (an entrant reads it before answering). An input never becomes a round |
| `evidence` | the robots rule, the licence sentence, the HTTP status, or the issue number. A label is not evidence |
| `revisit` | for anything not integrated: the specific fact that would reopen it |

**Why it is code and not this page.** The rights decision used to be written
twice — narrated here, and typed as a `RIGHTS` dict inside
`tools/generate_rounds.py` that the generation gate actually read. Nothing kept
them in step and they had already drifted: `trends_basket` was missing from the
dict, so the gate called it `unresolved` and refused to schedule anything on it
while three hand-written Trends basket rounds ran live in
`questions/season0.json`. The generator now reads
`inventory.rights_table()`, `tests/test_inventory.py` fails if a registered
source has no row, and this page points at that file rather than restating it.

`state` and `rights` are deliberately separate. A source can be rights-approved
and not integrated — approval says nothing about whether anyone wrote the
template. And several sources are integrated while their rights sit at
`permission-needed`: they are read from an archive a maintainer fetched by
hand, which is exactly why the generator must not schedule new rounds on them.

**A `rejected` row is not a closed door.** §6 above is a rejection that was
reversed once somebody re-read its load-bearing argument, and the reversal was
worth more than the original verdict. That is only possible when the verdict
says what would change it, so every non-integrated row carries `revisit` and a
test enforces it.

---

## 8. A no-go that passed every gate: Census MARTS retail

**Status: measured, and declined.** `tools/probe_marts.py`,
`sources/marts/probe.json`, `tests/test_marts_probe.py`.

This is the opposite shape from §6. There the source was written off on an
argument that did not survive re-reading; here every mechanical test passes and
the answer is still no. Both are worth writing down, because "we checked and it
works, and we are not doing it" is a verdict that decays into "nobody looked"
within about two months unless the measurements are on disk.

**What was measured**, over the 36 monthly advance releases from 2023-07 to
2026-06, all fetched from census.gov and each recorded in the fixture with its
URL, sha256 and byte length:

| gate | result |
|---|---|
| §1.1 schedule | 8:30 a.m. ET, on a forward calendar Census publishes at [`retail/release_schedule.html`](https://www.census.gov/retail/release_schedule.html). **Not** a fixed day of the month — the 2025 shutdown moved it and the calendar has been catching up since |
| §1.2 first print | the advance value differs from the next release's print for the same reference month in **280/280** (category, month) pairs, mean 0.52% of the advance. Two mechanisms: real source revision, and concurrent seasonal adjustment, which moves every SA value every month regardless. So pinning the first print is not only implementable, it is unavoidable |
| §1.3 free | public domain, US government work, no key |
| §1.4 movement | mean absolute month-over-month change of the advance print runs 0.48% (food & beverage) to 2.47% (gasoline stations) |
| history | eight three-digit categories, present in 36/36 releases with an advance value in 36/36, one label spelling each |

The archive is addressable by **reference month**, which is what makes a
resolution rule possible at all:

```
https://www2.census.gov/retail/releases/historical/marts/rs{YY}{MM}.xlsx
```

`{YY}{MM}` is the advance month the release is about, not the day it was
published, so a round can name the artifact that will settle it before that
artifact exists. Nothing on any Census retail page links to that directory; it
was found by walking `www2.census.gov/retail/`.

**Why it is still a no.** MARTS surveys **businesses** about their sales
receipts. A simulated citizen structurally cannot answer it — that is the
objection issue #36 raised when the retail series were first considered for the
market-research board, and no amount of clean plumbing addresses it. Scoring
it would measure macroeconomic nowcasting, which is a different benchmark from
the one this arena runs, and issue #48 lists redesigning the taxonomy as a
non-goal. **This is a maintainer's call, not a finding**, and it is the only
thing standing between the fixture above and a shipped round family.

**Three traps recorded for whoever picks it up**, because each one fails
quietly:

- **Two of the 54 archived workbooks are strict OOXML** (`rs2501`, `rs2601`),
  in the `purl.oclc.org` namespaces rather than the usual
  `schemas.openxmlformats.org` ones. openpyxl 3.1.5 returns **zero sheets** for
  those and raises nothing. `probe_marts` reads the namespace off the
  document's own root element, which is also why it needs no new dependency.
- **Three category rows wrap across two spreadsheet rows** (444, 448, 451): the
  NAICS code on one, every number on the next. A row-at-a-time parser drops
  exactly the categories a retail question would ask about.
- **The archive backfills late.** `rs2607.xlsx` was still 404 seventeen days
  after the July 2026 release, and `rs2605`/`rs2606` posted 28 and 29 days
  after theirs. A resolver has to snapshot `marts_current.xlsx` on release day
  and cite the permanent `rs{YY}{MM}.xlsx` once it appears.

The workbooks themselves are not committed. §2.1 is right that a vintage
rebuilt from parsed rows is our reading of the file rather than the file, so
the fixture carries each body's sha256 and `probe_marts.py --verify`
re-downloads and fails if any of them has moved. 1.2 MB of binaries is
provenance for a series, and there is no series.

---

## 9. A worked withdrawal: two sources whose terms bar the use itself

§6 is a rejection that was reversed by re-reading its own argument. This is the
opposite case, recorded because the reasoning is the part worth reusing: on
2026-09-03 all five sources that had been sitting at `permission-needed` were
decided at once (issue #68), and the question that split them was **not** "are
we allowed to publish this" but **"what exactly does the clause restrict."**

Three of the five restrict *passing the data on*. That is a condition this
repository can meet and keep meeting, so they were approved on it:

| source | the operative clause | why it is satisfied |
|---|---|---|
| AAII | "No part of the contents ... may be copied or **forwarded to anyone else**" | silent on private analysis and on automated access; the bodies stay unpublished |
| Michigan, party cut | data "may be displayed, reformatted, and printed for **your organization's use**"; written consent is reserved for "reproduce, retransmit, distribute, sell, publish, or broadcast" | organizational use is granted outright — this row had previously been read as needing consent for *any* use, which blocked three series for a restriction the agreement does not make |
| YouGov crosstabs | CC BY-NC 4.0, naming "academic research"; plus a bar on bots and on training AI | the fetch is a deliberate manual act, not a refresh side effect; and evaluating a finished model against a published number is not training, fine-tuning or developing one — a maintainer position, recorded as one |

They carry `rights: approved-no-redistribution`, and the condition is enforced
rather than promised: `inventory.PUBLISH_BLOCKLIST` names each archive
directory and `tests/test_inventory.py` fails if a row carries that verdict
without a blocklist entry, or if any of those bodies turns up under `site/`.

**Two restrict the use itself, and no amount of not-publishing helps.**

- **The Conference Board** bars "extract for use in a database" as its own
  prohibited act, in a list alongside reproduce and distribute. Building the
  series *is* the named act; keeping the result private does not avoid it. The
  member licence that does permit a copy is for "personal, noncommercial
  purposes" by employees of member organisations, which is not a public
  benchmark.
- **Penta-CivicScience** bars "any text or data mining or web scraping",
  names "any 'robot', 'bot', 'spider', 'scraper' ... to access, obtain, copy,
  monitor or republish", and separately bars "any automated analytical
  technique aimed at analysing text and data in digital form to generate
  information which includes ... patterns, trends and correlations". The
  adapter fetched `/wp-json/wp/v2/posts` on a schedule and the pipeline
  computed trends from it. Three clauses, each sufficient on its own.

Both were withdrawn the same day: the series left `ssa/series.py`, the four
rounds left `questions/season0.json` (two of them already resolved and scored,
which is a real cost and was accepted rather than argued away), the 24 archived
Conference Board bodies left `sources/`, and both adapters, their tests, the
CCI backfill tool and its workflow left with them. `ssa/inventory.py` keeps
both rows at `rights: rejected` with the clauses quoted and a `revisit` naming
the written permission that would reopen each. A reader who notices that the
most famous US consumer-confidence index is missing should find that row rather
than assume an oversight.

**The transferable rule.** A terms-of-use clause is not one thing. Sort it into
*retrieval* (how you may fetch), *use* (what you may compute), and
*redistribution* (what you may pass on) before deciding, because a source can
be fully open on two of those and closed on the third — and only the third is a
condition a repository can hold itself to. Reading "the terms are restrictive"
as a single verdict is what left three usable sources blocked and two
unusable ones scheduled.
