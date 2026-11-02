# Consumer and Market Pipeline

This implementation connects five sources to the arena's existing forecast
types and scorers. It does not add live questions or change the maintainer's
rights decisions. The season validator still refuses nonapproved sources.
Netflix and ACSI have no collector or scoring target while permission is absent.

| Source | Ingestion | Forecast and score | Public evidence |
| --- | --- | --- | --- |
| NY Fed SCE | Official workbook, exact sheet/header matching | Spending growth, stock-price rise probability, unemployment rise probability; normal forecast / CRPS | Outcome, SHA256, full FRBNY attribution and disclaimer |
| Michigan | Official inflation CSVs, separate preliminary/final artifacts and target IDs | One-year inflation expectation; normal forecast / CRPS | Outcome, SHA256, copyright line; original CSV stays private |
| US Mint | Manually downloaded official CSV plus publisher artifact | Net change between adjacent Sunday reports; normal forecast / CRPS | Increment, both report hashes, source credit |
| NYT | Official Books API with `NYT_API_KEY` | Named list, primary ISBN-13 edition identity, future ordered top N / RBO | Scores and permitted rank display; API response stays private; NYT credit |
| Steam | Official `IStoreTopSellersService/GetWeeklyTopSellers/v1/` with `STEAM_API_KEY` | Fixed country/week, typed app/package/bundle IDs, future ordered top N / RBO | Scores and permitted rank display; API response stays private; Steam credit |

## Run

Use a persistent private directory on the collection machine, set by
`SSA_CONSUMER_ARCHIVE`. The default is the repository's ignored consumer archive.
Its raw files, normalized files, credentials, historical ranking lists and lock
snapshots must not be committed, attached to a PR, uploaded to a public Release,
or bundled into a deployment. In-repository archive writes outside the ignored
directory are refused. The environment variable can point at a private volume.
Back up that volume through an authorized private storage service; public GitHub
Actions artifacts are not the storage mechanism.

1. Configure API keys locally or as private runner secrets. Register NYT access
   at [NYT Developers](https://developer.nytimes.com/) and Steam access at the
   [Steam Web API key page](https://steamcommunity.com/dev/apikey). Keys are sent
   as request parameters, never saved in URLs, manifests, logs or public outputs.
2. Collect survey data with `python tools/consumer_data.py collect --sources sce
   michigan_current michigan_finals --fetch-sce`. For NYT use `fetch nyt` with
   the exact dated Books list URL. For Steam use `fetch steam` with the official
   API URL and `--steam-context` pointing at a private JSON object containing
   only `country_code` and the publisher's integer `start_date` label. An empty
   response, API error, wrong country or different returned week is rejected.
3. For Mint, download through the official website and run `consumer_data.py
   import mint` with `--url` and `--evidence-file`. The collector makes no
   scripted Mint request and does not bypass its 403 protection.
4. Put reviewed consumer rounds in the season manifest with the contract below.
   Use the `consumer-` round-ID prefix, which routes input snapshots to private
   storage. The usual inventory rights gate applies to publication and scoring.
5. Run the normal `ssa.refresh` on the machine with this archive, or run
   `python tools/run_consumer_cycle.py --season questions/season0.json` to
   process admitted consumer rounds without calling models or touching upstream.
   Run before the call window opens to freeze actual inputs; retain this private
   archive between runs. Public timestamps hash the private input snapshot.
6. The cycle writes its report and write-once resolutions in private storage.
   Its `public_results` section contains per-round scores, attribution and
   publisher hashes/URLs; it excludes raw bodies, normalized histories and
   private file paths. The normal question and forecast pages show attribution.

## Round Contract

Every round carries the usual arena fields and a `consumer` object:

```json
{
  "source": "sce",
  "reference_date": "2026-10-01",
  "population": "US household heads",
  "calendar_url": "https://www.newyorkfed.org/research/calendars/nationalecon_cal",
  "capture_until": "2026-11-10T16:00:00Z"
}
```

This is the SCE contract shape, not an added live question. A complete question
also fixes its release and lock timestamps from the official calendar. Monthly
survey locks precede release by at least 48 hours. The three target IDs are
`sce_spending_growth`, `sce_stock_prices_rise`, and `sce_unemployment_rise`.
Their source key for admission is `consumer_sce_extensions`.

Michigan uses `umich_inflation_1y_preliminary` / `michigan_current` and
`umich_inflation_1y_final` / `michigan_finals`. A preliminary result can never
settle from a final CSV, even when the values are equal. Historical final data
does not fabricate preliminary first prints. A target needs enough genuinely
captured history for its persistence baseline before it can score.

Weekly targets also carry `measurement_start`, `measurement_end` (exclusive),
and `measurement_timezone` (UTC by default). They lock strictly before the
measurement starts; a release-minus-48-hours rule is insufficient. Windows span
seven local calendar days, including daylight-saving transitions. The reference
date is the final included day. NYT sales weeks end Saturday; the separately
declared `published_date` is a publication label, not a sales date or proof of
first availability. Steam weeks run Tuesday through Monday and must preserve
the publisher's timestamp label without treating it as a measurement cutoff.
The reviewed question supplies actual measurement timestamps explicitly.

NYT requires `list_name` and `published_date`; Steam requires `country`.
Both use the existing `ranking_list` answer shape and an RBO `ranking` block
with exact week dates, length and `rbo_p`. IDs are `isbn:<13 digits>` or
`app:<id>`, `package:<id>`, `bundle:<id>`. Entrants may name previously unlisted
items. Reordering an otherwise correct list loses credit; completely new lists
remain scoreable. There is no dropout question or fixed 20-30 candidate roster.

Mint uses target `mint_weekly_net_sales`, a `product_id`, and a `product` object
containing `fixed_supply`, `general_public_buyers`, `batches_known`, and
`not_sold_out` (all true), plus a positive `supply_limit` and `evidence_url`.
Ordinary bullion/production totals, products with open-ended replenishment,
dealer-only channels and already sold-out products do not pass this contract.
No product is silently chosen just because a CSV contains it.

## Settlement and Tests

History contains only artifacts captured before the entrants' shared input
freeze. Settlement chooses the first captured artifact for the exact declared
period and stage between `release_at` and `capture_until`. A later revision
cannot replace it. A missing or late release remains unsettled; an older valid
file is useful for history but cannot answer the new question. Mint additionally
requires the adjacent prior report and preserves negative net changes.

The adapter, scalar resolver, ranking contract, normal refresh, public timestamp
manifest and standalone cycle share this implementation. CRPS and RBO feed the
existing score fields and Pairwise consumers; geography creates no new domain.
Sources without a matching survey instrument receive no US persona weighting.

Run `python -m tests.test_consumer_pipeline` and
`python -m tests.test_consumer_data`; the existing questions CI workflow discovers
these suites alongside the ranking, season, resolution and timestamp contracts. Fixtures are
generated synthetic publisher artifacts, including the complete nine-sheet SCE
layout. Tests exercise ingestion through actual production freeze, resolution
and leaderboard code, not a separate example scoring formula.

Implementation references: [NYT's official Books API schema](https://github.com/nytimes/public_api_specs/blob/master/books_api/books_api.yaml),
[Steam Web API documentation](https://partner.steamgames.com/doc/webapi_overview),
and Valve's served `CStoreTopSellers_GetWeeklyTopSellers_Request` / `Response`
definitions in its [official chart application module](https://cdn.akamai.steamstatic.com/store/ssr/DdF7rI4Q.js)
(checked 2026-11-02). Steam's unauthenticated endpoint returns an API error;
authenticated retrieval needs an operator's key and is not claimed as verified
by the synthetic integration tests.

Permission drafts for Michigan, ACSI and Netflix are in
[Permission Email Drafts](PERMISSION_EMAIL_DRAFTS.md). They have not been sent.
