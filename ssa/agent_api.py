"""Route A: turn a real round into the contract envelope, and a reply into a
forecast record.

`ssa/participants.py` decides *whether and where* to call. This decides *what
is sent and what comes back*. `docs/agent-api.md` is the contract both
implement; `schema/agent-api-request.schema.json` and its response twin are
the machine-readable form.

Why this reuses `harness._ask` instead of its own client
--------------------------------------------------------
The request body is the JSON envelope itself and the reply body is the
forecast object; `harness._call_agent` sends the one and returns the other as
text. `harness._ask` takes the body as a string and the reply parser as an
argument, so a participant call is the existing runner with a different string,
a different transport and a different parser, and it inherits, for free and
identically:

- **idempotency by input hash**, which the contract requires: `prompt_hash`
  over the envelope is the same key our own models use, so a second run over an
  unchanged round does not call the endpoint again;
- **the reply log**, so a run that dies after the response arrives does not
  ask the participant to compute it twice;
- **the buy window and the insurance tail**, so an endpoint that is down at
  72h is retried until 30 minutes before the deadline and then stops.

Writing a second client would have given the season two retry policies and two
definitions of the window, and the participants' would be the one nobody
exercises weekly.

What is deliberately *not* inherited
------------------------------------
- **No mock, ever.** `harness.forecast` can file a labelled placeholder when a
  key is missing and `SSA_ALLOW_MOCK` is set. There is no such thing here: a
  placeholder filed under someone else's entrant id is us inventing their
  forecast. A participant who cannot be reached has no forecast that round.
- **No standby.** See `participants.route`.
"""
import json

from . import batches
from . import harness
from . import participants
from . import profile_round
from . import ranking_round

SCHEMA_VERSION = "ssa-agent-api-v2"

# An endpoint that fails this many calls in a row within one run is not called
# again in that run: the remaining rounds are recorded as failures without a
# request, and the next six-hourly refresh starts it fresh. This bounds what a
# dead or hanging endpoint can cost the run (one round's timeout, a few times)
# and what a run can cost the endpoint (a burst of twenty calls into an outage).
MAX_CONSECUTIVE_FAILURES = 3
_failures_this_run = {}

BOARD = {"profile_energy": "profile", "ranking_list": "ranking"}


def target_type(r):
    """The contract's name for this round's answer shape.

    Read from the round when it says so, otherwise derived from the same
    predicates the scorers use, so a round cannot be described to a participant
    as one shape and scored as another.
    """
    declared = r.get("target_type")
    if declared:
        return declared
    if profile_round.is_profile(r):
        return "profile_energy"
    if ranking_round.is_ranking(r):
        return "ranking_list"
    return "continuous_normal"


def _question(r):
    return (r.get("question") or r.get("title")
            or f"Forecast {r.get('series') or r['round_id']}.")


def _context(r, history, profile_history=None, ranking_history=None):
    """The frozen inputs a participant may see, and nothing else.

    Everything here is already public in `site/data.json`, and everything here
    is frozen at the same instant the persistence null is (`batches.freeze_at`)
    because it comes from the same round object the null was computed on. An
    endpoint that reads only this is answering the question every other entrant
    was asked.
    """
    ctx = {}
    nulls = r.get("baselines") or {}
    if nulls.get("persistence"):
        ctx["persistence"] = nulls["persistence"].get("mean")
    if history:
        ctx["history"] = [{"date": p["date"], "value": p["value"]}
                          for p in history[-24:]]
    if profile_history:
        ctx["history_by_cell"] = {
            cell: [{"date": p["date"], "value": p["value"]} for p in pts[-12:]]
            for cell, pts in sorted(profile_history.items())}
    if ranking_history:
        ctx["recent_weeks"] = ranking_history[-4:]
    return ctx


def build_envelope(entrant, r, history=None, profile_history=None,
                   ranking_history=None):
    """The request body's `content`, as a dict.

    `request_id` is derived from (entrant, round), not random. The contract
    calls for idempotency by entrant, round and input hash; a random id would
    make every retry look like a new question to an endpoint that dedupes on
    it, which is the one thing a participant is told they may rely on.
    """
    tt = target_type(r)
    deadline = batches.effective_deadline(r["lock_at"])
    block = {
        "round_id": r["round_id"],
        "board_id": BOARD.get(tt, "topline"),
        "target_type": tt,
        "question": _question(r),
        "unit": r.get("unit") or "points",
        # The participant deadline, not the arena's internal lock. See the
        # field's description in the request schema.
        "lock_at": deadline.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "context": _context(r, history, profile_history, ranking_history),
    }
    if tt == "profile_energy":
        block["cells"] = list(profile_round.cells_for(r))
    elif tt == "ranking_list":
        spec = ranking_round.spec_for(r)
        block["ranking"] = {"length": spec["length"]}
        if spec.get("items"):
            block["ranking"]["items"] = list(spec["items"])
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": f"{entrant}:{r['round_id']}",
        "round": block,
        "optional_crosstabs": [],
    }


def _payload(text):
    """The decoded response envelope, or a raised reason a participant can act
    on.

    The reply is JSON, not prose: an endpoint built against this contract emits
    an object, and accepting a bare number or a sentence here would let a
    non-conforming endpoint score while a conforming one is held to the schema.
    """
    try:
        obj = json.loads(text)
    except (TypeError, ValueError) as err:
        raise ValueError(
            f"response was not JSON ({err}); the contract returns an object "
            f"matching schema/agent-api-response.schema.json") from err
    if not isinstance(obj, dict):
        raise ValueError("response JSON was not an object")
    got = obj.get("schema_version")
    if got != SCHEMA_VERSION:
        raise ValueError(
            f"response schema_version was {got!r}, expected {SCHEMA_VERSION!r}")
    fc = obj.get("forecast")
    if not isinstance(fc, dict):
        raise ValueError("response has no `forecast` object")
    return fc


def parse_scalar(text):
    """A scalar round's answer: {mean, sd} or {quantiles}. Both are scored
    with CRPS (`scoring.crps_forecast`), so which one an endpoint sends is
    its own choice."""
    return harness.answer(_payload(text), "forecast")


def parse_profile(text, cells):
    fc = _payload(text)
    prof = fc.get("profile")
    if not isinstance(prof, dict):
        raise ValueError("profile round needs a `profile` object of cells")
    missing = [c for c in cells if c not in prof]
    if missing:
        # All cells or none: the energy score is a norm over the whole vector,
        # and filling a hole would reward a view the endpoint did not express.
        raise ValueError(
            f"profile is missing {len(missing)} of {len(cells)} cells: "
            f"{missing[:4]}")
    return harness.parse_profile(json.dumps(prof), cells)


def parse_ranking(text, spec):
    fc = _payload(text)
    order = fc.get("ranking")
    if not isinstance(order, list):
        raise ValueError("ranking round needs a `ranking` array")
    return harness.parse_ranking(json.dumps({"ranking": order}), spec)


def forecast(entrant, r, history=None, previous=None, profile_history=None,
             ranking_history=None):
    """One forecast dict from a participant's endpoint, or a raised failure.

    Mirrors `harness.forecast`'s cost layers -- an unchanged input hash returns
    the file on disk untouched, a logged reply is replayed rather than re-asked
    -- because `_ask` owns both and this passes through it.
    """
    ok, why = participants.callable_now(entrant)
    if not ok:
        raise RuntimeError(f"{entrant}: {why}")

    tt = target_type(r)
    envelope = build_envelope(entrant, r, history, profile_history,
                              ranking_history)
    prompt = json.dumps(envelope, sort_keys=True, separators=(",", ":"))
    ih = harness.prompt_hash(entrant, prompt)

    if previous and f"in={ih}" in (previous.get("notes") or "") \
            and not (previous.get("notes") or "").startswith("MOCK"):
        return previous

    if tt == "profile_energy":
        cells = list(profile_round.cells_for(r))
        parse = lambda text: parse_profile(text, cells)          # noqa: E731
        key = "profile"          # the forecast schema's key, same as our own
    elif tt == "ranking_list":
        spec = ranking_round.spec_for(r)
        parse = lambda text: parse_ranking(text, spec)           # noqa: E731
        key = "ranking"
    else:
        parse = parse_scalar
        key = "topline"

    if _failures_this_run.get(entrant, 0) >= MAX_CONSECUTIVE_FAILURES:
        raise RuntimeError(
            f"{entrant}: not called; {MAX_CONSECUTIVE_FAILURES} consecutive "
            "failures this run. Retried by the next refresh.")
    try:
        top, via, ih, replayed = harness._ask(entrant, prompt, previous,
                                              r["round_id"], parse=parse)
    except Exception:
        _failures_this_run[entrant] = _failures_this_run.get(entrant, 0) + 1
        raise
    _failures_this_run[entrant] = 0
    if top is None:
        return previous
    return {
        "round_id": r["round_id"],
        "entrant": entrant,
        key: top,
        "notes": (f"filed={harness.filed_stamp()}, "
                  f"{harness.model_id(entrant)}, agent-api {SCHEMA_VERSION}, "
                  f"via=participant"
                  f"{', replayed from the reply log' if replayed else ''}"
                  f"; in={ih}"),
    }
