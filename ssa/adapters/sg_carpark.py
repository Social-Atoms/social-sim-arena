"""HDB carpark occupancy in Clementi at Saturday 20:00 (HDB via data.gov.sg).

  GET https://api.data.gov.sg/v1/transport/carpark-availability
      ?date_time=YYYY-MM-DDTHH:MM:SS          (Singapore time, keyless)

returns items[0].timestamp and carpark_data[] with carpark_number,
update_datetime (no zone: Singapore time) and carpark_info[] {lot_type,
total_lots, lots_available}, all as text. Past minutes are served, so the
archive can be filled after the fact; those bodies are labelled `backfill`.

The rules are the approved Data Card `sg-hdb-carpark-clementi-sat2000@v2`
(docs/data-cards/sg-hdb-carpark-clementi-sat2000.json, sha256
332008aa802b666dd10fbcbeb4ad63412c43586c0b5481be5fc5721becb30dd2). SSA both
sets and resolves this metric.

**Metric definition v1** (card target.calculation): (1) request
<Saturday>T20:00:00; (2) valid if items is non-empty, items[0].timestamp is no
more than 120 s before the minute, and the body has at least 1,900 car
('C') rows; (3) keep 'C' rows with total_lots > 0 and update_datetime no more
than 60 minutes before items[0].timestamp, every 'C' row of a carpark
counting; (4) keep carparks the pinned HDB Carpark Information table places
in CLEMENTI (SVY21 -> WGS84, pinned URA boundary): 48 carparks;
(5) occupancy = 100 x sum(total - available) / sum(total). Fallback minutes
20:01, 20:02, 20:05, 20:10. Missing-data policy: fewer than 36 of the 48
pinned carparks passing in the resolution snapshot cancels the round; a feed
carpark not in the pinned table is excluded and counted; a row whose
lots_available exceeds total_lots is dropped and counted.

**Zero point** `mean1_rms13` = baselines mean_k(k=1, lag=0): the same minute a
week earlier, sd = RMS of its own errors over the 13 most recent valid
Saturdays (searching 26 back); policy (4): not posted with fewer than 10
valid Saturdays in the last 13. Shown, unranked: the 4-week mean.

**Capture**: `.github/workflows/sg-timed-capture.yml`, Saturday 20:30 SGT,
one retry at 22:00 SGT, plus up to 12 missing past Saturdays of the last 30.
"""
import json
import sys
from datetime import timedelta

from .. import baselines
from . import sg_common

NAME = "lta-rt/carpark-hdb"
DATASET = "d_ca933a644e55d34fe21f28b8052fac63"
ENDPOINT = "https://api.data.gov.sg/v1/transport/carpark-availability"
AREA = "CLEMENTI"
MINUTES = ("20:00", "20:01", "20:02", "20:05", "20:10")
MAX_LAG = timedelta(seconds=120)
MIN_CAR_ROWS = 1900
STALE = timedelta(minutes=60)
PINNED_COUNT = 48
MIN_COVERAGE = 36
POST_MIN_VALID, POST_LOOKBACK = 10, 13

ZERO_POINT = {"name": "mean1_rms13", "family": "mean_k", "period": "week",
              "k": 1, "lag": 0, "search": 1, "digits": 3,
              "spread": {"rule": "rms", "n": 13, "search": 26}}
SHOWN = {"name": "mean4_rms13", "family": "mean_k", "period": "week",
         "k": 4, "lag": 0, "search": 4, "digits": 3,
         "spread": {"rule": "rms", "n": 13, "search": 26}}

NotPostable = baselines.NotPostable
_PINNED = {}


def clementi(root=None):
    key = root or sg_common.ROOT
    if key not in _PINNED:
        got = sg_common.carparks_in(AREA, root)
        if len(got) != PINNED_COUNT:
            raise sg_common.PinMismatch(
                f"{len(got)} HDB carparks fall in {AREA}, the card pins {PINNED_COUNT}")
        _PINNED[key] = got
    return _PINNED[key]


def _int(v, what):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        raise ValueError(f"{what} {v!r} is not an integer") from None


class Carpark(sg_common.WeeklyMinute):
    name = NAME
    endpoint = ENDPOINT
    weekday = 5                                   # Saturday
    minutes = MINUTES
    capture_delay = timedelta(minutes=30)         # Saturday 20:30 SGT
    retry_delay = timedelta(hours=2)              # Saturday 22:00 SGT
    grace = timedelta(hours=13)                   # Sunday 09:00 SGT
    backfill_weeks = 30

    def parse(self, body):
        try:
            doc = json.loads(body)
        except (TypeError, ValueError) as e:
            raise ValueError(f"not JSON: {e}") from None
        items = doc.get("items") if isinstance(doc, dict) else None
        if not isinstance(items, list):
            raise ValueError("no items list")
        if not items:
            return {"empty": True}
        it = items[0]
        if "timestamp" not in it or not isinstance(it.get("carpark_data"), list):
            raise ValueError("items[0] lacks timestamp / carpark_data")
        rows = []
        for c in it["carpark_data"]:
            no, up = c.get("carpark_number"), c.get("update_datetime")
            if no is None or up is None or not isinstance(c.get("carpark_info"), list):
                raise ValueError(f"carpark row without number/update/info: {c!r}"[:200])
            for i in c["carpark_info"]:
                if i.get("lot_type") != "C":
                    continue
                rows.append((str(no), sg_common.parse_sg_stamp(up),
                             _int(i.get("total_lots"), "total_lots"),
                             _int(i.get("lots_available"), "lots_available")))
        return {"empty": False, "timestamp": sg_common.parse_sg_stamp(it["timestamp"]),
                "stamp": it["timestamp"], "rows": rows}

    def judge(self, parsed, minute, prior):
        if parsed["empty"]:
            return "empty: items is empty"
        if minute - parsed["timestamp"] > MAX_LAG:
            return (f"stale: timestamp {parsed['stamp']} is "
                    f"{int((minute - parsed['timestamp']).total_seconds())} s before the minute")
        if len(parsed["rows"]) < MIN_CAR_ROWS:
            return f"short: {len(parsed['rows'])} car rows, fewer than {MIN_CAR_ROWS}"
        return None

    def measure(self, parsed):
        pinned = set(clementi(self.root))
        known = sg_common.hdb_carparks(self.root)
        ts = parsed["timestamp"]
        occ = tot = 0
        passed, stale, zero, over, unknown = set(), 0, 0, [], set()
        for no, up, total, avail in parsed["rows"]:
            if no not in known:
                unknown.add(no)
                continue
            if no not in pinned:
                continue
            if total <= 0:
                zero += 1
                continue
            if ts - up > STALE:
                stale += 1
                continue
            if avail > total:
                over.append(no)
                continue
            occ += total - avail
            tot += total
            passed.add(no)
        if len(passed) < MIN_COVERAGE:
            raise self.Cancel(f"coverage: {len(passed)} of {PINNED_COUNT} pinned "
                              f"{AREA} carparks pass, fewer than {MIN_COVERAGE}")
        return {"value": round(100.0 * occ / tot, 3), "lots": tot, "occupied": occ,
                "carparks": len(passed), "stale_rows": stale, "zero_total_rows": zero,
                "dropped_available_over_total": sorted(over),
                "feed_carparks_not_pinned": len(unknown),
                "timestamp": parsed["stamp"]}


def source(root=None):
    return Carpark(root)


def lock_instant(saturday):
    """Thursday 20:00 SGT, 48 hours before the target minute."""
    c = Carpark()
    return c.target_instant(c.check_day(saturday)) - timedelta(hours=48)


def lock_reference(saturday, root=None, rows=None):
    c = Carpark(root)
    day = c.check_day(saturday)
    lock = lock_instant(day)
    outcomes = c.evaluate(rows, before=day, cutoff=lock)
    series = c.series(outcomes)
    recent = [day - timedelta(days=7 * i) for i in range(1, POST_LOOKBACK + 1)]
    valid = sum(1 for d in recent if d in series)
    if valid < POST_MIN_VALID:
        raise NotPostable(f"{valid} valid Saturdays among the last {POST_LOOKBACK}, "
                          f"fewer than {POST_MIN_VALID} (policy (4))")
    ranked = baselines.reference(ZERO_POINT, series, day)
    try:
        shown = baselines.reference(SHOWN, series, day)
    except NotPostable as e:
        shown = {"method": SHOWN["name"], "unavailable": str(e)}
    return {"zero_point": ZERO_POINT["name"], "lock_at": sg_common.iso(lock),
            "ranked": ranked, "shown": shown, "valid_in_last_13": valid,
            "backfilled": sorted(d.isoformat() for d, o in outcomes.items()
                                 if o.get("backfill"))}


def resolve(saturday, now, root=None, rows=None):
    return Carpark(root).resolve(saturday, now, rows)


def capture(now=None, get=None, sleep=None, backfill=None, root=None):
    return Carpark(root).capture(now=now, get=get, sleep=sleep, backfill=backfill)


if __name__ == "__main__":                                   # pragma: no cover
    if sys.argv[1:2] == ["capture"]:
        try:
            print(json.dumps(capture(), indent=2))
        except Exception as e:                               # noqa: BLE001
            sys.exit(f"sg_carpark capture failed: {type(e).__name__}: {e}")
    elif sys.argv[1:2] == ["reference"] and len(sys.argv) == 3:
        print(json.dumps(lock_reference(sys.argv[2]), indent=2, default=str))
    elif sys.argv[1:2] == ["resolve"] and len(sys.argv) == 3:
        print(json.dumps(resolve(sys.argv[2], sg_common.archive_store.utcnow()),
                         indent=2, default=str))
    else:
        sys.exit("usage: python -m ssa.adapters.sg_carpark capture | reference "
                 "<saturday> | resolve <saturday>")
