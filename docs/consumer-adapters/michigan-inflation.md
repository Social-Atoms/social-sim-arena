# Michigan Inflation Adapter

The adapter keeps the one-year and five-to-ten-year expectation CSVs separate
from the existing sentiment index. Preliminary and final files are separate
vintages; a preliminary row never overwrites a final row. Rows use the survey
month, while the resolver uses the captured release-day artifact. The first
priority source is one-year inflation expectations. No round is activated here.
