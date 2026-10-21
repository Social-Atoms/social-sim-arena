# Terms Audit

Audited 2026-10-22. This is a retrieval, analysis and redistribution decision
for this repository. It is not legal advice. A source marked permission-needed
does not generate rounds.

| Source | Retrieval | Use | Redistribution / attribution | Decision |
| --- | --- | --- | --- | --- |
| NYT Books API | Official API key and API endpoint are documented in the [Books API specification](https://github.com/nytimes/public_api_specs/blob/master/books_api/books_api.yaml). | Books lists are publisher editorial rankings, not raw sales. API quota and API terms apply. | Developer portal terms and attribution page were not machine-readable in this audit. Keep the key private and do not publish raw API responses until the portal terms are recorded. | Permission needed |
| Netflix Top 10 | The [Netflix Terms of Use](https://help.netflix.com/legal/termsofuse) prohibit robots, spiders, scrapers, automated access and data-mining methods. | The same terms prohibit using Netflix content/information for machine-learning testing, benchmarking or validation without authorization. | Public TSV availability does not override the service terms. Do not redistribute the TSV or use it for benchmark scoring without written authorization. | Stop automated benchmark use |
| Steam weekly charts | [Steam Subscriber Agreement](https://store.steampowered.com/subscriber_agreement/) prohibits scripts, bots and non-human-controlled systems from interacting with Steam. | [Steam Web API Terms](https://steamcommunity.com/dev/apiterms) permit Steam Data in an application for end-user personal use, subject to the terms and documentation. The chart page is not itself a confirmed API grant. | Attribution/link obligations and personal-use limits apply. A public benchmark redistribution license was not established. | Permission needed; retain manual evidence only |
| ACSI | Public pages were readable in the captured corpus; automated access and page terms need separate review. | Publisher footers state: “No advertising or other promotional use can be made of ACSI data and information without the express prior written consent of ACSI LLC.” | The promotional-use restriction does not by itself prove that all non-promotional research redistribution is allowed. No written permission for this repository was found. | Permission needed |

Existing approved publisher records cover the Michigan national tables and the NY
Fed SCE workbook. U.S. Mint permits republication with source citation on its
cumulative-sales page; the automatic endpoint remains blocked, so manual CSV
import is the supported route. All seven source adapters must retain raw hashes,
capture dates and source URLs even when the public release contains only derived
records.

YouGov's [public-data license](https://yougov.com/about/terms/public-data-license)
prohibits automated scripts, bots and crawlers without express written permission,
and prohibits training or developing ML models from licensed data. Brand Movers
and Ratings need separate retrieval/use/redistribution decisions and do not
inherit a crosstab position. Manual wave validation does not make network calls.

## Operational Consequences

- Netflix is stopped for automated benchmark use until written authorization.
- NYT, Steam and ACSI may remain as private/manual evidence, but their adapters
  cannot generate rounds or publish raw bodies while the terms decision is open.
- A source approval decision is separate from an adapter merge. `ssa/inventory.py`
  is the generation gate.
