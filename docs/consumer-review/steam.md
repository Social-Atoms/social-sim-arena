# Steam Weekly Top Sellers

**What the number is.** Proposed target: the US top ten products by revenue in
Steam's official weekly chart. The population is Steam US purchasing activity;
this is neither copies sold nor concurrent players. Hardware and DLC are not
silently filtered out. App/package/bundle identifiers preserve entity type.

**Where it is published.** `https://store.steampowered.com/charts/topsellers/US`
and its dated weekly pages. The page states Tuesday 1AM Pacific publication.
Fifty-two consecutive weekly charts were captured, with starts from 2025-10-21
through 2026-10-13. Latest measured interval is labeled October 13-20.
The page's browsing currency/context is not the chart's country definition.

**How the arena reads it.** The new offline parser reads inert
`valve-ssr-data` JSON, requires the weekly route and `weekly.strCountryCode=US`,
checks exactly one typed ID per entry, and extracts ranks 1-10. Historical
requests must return the requested week. Each original HTML page and parsed list
is hashed and retained. Earlier captured versions are never overwritten.

**How a question resolves.** Proposed, not active: a ranking task over the
publisher's dated US chart, with deadline and visible history frozen before the
measurement week starts. The compatibility `week_end` is the final included
Monday; metadata retains both publisher Tuesday labels. `rtWeekStart` is a date
label, not proof of a midnight-UTC measurement cutoff. Exact Pacific boundaries,
candidate/new-item rules, and the loss contract need research review.

**What to watch.** Retrieval and redistribution terms remain unresolved. Charts
can contain purchases, free-to-play revenue, DLC, preorders, and hardware.
Top-ten churn is measured locally; no first-print historical backtest or live
readiness is claimed. Initial collection required two transport-gap backfills;
all 52 weeks now replay from local artifacts. Collection is a bounded local probe,
not a new cron; maintainers and monitoring budgets are unassigned.
