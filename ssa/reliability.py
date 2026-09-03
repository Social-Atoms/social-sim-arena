"""Durable, operator-facing state for one refresh run.

The refresh used to expose failures only as free-form log lines.  That is not
enough around a hard filing deadline: a provider timeout, a dead credential,
and a job withheld by the spend brake all require different actions even
though all three used to end as "no forecast file".

This module is deliberately independent of adapters and providers.  Callers
record the result of a source or entrant-round attempt; :class:`RunStatus`
classifies it into the finite states from issue #52 and produces one sorted,
deterministic document.  The document is suitable for ``site/operator.json``,
for a log summary, and for tests with no network.

The model is per run rather than a second job queue.  The committed forecast,
source vintage, reply log, and workflow cadence remain the durable work
records; this document answers the operator's narrower questions: what ran,
what survived, what happens next, and what needs a human now.
"""
from __future__ import annotations

import copy
import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone


SOURCE_STATES = frozenset({
    "healthy", "retryable_failure", "stale", "deadline_risk",
    "unresolvable",
})
ENTRANT_STATES = frozenset({
    "queued", "running", "succeeded", "retryable_failure",
    "terminal_failure", "missed_lock",
})

RETRY_INTERVAL = timedelta(hours=6)

_HTTP = re.compile(r"\bHTTP\s+(\d{3})\b", re.IGNORECASE)
_TERMINAL_WORDING = (
    "invalid_api_key", "incorrect api key", "account_deactivated",
    "organization has been disabled", "does not exist or you do not have access",
    "permission denied", "forbidden", "unauthorized", "authentication",
    "insufficient_quota", "credit_balance_exhausted", "no credits remaining",
    "billing", "unknown entrant", "unknown model", "no configured route",
)
_MALFORMED_WORDING = (
    "malformed", "parsed to zero", "built to zero", "zero points",
    "parse error", "failed to parse",
    "structure changed", "schema changed", "missing required",
    "refusing to guess", "invalid extraction",
    # Stable adapter contract errors.  These are responses that arrived but
    # cannot safely be interpreted, not transport failures another six-hour
    # retry can repair.  Keep the phrases aligned with the production adapters
    # and exercise them at the registry boundary in tests.
    "not an xlsx", "not a readable xlsx", "no sheet named",
    "has no sheetdata element", "the sce header row", "missing median",
    "reached its data rows before naming", "unreadable cell address",
    "not a yyyymm", "not a number; a",
    "not a median expected inflation", "history jumps from",
    "inflation sheet parsed to", "the sce history starts at",
    "no conference board headline", "no month is named",
    "the page says the index moved",
    "endpoint contract changed", "carries no widgets",
    "no timeseries widget", "returned an empty timeline",
    "returned an empty basket timeline", "not weekly",
    "google trends row carries", "google trends basket row carries",
    "response does not answer the request", "returned nothing",
    "no civiqs tracker payload", "carries no line_chart_data",
    "served its index page", "ignored the subgroup filter",
    "has no values", "no overlapping dates", "has no dated points",
    "returned no daily rows", "returned no items", "returned no articles",
    "refusing to archive it under the wrong day", "top pageviews listed",
)
_STALE_WORDING = (
    "stale", "frozen page", "stopped moving", "unchanged for",
    "newest row is", "newest observation",
)


def _as_utc(value):
    if value is None or isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(value):
    dt = _as_utc(value)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt is not None else None


def error_text(error):
    """Stable one-line error text; provider bodies remain useful but bounded."""
    if error is None:
        return None
    text = " ".join(str(error).split())
    # Several useful transport exceptions (notably bare ``TimeoutError()``)
    # stringify to the empty string.  An empty last_error is operationally
    # dangerous as well as unhelpful: source_health historically used its
    # truthiness to distinguish this run's failed live attempt from an older
    # healthy manifest clock.  Preserve at least the exception class so the
    # failure cannot be washed green and the operator has something actionable.
    if not text:
        text = (type(error).__name__ if isinstance(error, BaseException)
                else "unknown error")
    return text[:1000]


def http_status(error):
    match = _HTTP.search(error_text(error) or "")
    return int(match.group(1)) if match else None


def terminal_error(error, *, source=False):
    """Whether retrying the same operation later cannot repair this error.

    Provider 429 is intentionally retryable unless its body says the account
    is dead.  A malformed source extraction is terminal for the received body:
    retrying those same bytes would only repeat an unsafe interpretation.
    """
    text = (error_text(error) or "").lower()
    status = http_status(error)
    if status in (401, 403, 404):
        return True
    if any(word in text for word in _TERMINAL_WORDING):
        return True
    if re.search(r"\bno [a-z0-9_]+ in the environment\b", text):
        return True
    return source and any(word in text for word in _MALFORMED_WORDING)


def _next_retry(now, deadline=None):
    retry = _as_utc(now) + RETRY_INTERVAL
    due = _as_utc(deadline)
    return retry, bool(due is not None and retry >= due)


def _route_name(route):
    if isinstance(route, dict):
        via = route.get("via") or "unknown"
        base = (route.get("base") or "").split("//", 1)[-1].split("/", 1)[0]
        return f"{via}:{base}" if base else via
    return str(route) if route else None


class RunStatus:
    """Thread-safe source and entrant-round ledger for one refresh.

    All public records have the same operational fields even when a field is
    not applicable.  Consumers therefore never have to infer whether a missing
    key means "none", "not collected", or an older producer.
    """

    def __init__(self, now):
        self.now = _as_utc(now)
        self._sources = {}
        self._entrants = {}
        self._lock = threading.RLock()

    @staticmethod
    def _base(*, route=None, spend=0.0, next_lock=None, next_deadline=None):
        return {
            "attempts": 0,
            "last_error": None,
            "next_retry": None,
            "next_lock": iso(next_lock),
            "next_deadline": iso(next_deadline),
            "route": _route_name(route),
            "estimated_spend": round(float(spend or 0.0), 6),
            "required_action": "none",
            "evidence": [],
            "alert": False,
        }

    # -- sources ---------------------------------------------------------

    def source_started(self, source, *, route=None, next_lock=None,
                       next_deadline=None):
        with self._lock:
            row = self._sources.setdefault(
                source,
                dict(self._base(route=route, next_lock=next_lock,
                                next_deadline=next_deadline),
                     source=source, state="retryable_failure"))
            row["attempts"] += 1
            row["route"] = _route_name(route) or row["route"]
            row["required_action"] = "wait for the in-flight source attempt"
            return copy.deepcopy(row)

    def source_succeeded(self, source, *, evidence=None, stale=False,
                         route=None, next_lock=None, next_deadline=None,
                         attempted=True):
        with self._lock:
            row = self._sources.setdefault(
                source,
                dict(self._base(route=route, next_lock=next_lock,
                                next_deadline=next_deadline),
                     source=source, state="healthy"))
            if attempted and row["attempts"] == 0:
                row["attempts"] = 1
            row.update({
                "state": "stale" if stale else "healthy",
                "last_error": None,
                "next_retry": None,
                "route": _route_name(route) or row["route"],
                "required_action": (
                    "inspect upstream freshness; do not resolve a new round "
                    "until the source moves" if stale else "none"),
                "alert": bool(stale),
            })
            if evidence:
                row["evidence"] = sorted(set(row["evidence"] + [str(evidence)]))
            return copy.deepcopy(row)

    def source_failed(self, source, error, *, route=None, next_lock=None,
                      next_deadline=None, evidence=None, attempted=True):
        with self._lock:
            row = self._sources.setdefault(
                source,
                dict(self._base(route=route, next_lock=next_lock,
                                next_deadline=next_deadline),
                     source=source, state="retryable_failure"))
            if attempted and row["attempts"] == 0:
                row["attempts"] = 1
            err = error_text(error) or "unknown source failure"
            due = row.get("next_deadline") or iso(next_deadline)
            retry, risks_deadline = _next_retry(self.now, due)
            terminal = terminal_error(error, source=True)
            stale = any(word in (err or "").lower() for word in _STALE_WORDING)
            if stale:
                state = "deadline_risk" if risks_deadline else "stale"
                next_retry = iso(retry)
                action = (
                    "retry now or hold affected rounds before the filing deadline; "
                    "do not treat the unchanged body as a new release"
                    if risks_deadline else
                    "inspect upstream freshness; do not resolve a new round "
                    "until the source moves")
            elif terminal:
                state = "unresolvable"
                next_retry = None
                action = (
                    "repair source access or extraction; preserve the last "
                    "valid vintage and do not resolve from a substituted source")
            elif risks_deadline:
                state = "deadline_risk"
                next_retry = iso(retry)
                action = (
                    "retry now or hold affected rounds before the filing deadline")
            else:
                state = "retryable_failure"
                next_retry = iso(retry)
                action = (
                    "retry on the next refresh; preserve the last valid vintage")
            row.update({
                "state": state,
                "last_error": err,
                "next_retry": next_retry,
                "route": _route_name(route) or row["route"],
                "required_action": action,
                "alert": True,
            })
            if evidence:
                row["evidence"] = sorted(set(row["evidence"] + [str(evidence)]))
            return copy.deepcopy(row)

    def source_degraded(self, source, error, *, archive_evidence, route=None,
                        next_lock=None, next_deadline=None):
        """Record a failed live attempt served from the same validated source.

        This is not a semantic fallback: the archive is the exact source body
        previously accepted by the same parser.  It is nevertheless not a new
        observation, so it can never be called healthy.  Near a filing
        deadline the same condition is promoted to ``deadline_risk``.
        """
        with self._lock:
            row = self._sources.setdefault(
                source,
                dict(self._base(route=route, next_lock=next_lock,
                                next_deadline=next_deadline),
                     source=source, state="stale"))
            if row["attempts"] == 0:
                row["attempts"] = 1
            retry, risks_deadline = _next_retry(
                self.now, row.get("next_deadline") or next_deadline)
            state = "deadline_risk" if risks_deadline else "stale"
            action = (
                "restore live access now or hold affected rounds before the "
                "deadline; the same-source archive is not a new release"
                if risks_deadline else
                "restore live access; the validated same-source archive may "
                "support forecasts but is not evidence of a new release")
            row.update({
                "state": state,
                "last_error": error_text(error) or "unknown source failure",
                "next_retry": iso(retry),
                "route": _route_name(route) or row["route"],
                "required_action": action,
                "alert": True,
            })
            row["evidence"] = sorted(set(
                row["evidence"] + [str(archive_evidence),
                                   "live attempt failed; same-source archive used"]))
            return copy.deepcopy(row)

    def source_health(self, health_row, *, next_lock=None, next_deadline=None):
        """Fold the two-clock health check into the issue #52 state names."""
        source = health_row["source"]
        legacy = health_row.get("state")
        attempted = source in self._sources
        evidence = (
            f"fetched_days={health_row.get('fetched_days')}; "
            f"changed_days={health_row.get('changed_days')}; "
            f"fetch_budget={health_row.get('budget_fetch_days')}; "
            f"change_budget={health_row.get('budget_change_days')}"
            + (f"; stale_keys={','.join(health_row.get('stale_keys') or [])}"
               if health_row.get("stale_keys") else "")
            + (f"; failing_keys={','.join(health_row.get('failing_keys') or [])}"
               if health_row.get("failing_keys") else "")
            + (f"; missing_keys={','.join(health_row.get('missing_keys') or [])}"
               if health_row.get("missing_keys") else ""))
        with self._lock:
            current = self._sources.get(source)
            if current and current.get("alert") and \
                    current.get("state") != "healthy":
                # The manifest clocks describe a prior successful vintage,
                # not the live request that just failed.  They may add evidence
                # but cannot turn this run healthy or erase its actionable
                # error.  This applies both to a validated archive degradation
                # and to an unresolvable source whose manifest body was absent,
                # corrupt, or rejected by the current parser.
                current["evidence"] = sorted(set(
                    current["evidence"] + [evidence]))
                return copy.deepcopy(current)
            if legacy == "ok":
                return self.source_succeeded(
                    source, evidence=evidence, next_lock=next_lock,
                    next_deadline=next_deadline, attempted=attempted)
            if legacy == "stale":
                return self.source_succeeded(
                    source, evidence=evidence, stale=True, next_lock=next_lock,
                    next_deadline=next_deadline, attempted=attempted)
            if legacy == "failing":
                return self.source_failed(
                    source, "last successful fetch exceeded its freshness budget",
                    next_lock=next_lock, next_deadline=next_deadline,
                    evidence=evidence, attempted=attempted)
            # A required source with no successful vintage cannot be judged or
            # used safely.  "unknown" would create a sixth undeclared state.
            row = self._sources.setdefault(
                source,
                dict(self._base(next_lock=next_lock,
                                next_deadline=next_deadline),
                     source=source, state="unresolvable"))
            row.update({
                "state": "unresolvable",
                "last_error": "source has no successful fetch evidence",
                "required_action": "fetch and validate the source before publication",
                "evidence": [evidence],
                "alert": True,
            })
            return copy.deepcopy(row)

    # -- entrant-rounds --------------------------------------------------

    @staticmethod
    def _entrant_key(round_id, entrant):
        return (str(round_id), str(entrant))

    def entrant_queued(self, round_id, entrant, *, lock_at, deadline,
                       route=None, estimated_spend=0.0, next_retry=None,
                       action="wait for the filing worker"):
        with self._lock:
            key = self._entrant_key(round_id, entrant)
            row = self._entrants.setdefault(
                key,
                dict(self._base(route=route, spend=estimated_spend,
                                next_lock=lock_at, next_deadline=deadline),
                     round_id=key[0], entrant=key[1], state="queued"))
            row.update({
                "state": "queued",
                "next_retry": iso(next_retry),
                "estimated_spend": round(float(estimated_spend or 0.0), 6),
                "required_action": action,
                "alert": "withheld" in action.lower() or "raise" in action.lower(),
            })
            return copy.deepcopy(row)

    def entrant_started(self, round_id, entrant):
        with self._lock:
            key = self._entrant_key(round_id, entrant)
            if key not in self._entrants:
                raise KeyError(f"entrant-round was not queued: {round_id}/{entrant}")
            row = self._entrants[key]
            row.update({"state": "running", "next_retry": None,
                        "required_action": "wait for the provider response",
                        "alert": False})
            row["attempts"] += 1
            return copy.deepcopy(row)

    def entrant_deferred(self, round_id, entrant, *, next_retry, action):
        """Return a claimed job to the queue before any provider call."""
        with self._lock:
            key = self._entrant_key(round_id, entrant)
            if key not in self._entrants:
                raise KeyError(f"entrant-round was not queued: {round_id}/{entrant}")
            row = self._entrants[key]
            row.update({"state": "queued", "next_retry": iso(next_retry),
                        "required_action": action, "alert": False})
            row["attempts"] = max(0, row["attempts"] - 1)
            return copy.deepcopy(row)

    def entrant_succeeded(self, round_id, entrant, *, route=None,
                          artifact=None, fallback_error=None):
        with self._lock:
            key = self._entrant_key(round_id, entrant)
            if key not in self._entrants:
                raise KeyError(f"entrant-round was not queued: {round_id}/{entrant}")
            row = self._entrants[key]
            routed = _route_name(route) or row["route"]
            # A successful standby answer represents the failed direct attempt
            # plus the successful standby attempt, even though the harness owns
            # both calls inside one forecast job.
            if fallback_error:
                row["attempts"] = max(2, row["attempts"])
            row.update({
                "state": "succeeded",
                "last_error": error_text(fallback_error),
                "next_retry": None,
                "route": routed,
                "required_action": "none",
                "alert": bool(fallback_error),
            })
            if artifact:
                row["evidence"] = sorted(set(
                    row["evidence"] + [f"forecast={artifact}"]))
            if fallback_error:
                row["evidence"] = sorted(set(
                    row["evidence"] + ["standby route preserved the forecast"]))
            return copy.deepcopy(row)

    def entrant_failed(self, round_id, entrant, error, *, terminal=None):
        with self._lock:
            key = self._entrant_key(round_id, entrant)
            if key not in self._entrants:
                raise KeyError(f"entrant-round was not queued: {round_id}/{entrant}")
            row = self._entrants[key]
            if row["attempts"] == 0:
                row["attempts"] = 1
            err = error_text(error) or "unknown entrant failure"
            due = _as_utc(row["next_deadline"])
            terminal = terminal_error(error) if terminal is None else bool(terminal)
            retry, cannot_retry = _next_retry(self.now, due)
            if due is not None and self.now >= due:
                state, next_retry = "missed_lock", None
                action = (
                    "do not file late or create a scored mock; record the miss")
            elif terminal:
                state, next_retry = "terminal_failure", None
                action = (
                    "repair credentials/route, then retry before the deadline; "
                    "do not create a scored mock")
            elif cannot_retry:
                state, next_retry = "retryable_failure", iso(retry)
                action = (
                    "retry immediately before the deadline; the scheduled retry is too late")
            else:
                state, next_retry = "retryable_failure", iso(retry)
                action = "retry on the next refresh; keep other entrants' successes"
            row.update({
                "state": state,
                "last_error": err,
                "next_retry": next_retry,
                "required_action": action,
                "alert": True,
            })
            return copy.deepcopy(row)

    def entrant_withheld(self, round_id, entrant, *, estimated_spend=None):
        with self._lock:
            key = self._entrant_key(round_id, entrant)
            if key not in self._entrants:
                raise KeyError(f"entrant-round was not queued: {round_id}/{entrant}")
            row = self._entrants[key]
            retry, risks = _next_retry(self.now, row["next_deadline"])
            if estimated_spend is not None:
                row["estimated_spend"] = round(float(estimated_spend), 6)
            row.update({
                "state": "queued",
                "next_retry": iso(retry),
                "last_error": "withheld by the per-run spend ceiling",
                "required_action": (
                    "raise the spend ceiling or retry manually before the deadline"
                    if risks else
                    "leave queued for the next refresh; nearest deadlines run first"),
                "alert": True,
            })
            return copy.deepcopy(row)

    def entrant_missed(self, round_id, entrant, *, lock_at, deadline,
                       route=None, estimated_spend=0.0,
                       error="no valid forecast existed at the filing deadline"):
        self.entrant_queued(
            round_id, entrant, lock_at=lock_at, deadline=deadline, route=route,
            estimated_spend=estimated_spend,
            action="do not file late; record the miss")
        with self._lock:
            row = self._entrants[self._entrant_key(round_id, entrant)]
            row.update({
                "state": "missed_lock",
                "last_error": error_text(error),
                "next_retry": None,
                "required_action": (
                    "do not file late or create a scored mock; record the miss"),
                "alert": True,
            })
            return copy.deepcopy(row)

    # -- output ----------------------------------------------------------

    def carry_entrant_states(self, document):
        """Carry first-pass filing states into a no-filing rebuild pass.

        ``refresh.yml`` intentionally runs refresh twice.  The second pass
        resolves and rebuilds the site with ``SSA_SKIP_FILING=1``; overwriting
        the first pass's provider failures with an empty table would make the
        operator report green precisely because the retry-suppression guard
        worked.  Only entrant rows are carried: source checks really do run
        again and should report their newest evidence.
        """
        rows = (document or {}).get("entrant_round_states") or []
        with self._lock:
            for original in rows:
                row = copy.deepcopy(original)
                key = self._entrant_key(row["round_id"], row["entrant"])
                self._entrants.setdefault(key, row)

    def as_dict(self):
        with self._lock:
            sources = [copy.deepcopy(self._sources[k])
                       for k in sorted(self._sources)]
            entrants = [copy.deepcopy(self._entrants[k])
                        for k in sorted(self._entrants)]
        for row in sources:
            if row["state"] not in SOURCE_STATES:
                raise AssertionError(f"undeclared source state: {row['state']}")
        for row in entrants:
            if row["state"] not in ENTRANT_STATES:
                raise AssertionError(f"undeclared entrant state: {row['state']}")
        source_counts = {s: sum(r["state"] == s for r in sources)
                         for s in sorted(SOURCE_STATES)}
        entrant_counts = {s: sum(r["state"] == s for r in entrants)
                          for s in sorted(ENTRANT_STATES)}
        alerts = []
        for kind, rows, names in (
                ("source", sources, ("source",)),
                ("entrant_round", entrants, ("round_id", "entrant"))):
            for row in rows:
                if not row["alert"]:
                    continue
                alerts.append({
                    "kind": kind,
                    "id": "/".join(row[n] for n in names),
                    "state": row["state"],
                    "last_error": row["last_error"],
                    "required_action": row["required_action"],
                })
        alerts.sort(key=lambda a: (a["kind"], a["id"], a["state"]))
        return {
            "generated_at": iso(self.now),
            "source_states": sources,
            "entrant_round_states": entrants,
            "counts": {"sources": source_counts,
                       "entrant_rounds": entrant_counts},
            "alerts": alerts,
        }

    def summary(self):
        """Sorted text rendered solely from :meth:`as_dict`."""
        doc = self.as_dict()
        lines = [
            f"operator status {doc['generated_at']}",
            f"sources={len(doc['source_states'])} "
            f"entrant_rounds={len(doc['entrant_round_states'])} "
            f"alerts={len(doc['alerts'])}",
        ]
        for row in doc["source_states"]:
            evidence = "; ".join(row["evidence"]) or "none"
            lines.append(
                f"SOURCE {row['source']} state={row['state']} "
                f"attempts={row['attempts']} next_retry={row['next_retry'] or '-'} "
                f"route={row['route'] or '-'} spend=${row['estimated_spend']:.6f} "
                f"lock={row['next_lock'] or '-'} "
                f"deadline={row['next_deadline'] or '-'} "
                f"error={row['last_error'] or '-'} "
                f"action={row['required_action']} evidence={evidence}")
        for row in doc["entrant_round_states"]:
            evidence = "; ".join(row["evidence"]) or "none"
            lines.append(
                f"ENTRANT {row['round_id']}/{row['entrant']} "
                f"state={row['state']} attempts={row['attempts']} "
                f"route={row['route'] or '-'} spend=${row['estimated_spend']:.6f} "
                f"lock={row['next_lock'] or '-'} "
                f"deadline={row['next_deadline'] or '-'} "
                f"error={row['last_error'] or '-'} "
                f"action={row['required_action']} evidence={evidence}")
        return lines

    def write(self, path):
        """Atomically write the deterministic JSON document."""
        doc = self.as_dict()
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as handle:
            json.dump(doc, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, path)
        return doc
