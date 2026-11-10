# New York Fed Survey of Consumer Expectations

**What the number is.** The median one-year-ahead and median three-year-ahead expected inflation rates of US household heads, in percent, from the NY Fed's monthly Survey of Consumer Expectations.

**Where it is published.** [newyorkfed.org/microeconomics/sce](https://www.newyorkfed.org/microeconomics/sce), as one all-history workbook (`frbny-sce-data.xlsx`, every month since June 2013) republished in full with each monthly release, in the first ten days of the following month; the release dates are preannounced on the NY Fed calendar.

**How the arena reads it.** [`ssa/adapters/sce.py`](https://github.com/Social-Atoms/social-sim-arena/blob/main/ssa/adapters/sce.py) fetches the workbook (the site refuses generic clients, so the request carries a browser user agent) and parses it with the standard library, since an xlsx is a zip of XML. The sheet is found by its name and the two columns by their exact headers, never by position, so a reordered or renamed workbook fails loudly. Rows are dated by the reference month, first of the month. Each capture is archived write-once per day under `sources/sce/<fetch day>.xlsx`, and the data's own as-of is the newest reference month inside it. The day's first capture comes before the 11:00 ET release, so a scheduled release day also keeps `sources/sce/<day>.post.xlsx`: the first capture at or after the release (the later of the round's `release_at` and 11:00 America/New_York) whose newest month is newer than the morning file's, itself write-once. Readers take it as that day's newest vintage.

**How a question resolves.** On the named month's row in the vintage archived on the release day.

**What to watch.** Like Michigan, a month-labelled series cannot be frozen by a date filter; a question relies on the lock snapshot.

## Moving expectations (ten-cell profile)

**What the number is.** The mean percent chance that a household head will move to a different primary residence over the next 12 months, in each of ten demographic cells (age under 40, 40-60, over 60; income under $50k, $50-100k, over $100k; region West, Midwest, South, Northeast), scored together as one profile. A stated intention, not an observed move. The national mean is shown, not scored. The rules are the approved Data Card [`nyfed-sce-moving@v2`](../data-cards/nyfed-sce-moving.json) (sha256 `bd4130d2434db06cd3620aa3d6cef9e92165e08c081c0fe38b31f1f0f2faf676`) of the Urban Track (housing).

**How the arena reads it.** No request of its own: [`ssa/adapters/sce.py`](../../ssa/adapters/sce.py) `parse_moving` reads two more sheets of the workbook archived above, `Moving expectations Demo` and `Moving expectations`, by sheet name and exact header string, at full stored precision. The series (`sce_moving_*`, source key `sce_moving`) come from the newest archived vintage, after the day's capture is filed. A missing or renamed header, a non-numeric cell or a value outside 0-100 raises.

**How a question resolves.** Each cell on its month-M row in **the first archived workbook carrying month M**, on or after the release day (a release day's `<day>.post.xlsx` sorts after its morning file), not the file named for release day, whose capture predates the 11:00 ET release. No vintage carrying M by release + 7 days cancels the round; it never resolves from a later month. The reference is per-cell persistence with sd 1.5 (the arena default, measured within 0.3% of an honest spread), and it refuses to post unless every cell's history ends at month M-1. Rounds lock at release − 48 h, 11:00 America/New_York (16:00Z in standard time). Proposed rounds: [`nyfed-sce-moving.rounds.json`](../data-cards/nyfed-sce-moving.rounds.json).

**What to watch.** Three of the ten cells (age under 40, income $50-100k, Northeast) move by less than or about their own sampling noise; the question text prints every cell's noise floor. Attribution required with any publication: *Source: Survey of Consumer Expectations, © 2013-2026 Federal Reserve Bank of New York (FRBNY).* followed by the licence's availability and disclaimer sentences.
