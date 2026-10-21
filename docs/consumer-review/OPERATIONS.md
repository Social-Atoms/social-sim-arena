# Collection and Reliability Review

## Verified Code Paths

YouGov toplines use the Silver Bulletin databases. Crosstabs use the archived
workbook/parser and explicit manual acquisition command. The latest committed
crosstab workbook is 2026-10-16 with wave date 2026-10-12. No scheduled YouGov
download was enabled or executed for this task.

`tools/local_source_archive.py` already implements Civiqs/Trends/Wikipedia
collection and has commit/push behavior. It was deliberately not executed:
the user's no-push requirement also applies to helper scripts. The refresh
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

`tools/consumer_data.py` never imports refresh/provider execution or runs git.
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

`tests/test_consumer_data.py` exercises HTML returned as data, incomplete ranks,
duplicate months, non-finite values, changed headings, wrong Steam country/week,
missing Mint weeks, negative sales corrections, raw tampering, missing API keys,
and a failed live attempt with an earlier valid archive. It also checks that
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
