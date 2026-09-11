# Sources

One page per source the arena scores against. Each says the same five things, in the same order: what the number is, where it is published, how the arena reads it, how a question on it resolves, and what to watch out for. The page is the reader's entry; the adapter it links is the code that does the reading.

| Source | Page | Tasks on the site | Reader |
| --- | --- | --- | --- |
| Economist / YouGov | [yougov.md](yougov.md) | Economist / YouGov approval, YouGov measured crosstabs, the YouGov half of Generic ballot & midterm | `ssa/adapters/silverbulletin.py`, `ssa/adapters/yougov_xtab.py` |
| Morning Consult | [morning-consult.md](morning-consult.md) | Morning Consult approval | `ssa/adapters/silverbulletin.py` |
| Silver Bulletin poll database | [silver-bulletin.md](silver-bulletin.md) | Generic ballot & midterm; the poll-level records behind the two approval tasks above | `ssa/adapters/silverbulletin.py`, `ssa/average.py` |
| Civiqs | [civiqs.md](civiqs.md) | Civiqs approval, the five Civiqs sentiment trackers, Civiqs 16-cell profile | `ssa/adapters/civiqs.py` |
| Harvard CAPS / Harris | [harvard-harris.md](harvard-harris.md) | Harvard / Harris approval | `ssa/adapters/hhpoll.py` |
| University of Michigan | [michigan.md](michigan.md) | Michigan consumer sentiment, Sentiment by party | `ssa/adapters/umich.py`, `ssa/adapters/umichparty.py` |
| New York Fed SCE | [ny-fed-sce.md](ny-fed-sce.md) | Household inflation expectations | `ssa/adapters/sce.py` |
| AAII | [aaii.md](aaii.md) | Investor sentiment | `ssa/adapters/aaii.py` |
| Google Trends | [google-trends.md](google-trends.md) | Brand search-interest mix | `ssa/adapters/trends.py` |
| Wikimedia | [wikimedia.md](wikimedia.md) | Wikipedia weekly top 10 | `ssa/adapters/wikipedia.py` |

Two rules hold across every page. A number is read from an archive the arena keeps, never from whatever the publisher shows at the moment of scoring, so every resolution can be checked later. And a source that goes quiet fails loudly rather than serving something stale; [sources.md](../sources.md) is the process for adding a source and says why.
