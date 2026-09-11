# AAII Investor Sentiment Survey

**What the number is.** The percent of AAII members who are bullish, neutral and bearish on the stock market over the next six months, weekly since 1987. The arena scores the bull-bear spread, bullish minus bearish, in points.

**Where it is published.** [aaii.com/sentimentsurvey](https://www.aaii.com/sentimentsurvey). The voting period runs Thursday through Wednesday, the reported date is the closing Wednesday, and results publish the following Thursday.

**How the arena reads it.** [`ssa/adapters/aaii.py`](https://github.com/Social-Atoms/social-sim-arena/blob/main/ssa/adapters/aaii.py) reads the HTML results page, which serves a rolling window of about 22 weeks, and archives each fetch under `sources/aaii/`; the committed vintages extend the history a week at a time. The page's dates carry no year, so the year is inferred from the response's own date header, never the local clock. Every row checks itself: bullish plus neutral plus bearish must equal 100, and the header cells must appear in the exact order reported date, bullish, neutral, bearish before anything is read, which catches a column swap the sum cannot. The site sits behind a bot filter and refuses non-browser user agents. The full-history spreadsheet is an OLE2 file and is deliberately not parsed; this repository reads with the standard library only.

**How a question resolves.** On the results-page row for the named voting week.

**What to watch.** The rolling window clears the roughly fifteen weeks the baselines need with little margin; the archive is what makes the history longer than the page.
