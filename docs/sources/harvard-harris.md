# Harvard CAPS / Harris Poll

**What the number is.** The share of respondents who strongly or somewhat approve of the President's job performance, from the monthly Harvard CAPS / Harris Poll. The survey filters on registered voters and weights to the US adult population; the scored number is the approve net among those respondents as published. Percent.

**Where it is published.** [harvardharrispoll.com](https://harvardharrispoll.com/), one page per poll with a topline, key results and crosstabs as PDFs. Roughly monthly, with no release calendar: months are skipped without notice (no June 2026 poll), and neither the page slugs nor the PDF names follow a pattern.

**How the arena reads it.** The archive is the source of truth. A maintainer passes a topline URL to `hhpoll.fetch`, which converts the PDF with `pdftotext`, finds the one table headed by the question code `M3ALT`, and reads the approve, disapprove and not-sure nets. The file is archived under `sources/hhpoll/` named by the document's own production stamp, never the download day, and rows are dated by the fielding end date. Every number is cross-checked before it leaves: the three nets must sum to about 100, and each must agree with its own count over the weighted base. The reader is [`ssa/adapters/hhpoll.py`](https://github.com/Social-Atoms/social-sim-arena/blob/main/ssa/adapters/hhpoll.py); its docstring records what was verified.

**How a question resolves.** On the first topline archived with a production stamp after the lock, so every question on this task carries `release_estimated: true` and resolves on the next published wave.

**What to watch.** Only 2026 vintages have been verified; the archive holds February, March, April, June (the May poll) and July 2026. A backfill into earlier years needs its own verification pass before any of those PDFs are trusted.
