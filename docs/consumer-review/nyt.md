# NYT Best Sellers

**What the number is.** Proposed target: the official top ten of a named NYT
Books list. Population is purchases counted under NYT's editorial methodology,
not a survey population or raw unit sales. Preserve ISBN13 edition identity,
dagger/asterisk flags, and the exact list name. Candidate/new-book policy remains
an organizer decision.

**Where it is published.** Official Books API:
`https://api.nytimes.com/svc/books/v3/lists/current/hardcover-fiction.json`.
The research references `https://developer.nytimes.com/docs/books-product/1/overview`.
An authenticated Books API request succeeded on 2026-10-22 (Asia/Shanghai).
Archived 52 consecutive Hardcover Fiction top-ten lists, with publication labels
from 2025-11-02 through 2026-10-25. The collection archive retains acquisition
dates and ISBN-level identifiers. Account credentials are excluded from the PR.

**How the arena reads it.** Proposed, not live: `nyt_books` accepts a dated list
or overview JSON, validates complete top-ten ranks and ISBN13 identifiers, and
preserves publication and bestseller dates separately. The CLI reads only the
`NYT_API_KEY` environment variable or a private credential file written by
`configure-nyt`; request errors and manifests omit the key. The prototype is not
included in this documentation-only PR.
Authenticated requests do not follow redirects. An authorized local JSON export can be imported without network access.
The bounded `history nyt --weeks 52` command keeps at least 13 seconds between
requests, skips retained weeks, checks the returned publication date and list,
and stops on the first failed response without retrying. Synthetic test fixtures
are never mixed into acquired data.

**How a question resolves.** Pending review. The local ranking bridge uses the
publication date as its `week_end` label and records that it is not the sales
window end. The current response was captured before its 2026-10-25 publication
label and identifies 2026-10-10 as its bestseller date. The future publication
label is not the first-availability timestamp. A formal task must establish the
actual sales window, first public availability, pre-window deadline/history
freeze when applicable, and a pinned release artifact before enabling a resolver.
No date is inferred from a generic 48-hour rule here.

**What to watch.** Retrieval/use/redistribution, quotas, historical list access,
edition aggregation, bulk purchases, candidate changes, and first-print versions
remain open. The 51 consecutive weekly comparisons average 4.0980 new ISBN
editions entering the top ten. This is a retrospective diagnostic, not a
forecasting backtest. Default acquisition covers hardcover fiction only as a
probe, not all NYT lists. Retrospective API retrieval is not evidence of the
original historical first print. Collection is manual on demand in this workspace;
primary and backup owners are unassigned. No live round is enabled.
