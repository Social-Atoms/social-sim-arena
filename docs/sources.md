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
first print** and ignores every later revision. Say which it is.

**Retroactive rescaling.** Google Trends returns *relative* values that change
when the query window changes, so the same request answers differently at
different times. That is not a revision policy to work around; it is
unfixable, and it is why that source is rejected.

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
81 published YouGov waves, **ten of seventeen subgroup series carried no weekly
signal at all**. That is why crosstab rounds are monthly on a four-wave average.

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
