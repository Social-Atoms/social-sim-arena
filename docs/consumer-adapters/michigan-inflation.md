# Michigan Inflation Adapter

The adapter keeps the one-year and five-to-ten-year expectation CSVs separate
from the existing sentiment index. Preliminary and final files are separate
vintages; a preliminary row never overwrites a final row. Rows use the survey
month, while the resolver uses the captured release-day artifact. The first
priority source is one-year inflation expectations. No round is activated here.

Code: `michigan_finals` and `michigan_current` in `ssa/adapters/consumer.py`,
collected by `tools/consumer_data.py`. The inventory row stays `unresolved`
until a maintainer signs off on extending the existing Michigan approval.
