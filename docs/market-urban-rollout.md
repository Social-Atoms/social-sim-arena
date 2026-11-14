# Market and Urban rollout — 10 November 2026

This release adds a future question batch to the current arena. It preserves
published questions, results, scoring and the frozen S0/Common cohort. It does
not reset the leaderboard or create a separate Season 1.

## Admitted launch

| Track | Sources | Question status |
| --- | --- | --- |
| Market | New York Fed household spending, stock-price expectations and unemployment expectations | November survey, released 7 December; lock 5 December 16:00 UTC |
| Market | European Commission consumer confidence, EU and euro area | Existing dev definitions, lock 18 November; no country profile |
| Urban | Seattle community calls, NYC 311, NY Fed moving profile, BLS CPI rent, Singapore COE, taxis, HDB carparks, LTA train and bus | 32 new future rounds, plus the three market SCE rounds above |

All 35 newly added rounds count toward scoring. The owner's 10 November decision
removes unscored trial requirements. Capture completeness, a valid reference,
pre-behavior locks, missing-data rules and cancellation checks still apply.
The first new Urban lock is 30 November 15:59 UTC (LTA December targets).
The three October SCE definitions that never reached main before their locks
are withdrawn from the dev manifest and replaced by future November targets.
Every question already on main remains unchanged.

## Waiting outside this release's active question set

| Sources | What is still needed |
| --- | --- |
| MTA and TSA | Rights admission; MTA also needs a weekly manual-download operator |
| Michigan inflation, Netflix, ACSI | Written source permission and source-specific release contracts |
| NYT | Evidence for the sales/publication window; API key is configured |
| Steam | Production key and reviewed measurement window |
| US Mint | Eligible product evidence and adjacent manual reports |
| Texas, Chicago CTA | Written source permission, then implementation; cards exist |
| Zillow | Written permission, then card approval and implementation |
| OpenTable | Parked; no collection or activation |

These sources are not added as live questions. Disabled reusable adapters may
remain in dev; their existence does not bypass admission. The pending review PR
holds the outstanding decisions and original cards without enabling collection.

## Regular data fetching

`market-urban-data-fetch` runs at minute 37 every six hours, independently of
model forecasting. It can also be dispatched manually on a reviewed branch.
It checks inventory rights before each public collector, captures each source
independently, and records sanitized status in `sources/track-fetch-status.json`.
A failed source cannot skip later sources. Successful public captures are kept
before the job reports failures. Consumer originals remain age-encrypted on the
existing sealed branch; public consumer data is rebuilt only after persistence.
No provider keys or participant signing keys are supplied to this workflow.

The existing six-hour forecast/resolve refresh is retained. The additional
NYC pre-lock captures, BLS release-hour captures and Singapore Friday/Saturday
captures remain scheduled. Manual MTA/Mint routes are not replaced by scraping.
Unresolved TSA rights now prevent automatic capture.

GitHub schedule triggers run only after the workflows reach the default branch
(main). A dev merge or preview is not production activation. LTA_API_KEY and
NYT_API_KEY are configured; the existing age recipient/identities are reused.
STEAM_API_KEY is absent, so Steam stays in preparation.

Before main merges, review the future dates and the pending-source exclusions.
If the release slips past a new lock, move only unpublished questions forward;
never backdate admission or rewrite an already public question.
