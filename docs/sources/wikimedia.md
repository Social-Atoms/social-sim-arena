# Wikimedia pageviews

**What the number is.** How many times an article on the English Wikipedia was requested, per day, by human readers, from the Wikimedia Pageviews API. The Wikipedia weekly top 10 question asks for the ordered top ten articles by pageviews for a Monday-to-Sunday week. A day's count is computed once from the request logs and never revised, which makes this the simplest resolution source in the arena.

**Where it is published.** [wikimedia.org/api/rest_v1](https://wikimedia.org/api/rest_v1/), free and keyless; a descriptive user agent is required and enforced. The [Topviews](https://pageviews.wmcloud.org/topviews/) tool shows the same data.

**How the arena reads it.** [`ssa/adapters/wikipedia.py`](https://github.com/Social-Atoms/social-sim-arena/blob/main/ssa/adapters/wikipedia.py) asks for user traffic only, never spiders or automated requests, and works in complete Monday-to-Sunday weeks dated by the Sunday they end; a week with a missing day is not emitted. For the top 10 it fetches the daily top endpoint (`top/en.wikipedia/all-access`), archives each day's list under `wikitop/`, and sums the seven daily lists per article, applying one exclusion rule (the main page and non-article namespaces). Freshness is read off the response, never the clock; the API runs a day or two behind the present.

**How a question resolves.** On the week's summed list from the archived daily lists, ordered by total views, scored with rank-biased overlap against the filed list.

**What to watch.** The archive of daily lists started in late July 2026, so the published history is a few weeks long. The earlier per-article pageview task (Wikipedia pageviews) was retired in favour of the top-10 list.
