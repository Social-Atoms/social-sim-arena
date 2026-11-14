# Continuous-Stream Target Protocol

Status: proposed rule for Class B streams. No stream target activates from this
document.

1. Define a fixed metric, population, unit and source endpoint before the first
   measurement. Publish the proposed observation-window start/end and timezone,
   and timestamp the definition before the window opens. No after-the-fact
   choice of the most interesting window or series is allowed.
2. Lock all forecasts and their visible history before the observation window
   starts. Archive the locked candidate set, question text, model inputs and
   expected sampling schedule. A release-based event date set by a third party
   does not permit choosing a convenient post-release measurement window later.
3. Sample on a fixed schedule with a documented clock tolerance. Store the raw
   response, URL, measurement period, capture timestamp, HTTP status, SHA256,
   parser version and monotonic reading sequence. No silent interpolation of a
   missing sample and no retrospective relabeling of a late capture.
4. Predeclare the aggregation rule and minimum coverage. An incomplete or late
   window is held, not scored as zero. All source failures are isolated to their
   affected rounds, with the last valid snapshot marked stale and unavailable
   for settlement.
5. Resolve from the earliest valid archive matching the declared window, never
   from a live endpoint or a later revised total. Record publisher revisions as
   new vintages; do not overwrite the pinned result. A correction policy must
   state when a settlement is voided versus retained.
6. Dry-run at least one complete window and one missed-sample scenario, including
   archived raw evidence, reproducible parsing, baseline, scoring and a visible
   hold on incomplete data. Obtain source terms and owner/backup signoff before
   registering a formal task.

TSA daily passengers and Steam concurrent-player counts remain unregistered
until their source-specific windows, sampling coverage and rights pass this
protocol. This is stricter than treating a daily page update as an announced
release.
