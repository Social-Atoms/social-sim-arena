# US Mint Weekly Sales

**What the number is.** Proposed target: weekly change in cumulative net sales
for a specific fixed-limit product/SKU. Net sales may decline after returns or
cancellations. The buyer population may include dealers and must not be described
as ordinary individual consumers without evidence. Running totals are diagnostics.

**Where it is published.** Official cumulative sales page:
`https://www.usmint.gov/about/production-sales-figures/cumulative-sales`.
Research reports publication Tuesday by 17:00 Eastern for sales through Sunday;
that contract still needs live confirmation. Automated access previously returned
403. Two manually downloaded official CSV reports have period ends
2026-10-11 and 2026-10-18. Each contains 698 unique SKUs, all matched across the
two dates. Raw bytes, hashes, labels and capture times are retained privately.

**How the arena reads it.** The private importer reads the original five-column
official CSV or the existing `product_id,period_end,cumulative_net_sales`
transcription, with original evidence and a publisher URL. `Item` is the SKU,
`Adj. Net Demand` the cumulative count, and `Date Sales Report is Valid` the period
end. Dates must be Sundays, counts nonnegative integers, and SKU/date pairs unique.
After joining archived reports in calendar order, it computes differences only
between adjacent seven-day periods. It retains negative changes, skips gaps, and
flags revised cumulative values while preserving the earliest retained capture.
The two reports yield 1,396 cumulative observations and 698 weekly-change
observations: 131 positive, 555 zero and 12 negative. Original evidence and
source hashes are recorded in the separate data snapshot.

**How a question resolves.** Pending product and schedule review. A task needs a
fixed measurement interval, official release and audit policy, premeasurement
deadline where live sales information would leak, and a pinned original release.
Do not resolve from a revised cumulative number or infer a sold-out state from a
temporary batch shortage.

**What to watch.** Each SKU currently has one weekly-change observation, so longer
weekly histories are needed to assess movement. All 698 SKUs are archived for
review; they are not 698 approved fixed-limit forecasting targets. Retired
products, total limits versus batch supply, replenishment, dealer mix, SKU units,
corrections and holiday delays need review. Automated access remains unresolved;
manual official exports work. Owners, cadence, rights, historical first
prints and formal target admission remain pending.
