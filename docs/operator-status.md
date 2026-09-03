# Refresh reliability and operator status

Every refresh writes `site/operator.json` and prints the same information as a
sorted text summary.  The JSON is the durable incident record; log lines are no
longer the only evidence that a source or entrant failed.

The scheduled workflow runs refresh twice.  The first pass files forecasts and
the second (`SSA_SKIP_FILING=1`) rebuilds after resolution.  The second pass
carries the first pass's entrant states forward, so retry suppression cannot
erase a timeout or terminal credential failure from the report.

## States

Source states are:

| State | Meaning | Operator action |
|---|---|---|
| `healthy` | A validated response is within both freshness budgets. | None. |
| `retryable_failure` | A transport/service failure can wait for the next six-hour refresh. | Let the scheduled retry run; keep the last valid vintage. |
| `stale` | The upstream body has not moved within cadence, or the live attempt failed and the same validated source archive was used. | Restore/inspect the source; archived data may support forecasts but is not a new release. |
| `deadline_risk` | The normal retry would land after the next filing deadline. | Retry now or hold affected rounds. |
| `unresolvable` | Access/extraction is terminal for this route/body and no validated same-source archive is usable. | Repair access/parser. Never substitute a semantically different source. |

Entrant-round states are:

| State | Meaning | Operator action |
|---|---|---|
| `queued` | Waiting for a worker, news window, or spend capacity. | Let the next run drain it, unless its action says the next run is too late. |
| `running` | A worker owns the entrant-round. | Wait for the provider response. |
| `succeeded` | A real forecast artifact exists. | None; a standby success remains visibly alerted. |
| `retryable_failure` | 429, timeout, or transient transport/service failure. | Retry while the common batch deadline remains open. |
| `terminal_failure` | Dead credentials/account/model route. | Repair or deliberately switch route; do not make a scored mock. |
| `missed_lock` | No real forecast existed when the common filing deadline passed. | Record the miss. Never file late. |

Every row always contains `attempts`, `last_error`, `next_retry`, `next_lock`,
`next_deadline`, `route`, `estimated_spend`, `required_action`, `evidence`, and
`alert`.  Null values mean not applicable, not uncollected.

## Artifact safety

- Source loaders and per-source derivations are isolated. They all run even if
  one fails, and each validated success is preserved. A sibling 403 cannot
  erase it. With a valid same-source archive, dependent forecasting may
  continue against the explicitly stale vintage. Without one, only series and
  entrant-rounds backed by that source are omitted/held; unrelated rounds still
  file. The run exits non-zero after writing the partial successes.
- A response is parsed before it becomes the current provenance vintage. An
  HTTP 200 login page or shifted table is failure evidence, not a valid source.
  Silver Bulletin also checks the newest meaningful field date before writing
  provenance, so a frozen sheet whose wrapper bytes keep changing cannot reset
  the freshness clock and appear healthy.
- A failed live attempt may read only the manifest-verified archive for that
  exact source and must pass the same parser again. It remains visibly
  `stale`/`deadline_risk`; because it contains no new release, resolution does
  not settle against it. With no valid same-source artifact the source is
  `unresolvable`, and no different publisher is substituted.
- Michigan is one semantic source assembled from two official responses. Its
  manifest hashes a canonical envelope containing the exact finals and
  preliminary bodies plus both URLs. Archive fallback re-runs both live
  parsers; a legacy finals-only file, missing preliminary part, or tampered
  byte is rejected. Derived `site/data.json` is never source evidence.
- Scalar adapters that own archives (SCE, Civiqs and Trends)
  return transport diagnostics alongside usable archive rows. The registry
  keeps those rows but records the source as `stale`/`deadline_risk`; malformed
  live payloads remain loud and cannot hide behind an older parse. Civiqs
  health keeps fetch and upstream-reading clocks separate for every registered
  archive key (including the weekly emotion tracker's wider cadence); one fresh
  key cannot hide another frozen key, and stale/missing key names are evidence.
- Ranking feeds have their own semantic source rows:
  `ranking_wikitop` and `ranking_trends_basket`. A failed weekly-list source is
  not hidden by the unrelated scalar Wikipedia pageview/Trends series. A live
  failure with complete archived weeks keeps those histories but marks the
  ranking source degraded; with no usable history, only its rounds are held.
- Entrant workers write independently. A model timeout cannot remove another
  model's successful file.
- `SSA_ALLOW_MOCK=1` may still create a labelled local rendering artifact, but
  `count_forecasts` and every leaderboard exclude it, including the crowd.
- Web search is prospective-only. Planning, execution, cache replay, and
  scoring in `ssa.model_backtest` each refuse a web entrant independently.

## Verification

The complete offline fault matrix is:

```bash
PYTHONPATH=. python tests/test_reliability.py
```

It injects source 403 with and without an archive, empty-message timeouts,
stale HTTP 200, malformed extraction, scalar and ranking archive fallbacks,
provider 429/401 and timeout, route fallback, partial model success, spend
withholding, and a missed batch deadline. It also proves source/round
isolation, UMich composite integrity, deterministic output, second-pass state
carry, MOCK exclusion, and the web-backtest refusals.
