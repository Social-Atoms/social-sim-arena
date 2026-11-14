# NY Fed SCE Extensions

**What the number is.** Nine candidate indicators from the existing monthly SCE
workbook: spending growth, probability of rising unemployment, probability of
rising stock prices, income growth, home price change, debt delinquency, earnings
growth, job loss, and job finding. Growth rates are percent; probabilities are
percentage-valued probabilities. The sampled population is US household heads,
with narrower conditioning for labor/debt questions requiring question-level
review. This is one source, not nine independent sources.

**Where it is published.** The committed official workbook
`sources/sce/2026-10-21.xlsx` contains 43 sheets. All nine selected sheets have
160 monthly observations, June 2013 through September 2026. Source:
`https://www.newyorkfed.org/medialibrary/interactives/sce/sce/downloads/data/frbny-sce-data.xlsx`.
Existing SCE licensing evidence remains in the workbook and source inventory.

**How the arena reads it.** The new parser reuses the existing SCE XML readers,
locates sheets by name and columns by complete header, and checks finite values,
unique dates, probability bounds, and monthly continuity. Header mappings and
units are recorded with each parsed vintage. The default collection reuses the
committed workbook offline; an explicit fetch is optional.

**How a question resolves.** Proposed: reuse SCE's reviewed monthly publication
calendar and immutable release-day workbook convention. Deadline/history freeze
must precede the named release; measurement month is a label, not a publish day.
Only the existing 1y/3y inflation tasks remain registered live. No new SCE question
or population/persona instrument is silently introduced.

**What to watch.** Recomputed last-60-month movement identifies earnings, job
loss, and job finding as noisy; debt delinquency is close to the boundary.
Income/home-price changes are small despite a positive movement diagnostic.
All remain candidates; demographic sheets are inventoried but not automatically
added. The archive is a current-vintage historical series, not thirteen years of
release-day artifacts. Owners and extension-specific operational signoff await
review; existing SCE maintenance can be reused after that review.
