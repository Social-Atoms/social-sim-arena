# U.S. Mint Adapter

The adapter accepts the official five-column CSV by manual download because the
publisher endpoint returns HTTP 403 to automated requests. It joins cumulative
SKU values by Sunday period and emits the adjacent-period increment. Negative
changes remain valid revisions. Candidate questions are limited to fixed-supply
products after checking public buyers, batch releases, non-instant sellout and
known limits. Cumulative totals are diagnostics, not targets. No round is active.

Code: `mint` in `ssa/adapters/consumer.py`, loaded with
`tools/consumer_data.py import mint <file> --url <page> --evidence-file <file>`.
The inventory row stays `unresolved`: the cumulative-sales page's republication
statement must be quoted verbatim with its URL
(https://www.usmint.gov/about/production-sales-figures/cumulative-sales) and
signed off by a maintainer before approval.
