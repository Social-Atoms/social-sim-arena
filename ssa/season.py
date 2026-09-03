"""Semantic checks for the human-reviewed season manifest.

JSON syntax is not a publication gate.  A syntactically valid round can still
lock after it releases, ask for a scalar while declaring profile cells, point
at a source whose rights are unresolved, duplicate a target under a new id, or
name a resolution rule that cannot identify a frozen observation.  This module
checks those meanings before :mod:`ssa.bundle` is allowed to publish them.

The validator is deliberately read-only.  Candidate generation still writes
only ``questions/candidates/`` and a human still moves accepted rounds into the
season.  Validation proves that review produced a coherent manifest; it never
turns a candidate into an approved round.
"""
import re
from datetime import datetime, timedelta, timezone

from . import inventory, profile_round, ranking_round
from .series import SERIES

TARGET_TYPES = {"continuous_normal", "profile_energy", "ranking_list"}
REQUIRED = (
    "round_id", "tracker", "series", "question", "unit", "release_at",
    "release_estimated", "lock_at", "resolve", "target_type",
)
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

# These targets are not registry time series.  They still have an audited
# resolution source: both election outcomes use certified public results, and
# ranking kinds name the adapter that produces the scored observation.
SPECIAL_SERIES_SOURCE = {
    "generic_ballot_margin": "certified_election_results",
    "house_seats": "certified_election_results",
    "wiki_top10_en": "wikipedia",
}
RANKING_KIND_SOURCE = {
    "wiki_top10": "wikipedia",
    "trends_basket": "trends_basket",
}

# Rounds hand-reviewed and frozen before the source inventory acquired a rights
# gate.  Grandfathering is by exact round id, not source: it preserves the
# published season while ensuring that one more round from a blocked source
# still fails.  Adding an exception is a visible policy decision in code;
# removing one once its round is retired, or once its source is decided, is
# safe.
#
# Empty since 2026-09-03, when issue #68 read all five blocked sources' terms
# and decided every one of them.  Three came back approved on condition their
# retrieved bodies stay unpublished (AAII, Michigan's party cut, the YouGov
# crosstabs), so their rounds now pass the gate on their own merits rather than
# on an exception.  The other two were withdrawn -- The Conference Board bars
# extraction into a database whether or not anything is republished, and Penta
# bars automated retrieval and automated analysis -- and their rounds went with
# them.  Nothing is left to grandfather, and an empty set is the honest way to
# say that: the mechanism stays, so the next frozen round from an undecided
# source is a one-line, reviewable addition.
GRANDFATHERED_RIGHTS = frozenset()

# A resolution must name a stable observation, not merely contain prose.  The
# accepted anchors are the actual forms used by the reviewed season: a dated
# archive/snapshot, a publisher artifact, a release-date adjusted average, or
# certified results.  Vague moving targets are refused first and explicitly.
_MOVING_RESOLUTION = re.compile(
    r"\b(latest|most recent|current value|whatever is available|fallback)\b",
    re.IGNORECASE,
)
_RESOLUTION_ANCHOR = re.compile(
    r"(archive|snapshot|published|publication|release date|release post|"
    r"report|pdf|workbook|dashboard|screenshot|official|certified|"
    r"results page|endpoint|api|sca\.isr|conference-board)",
    re.IGNORECASE,
)


def _timestamp(value):
    if not isinstance(value, str) or not STAMP.fullmatch(value):
        raise ValueError("must be UTC at second precision with a trailing Z")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc)


def rounds_from(document):
    """Return the round list or raise for an unusable document envelope."""
    if isinstance(document, dict):
        rounds = document.get("rounds")
    else:
        rounds = document
    if not isinstance(rounds, list):
        raise ValueError("season document must be a list or an object with `rounds`")
    return rounds


def source_for(round_data):
    """The audited source key for one round, raising when it is unknowable."""
    target = round_data.get("target_type")
    rid = round_data.get("round_id", "?")
    if target == "ranking_list":
        kind = (round_data.get("ranking") or {}).get("kind")
        source = RANKING_KIND_SOURCE.get(kind)
        if source is None:
            raise ValueError(f"{rid}: ranking kind {kind!r} has no audited source")
        return source
    if target == "profile_energy":
        cells = profile_round.cells_for(round_data)
        sources = {SERIES[c]["source"] for c in cells}
        if len(sources) != 1:
            raise ValueError(
                f"{rid}: profile cells cross source contracts: {sorted(sources)}")
        representative = SERIES.get(round_data.get("series"))
        if representative is None:
            raise ValueError(
                f"{rid}: representative profile `series` is not registered")
        if representative["source"] not in sources:
            raise ValueError(
                f"{rid}: representative profile `series` uses source "
                f"{representative['source']!r}, not {next(iter(sources))!r}")
        return next(iter(sources))
    sid = round_data.get("series")
    if sid in SERIES:
        return SERIES[sid]["source"]
    source = SPECIAL_SERIES_SOURCE.get(sid)
    if source is None:
        raise ValueError(f"{rid}: series {sid!r} is neither registered nor audited")
    return source


def target_key(round_data):
    """Identity of the scored observation, independent of ``round_id``."""
    target = round_data["target_type"]
    if target == "profile_energy":
        identity = tuple(sorted(round_data.get("cells") or ()))
        release = round_data.get("release_at", "")[:10]
    elif target == "ranking_list":
        spec = round_data.get("ranking") or {}
        identity = (spec.get("kind"), spec.get("week_start"),
                    spec.get("week_end"), tuple(spec.get("items") or ()))
        # The structured week is the observation. Changing only the announced
        # release timestamp cannot turn the same completed week into a second
        # target.
        release = None
    else:
        identity = round_data.get("series")
        # A time correction on one publication day is still the same release,
        # not a second observation entrants may be scored against twice.
        release = round_data.get("release_at", "")[:10]
    return target, identity, release


def _shape_problems(round_data):
    rid = round_data.get("round_id", "?")
    target = round_data.get("target_type")
    problems = []
    try:
        if target == "continuous_normal":
            extra = [k for k in ("cells", "ranking") if k in round_data]
            if extra:
                problems.append(
                    f"{rid}: scalar round carries shape field(s) {', '.join(extra)}")
        elif target == "profile_energy":
            profile_round.cells_for(round_data)
            if "ranking" in round_data:
                problems.append(f"{rid}: profile round also carries `ranking`")
        elif target == "ranking_list":
            ranking_round.spec_for(round_data)
            if "cells" in round_data:
                problems.append(f"{rid}: ranking round also carries `cells`")
        else:
            problems.append(f"{rid}: unknown target_type {target!r}")
    except (KeyError, TypeError, ValueError) as err:
        problems.append(str(err))
    return problems


def _behavior_week(round_data, source):
    """Measured (start, end) for a behavioral round's declared week.

    Ranking rounds carry structured dates.  The two legacy scalar families and
    the Trends profile predate that block but freeze the measured week in their
    id.  Deriving the boundary from that contract avoids pretending every
    Wikipedia round has one magic release-minus-lock offset.
    """
    target = round_data.get("target_type")
    if target == "ranking_list":
        spec = ranking_round.spec_for(round_data)
        return (datetime.strptime(spec["week_start"], "%Y-%m-%d").date(),
                datetime.strptime(spec["week_end"], "%Y-%m-%d").date())
    if source not in ("wikipedia", "trends", "trends_basket"):
        return None
    match = re.search(r"20\d\d-\d\d-\d\d", round_data.get("round_id", ""))
    if match is None:
        raise ValueError("behavioral round id does not declare its week end")
    end = datetime.strptime(match.group(), "%Y-%m-%d").date()
    expected_weekday = 6 if source == "wikipedia" else 5
    if end.weekday() != expected_weekday:
        names = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                 "Saturday", "Sunday")
        raise ValueError(
            f"declared week ends on {names[end.weekday()]}, not "
            f"{names[expected_weekday]}")
    return end - timedelta(days=6), end


def _schedule_problem(round_data, source):
    rid = round_data.get("round_id", "?")
    try:
        release = _timestamp(round_data.get("release_at"))
        lock = _timestamp(round_data.get("lock_at"))
    except ValueError as err:
        return f"{rid}: invalid schedule: {err}"
    if lock >= release:
        return f"{rid}: invalid schedule: lock_at must be before release_at"

    try:
        measured = _behavior_week(round_data, source)
    except ValueError as err:
        return f"{rid}: invalid schedule: {err}"
    if measured is not None:
        start, end = measured
        if lock.date() >= start:
            return (f"{rid}: invalid schedule: behavioral round locks "
                    f"{lock.date()} but measured week starts {start}")
        if release.date() < end:
            return (f"{rid}: invalid schedule: releases {release.date()} before "
                    f"measured week ends {end}")
        return None

    if source == "civiqs" and release.weekday() != 4:
        return (f"{rid}: invalid schedule: Civiqs release is "
                f"{release.strftime('%A')}; its registered displayed-value "
                "contract samples Friday")

    hours = (release - lock).total_seconds() / 3600.0
    if round_data.get("tracker") == "midterm_special":
        # One-off election questions deliberately span the campaign; their
        # reviewed dates are the contract, and ordering is the semantic check.
        return None
    expected = 48.0
    if hours != expected:
        return (f"{rid}: invalid schedule: release is {hours:g}h after lock; "
                f"this source contract requires {expected:g}h")
    return None


def validate_document(document):
    """Return every semantic publication problem in deterministic order."""
    try:
        rounds = rounds_from(document)
    except ValueError as err:
        return [str(err)]
    problems = []
    ids = {}
    targets = {}
    rights = inventory.rights_table()

    for index, round_data in enumerate(rounds):
        if not isinstance(round_data, dict):
            problems.append(f"rounds[{index}]: must be an object")
            continue
        rid = round_data.get("round_id") or f"rounds[{index}]"
        missing = [key for key in REQUIRED if key not in round_data]
        if missing:
            problems.append(f"{rid}: missing required field(s): {', '.join(missing)}")
            continue
        if not isinstance(round_data["release_estimated"], bool):
            problems.append(f"{rid}: release_estimated must be true or false")
        for key in ("round_id", "tracker", "series", "question", "unit", "resolve"):
            if not isinstance(round_data[key], str) or not round_data[key].strip():
                problems.append(f"{rid}: `{key}` must be a non-empty string")

        if rid in ids:
            problems.append(f"{rid}: duplicate round_id (first at rounds[{ids[rid]}])")
        else:
            ids[rid] = index

        problems.extend(_shape_problems(round_data))
        if round_data.get("target_type") not in TARGET_TYPES:
            continue
        try:
            source = source_for(round_data)
        except (KeyError, TypeError, ValueError) as err:
            problems.append(str(err))
            continue

        state = rights.get(source, inventory.UNRESOLVED)
        if state not in inventory.GENERATING_RIGHTS and \
                rid not in GRANDFATHERED_RIGHTS:
            problems.append(
                f"{rid}: source {source!r} rights are {state!r}; only approved "
                "sources may be published")

        schedule = _schedule_problem(round_data, source)
        if schedule:
            problems.append(schedule)

        rule = round_data["resolve"].strip()
        if _MOVING_RESOLUTION.search(rule):
            problems.append(
                f"{rid}: resolution rule names a moving target; pin a first "
                "print, dated archive, or certified result")
        elif not _RESOLUTION_ANCHOR.search(rule):
            problems.append(
                f"{rid}: resolution rule does not name a deterministic "
                "publisher artifact or archived first print")

        key = target_key(round_data)
        if key in targets:
            problems.append(
                f"{rid}: duplicate target already published as {targets[key]} "
                f"({key[0]}, same observation and release)")
        else:
            targets[key] = rid
    return problems


def require_valid(document):
    """Raise ``ValueError`` with all semantic problems, otherwise return rounds."""
    problems = validate_document(document)
    if problems:
        raise ValueError("season is not publishable:\n  " + "\n  ".join(problems))
    return rounds_from(document)
