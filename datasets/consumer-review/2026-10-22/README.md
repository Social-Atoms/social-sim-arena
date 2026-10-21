# Consumer Data Review Snapshot

This directory contains actual data for Michigan inflation expectations,
New York Fed SCE extensions and U.S. Mint sales. Source admission remains pending.
It complements the seven-source evaluation in [PR #227](https://github.com/Social-Atoms/social-sim-arena/pull/227).

- `normalized.json`: 1,407 scalar histories and 4,601 `{date, value}` observations.
- `schema.json`: the JSON schema for the normalized review bundle.
- `provenance.json`: original publisher URLs, capture times, hashes and raw-file mappings.
- `raw/`: five original CSV/XLSX artifacts, preserved without modification.
- `manifest.json`: coverage totals and the four sources omitted from public upload.

Michigan has two inflation horizons through October 2026 preliminary. Finals
end in September, and sparse long-horizon months are not filled with zeros.
SCE has nine monthly indicators, 160 values each, June 2013-September 2026.
Mint has 698 matched SKUs at 2026-10-11 and 2026-10-18: 1,396 cumulative values
and 698 weekly changes, including 12 negative and 555 zero changes. Cumulative
Mint totals are diagnostics; each SKU currently has only one weekly change.

Sources: [Michigan](https://www.sca.isr.umich.edu/),
[Federal Reserve Bank of New York](https://www.newyorkfed.org/microeconomics/sce),
and [United States Mint](https://www.usmint.gov/about/production-sales-figures/cumulative-sales).
Mint permits republication with its attribution; SCE's original workbook contains
its data license. Existing repository approval covers Michigan's national tables.
The SCE workbook includes original publisher/license information and is not
relicensed by this snapshot.

NYT, Netflix, Steam and ACSI are omitted pending public redistribution review;
their local data is not represented as missing zeros. These captures are current
vintages, not proof of historical first prints or a leakage-free backtest.
No active task, resolution, leaderboard or production site-data file is changed.

Provenance IDs in the bundle resolve through the keys in `provenance.json`;
the corresponding raw-file paths are relative to this directory. All values are
numeric observations; publisher descriptions remain metadata.
