# SCE Spending Sandbox

Target: the published median expected household spending growth for the October
2026 reference month, in percent. The publisher calendar lists November 9,
2026, 11:00 ET for the SCE release. The sandbox locks November 7 at 11:00 ET,
then reads the named release-day workbook. Its reference month is not a public
availability timestamp.

Example forecast: Normal(mean=2.4%, sd=1.0). Example published value: 2.5%.
The offline test parses a structurally valid workbook fixture, checks the lock
is before release, and scores with the arena's CRPS function. The fixture is a
synthetic sandbox case, not a claim about the November 9 actual value. A formal
round requires a release-day archived workbook, rights signoff and reviewer
acceptance; this PR does not register one.
