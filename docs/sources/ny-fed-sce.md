# New York Fed Survey of Consumer Expectations

**What the number is.** The median one-year-ahead and median three-year-ahead expected inflation rates of US household heads, in percent, from the NY Fed's monthly Survey of Consumer Expectations.

**Where it is published.** [newyorkfed.org/microeconomics/sce](https://www.newyorkfed.org/microeconomics/sce), as one all-history workbook (`frbny-sce-data.xlsx`, every month since June 2013) republished in full with each monthly release, in the first ten days of the following month; the release dates are preannounced on the NY Fed calendar.

**How the arena reads it.** [`ssa/adapters/sce.py`](https://github.com/Social-Atoms/social-sim-arena/blob/main/ssa/adapters/sce.py) fetches the workbook (the site refuses generic clients, so the request carries a browser user agent) and parses it with the standard library, since an xlsx is a zip of XML. The sheet is found by its name and the two columns by their exact headers, never by position, so a reordered or renamed workbook fails loudly. Rows are dated by the reference month, first of the month. Each capture is archived write-once per day under `sources/sce/<fetch day>.xlsx`, and the data's own as-of is the newest reference month inside it.

**How a question resolves.** On the named month's row in the vintage archived on the release day.

**What to watch.** Like Michigan, a month-labelled series cannot be frozen by a date filter; a question relies on the lock snapshot.
