# Collection and Reliability Review

## Verified Code Paths

YouGov toplines use the Silver Bulletin databases. Crosstabs use the archived
workbook/parser and explicit manual acquisition command. The latest committed
crosstab workbook is 2026-10-16 with wave date 2026-10-12. No scheduled YouGov
download was enabled or executed for this task.

`tools/local_source_archive.py` already implements Civiqs/Trends/Wikipedia
collection and has commit/push behavior. It was not executed during source
evaluation. The refresh
workflow imports newly added archive files from the `source-archive` branch
before processing. Code inspection verifies that path; it does not prove the
current external courier host, launchd schedule, or remote delivery is healthy.
Those deployment facts remain unverified.

The committed operator snapshot flags Civiqs `deadline_risk` after 403 responses.
Its available original vintages cover different end dates: approval/economic
direction 2026-10-20, current economy/family finances/inflation 2026-10-19,
feelings 2026-10-15. File capture day alone is not the reading date.

`docs/operator-status.md` and the current implementation already describe
per-source isolation and validated same-source archive fallback. The handoff's
older strict-build wording must not be treated as an instruction to remove
that newer reliability behavior.

## New Local Collector

`tools/consumer_data.py` covers Michigan finals/current, SCE and Mint only
(`collect`, `fetch`, `import`, `build`, `verify`); Mint is a manual import because
its endpoint returns 403. It never imports refresh/provider execution or runs git.
Network fetches are explicit, bounded requests without automatic retries.
Normal TLS validation stays enabled. Failed parses cannot advance a valid
vintage pointer. Original and normalized bodies are content-addressed; replay
checks hashes and reruns the parser. A filesystem lock protects index updates
from concurrent collectors. Immutable attempt files preserve failure evidence.

A failed attempt can coexist with an earlier valid archive. The export records
`using_previous_vintage` and the failure state; it does not manufacture a new
release or settle any question. The review tool deliberately does not schedule
or resolve official tasks.

## Offline Failure Exercises

`tests/test_consumer_data.py` exercises HTML returned as data, duplicate months,
non-finite values, changed headings, missing Mint weeks, negative sales
corrections, raw tampering, rejection of the removed sources at the CLI, and a
failed live attempt with an earlier valid archive. It also checks that
append does not overwrite existing histories/rounds and that offline verification
does not fetch. No test uses live sources, endpoints, or paid models.

These are local injected failures, not proof of a deployed courier-to-refresh
drill. The external end-to-end exercise and maintenance-owner assignments remain
pending; no production scheduler or collection frequency was changed.

## Proposed Production Responsibilities

Assign a primary/backup owner per source, expected release calendar, semantic
period freshness budget, and an escalation/runbook. Alert on the expected wave
or period rather than file mtime. Check complete demographic/rank coverage.
Late captures must retain their true fetch time and cannot become earlier
first-print evidence. Separate collection from paid forecast execution, retain
private/public artifact boundaries, and hold affected rounds when release
evidence is missing. These are proposed deployment requirements, not a new
competing admission checklist.

## YouGov Crosstab Operations

Manual download remains the retrieval boundary until written permission for
automation exists. After a maintainer downloads a workbook, the operator path
must check the expected wave identifier, required demographic groups, headers,
row counts and SHA256 before archiving it. Alerts key off the expected wave,
not file modification time. A missing wave cannot settle a round, and a late
file retains its true acquisition time.

YouGov's [BrandIndex product page](https://yougov.com/business/products/brandindex)
advertises tailored snapshots and flexible API data delivery, but neither is a
public authorization for the arena's current crosstab workbook. A fixed file
delivery schedule and a license covering this exact tracker were not established
by the public materials. The operational choice is manual download plus offline
validation (see `tools/ingest_yougov_xtab.py`); do not schedule the existing
public download endpoint. Contract an API or fixed delivery only with written
terms specifying the product, retrieval method, analysis and redistribution.

## Civiqs Stable-Machine Plan

The preferred deployment is a small self-hosted runner or scheduled VM with
outbound HTTPS, encrypted storage and a deploy key limited to the archive branch.
The smallest always-on VM is expected to cost under USD 10 per month; the current
fetch path requires no Civiqs credential. A maintainer-operated Mac launch agent
is the fallback but has higher sleep and network risk. This document changes no
deployment and does not run collection on another machine.

On a missed fetch, retain the last valid snapshot with an explicit stale flag.
Never settle a round from stale data. A later successful fetch records the true
acquisition time and the revised vintage used for recovery. Courier and refresh
remain separate.
