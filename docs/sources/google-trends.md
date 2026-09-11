# Google Trends

**What the number is.** Search interest, the arena's first behavioral source: what people typed into a search box, not what they told an interviewer. Trends values are not counts. Each response is normalized to itself, the busiest week in the requested window is 100 and everything else is scaled to it, so the window is part of the definition. The Brand search-interest mix question asks for the share of weekly search interest taken by each of five brands (Tesla, iPhone, Samsung, Netflix, Disney), from one five-query comparison, US, web search, trailing twelve months.

**Where it is published.** [trends.google.com](https://trends.google.com/trends/). There is no official API; the route used is the one the Trends web page itself uses, two hops (an explore call that returns widget tokens, then the widget data), with a browser-shaped user agent and a cookie from a prior visit, none of it documented or promised.

**How the arena reads it.** [`ssa/adapters/trends.py`](https://github.com/Social-Atoms/social-sim-arena/blob/main/ssa/adapters/trends.py) fetches with the window fixed at the trailing twelve months and weekly granularity taken natively (rows seven days apart, verified). Because the whole scale shifts when a peak enters or leaves the window, and because the index is computed from a sample and the same completed week can read a point or two differently on different days, every fetch is archived under `trends/` at the repository root, one immutable file per query and fetch day. A completed week's value is whatever the earliest snapshot containing it showed, forever. Rate limits are real and were measured; the reader spaces its requests.

**How a question resolves.** On the first archived comparison snapshot that carries the completed week, each brand's share of the five.

**What to watch.** The earlier single-keyword task (Search interest levels) was declined: a series normalized within its own window is not comparable across fetches in the way a score needs. The five-brand mix survives because shares within one comparison are.
