"""Free taxis in Singapore's Downtown Core at Friday 23:00 (LTA via data.gov.sg).

  GET https://api.data.gov.sg/v1/transport/taxi-availability
      ?date_time=YYYY-MM-DDTHH:MM:SS          (Singapore time, keyless)

returns a GeoJSON FeatureCollection with one feature: `properties.timestamp`,
`properties.taxi_count` and a MultiPoint of [lon, lat], one per taxi
available for hire. The endpoint serves any past minute, so the archive can
be filled after the fact; those bodies are labelled `backfill`.

The rules are the approved Data Card `sg-taxi-downtown-fri2300@v2`
(docs/data-cards/sg-taxi-downtown-fri2300.json, sha256
e1d4489709326c3bbdb8f71ad205cadcab92ce036014deb5939da7b97efe2b8e). SSA both
sets and resolves this metric; no publisher releases it.

**Metric definition v1** (card target.calculation):
(1) request <Friday>T23:00:00; (2) valid if properties.timestamp is no more
than 120 s before the minute and taxi_count is at least 30% of the median
island-wide count of the last 4 valid Friday snapshots in SSA's archive;
(3) otherwise 23:01, 23:02, 23:05, 23:10 in turn, each judged against its own
minute; (4) count the points inside DOWNTOWN CORE of the pinned URA boundary
(ray casting, holes excluded, first polygon in file order).

Two implementation readings, recorded in docs/sources/singapore-taxis.md:
a snapshot whose point list does not match taxi_count ("points missing",
policy (4)) or that does not parse is invalid, never repaired; and while the
archive holds fewer than 4 earlier valid Fridays the median is taken over
those it has (a round cannot be posted then anyway, policy (2)).

**Zero point** `mean4_rms13` = baselines mean_k(k=4, lag=0, search=6) over
valid Fridays, sd = RMS of the same rule's errors over the 13 most recent
valid target Fridays (searching 26 back). Shown, unranked: persistence with
its own 13-week RMS spread.

**Capture**: `.github/workflows/sg-timed-capture.yml`, Friday 23:30 SGT, one
retry at Saturday 01:00 SGT; a run also fills up to 12 missing past Fridays
of the last 36 (the reference needs about 32 weeks of archive).
"""
import json
import sys
from datetime import timedelta

from .. import baselines
from . import sg_common

NAME = "lta-rt/taxi"
DATASET = "d_e25662f1a062dd046453926aa284ba64"
ENDPOINT = "https://api.data.gov.sg/v1/transport/taxi-availability"
AREA = "DOWNTOWN CORE"
MINUTES = ("23:00", "23:01", "23:02", "23:05", "23:10")
MAX_LAG = timedelta(seconds=120)
VALID_FRACTION = 0.30
MEDIAN_OF = 4

ZERO_POINT = {"name": "mean4_rms13", "family": "mean_k", "period": "week",
              "k": 4, "lag": 0, "search": 6, "digits": 2,
              "spread": {"rule": "rms", "n": 13, "search": 26}}
SHOWN = {"name": "persistence_rms13", "family": "mean_k", "period": "week",
         "k": 1, "lag": 0, "search": 1, "digits": 2,
         "spread": {"rule": "rms", "n": 13, "search": 26}}

NotPostable = baselines.NotPostable


def _median(xs):
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


class Taxi(sg_common.WeeklyMinute):
    name = NAME
    endpoint = ENDPOINT
    weekday = 4                                   # Friday
    minutes = MINUTES
    capture_delay = timedelta(minutes=30)         # Friday 23:30 SGT
    retry_delay = timedelta(hours=2)              # Saturday 01:00 SGT
    grace = timedelta(hours=13)                   # Saturday 12:00 SGT
    backfill_weeks = 36

    def parse(self, body):
        try:
            doc = json.loads(body)
        except (TypeError, ValueError) as e:
            raise ValueError(f"not JSON: {e}") from None
        if not isinstance(doc, dict) or doc.get("type") != "FeatureCollection":
            raise ValueError("not a FeatureCollection")
        feats = doc.get("features")
        if not isinstance(feats, list) or len(feats) != 1:
            raise ValueError(f"expected one feature, got {feats if feats is None else len(feats)}")
        f = feats[0]
        props = f.get("properties") or {}
        if "timestamp" not in props or "taxi_count" not in props:
            raise ValueError("properties.timestamp / taxi_count missing")
        geom = f.get("geometry") or {}
        if geom.get("type") != "MultiPoint":
            raise ValueError(f"geometry is {geom.get('type')!r}, not MultiPoint")
        pts = []
        for p in geom.get("coordinates") or []:
            if not isinstance(p, list) or len(p) < 2:
                raise ValueError(f"bad point {p!r}")
            pts.append((float(p[0]), float(p[1])))
        count = props["taxi_count"]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError(f"taxi_count {count!r} is not a count")
        return {"timestamp": sg_common.parse_sg_stamp(props["timestamp"]),
                "stamp": props["timestamp"], "taxi_count": count, "points": pts}

    def judge(self, parsed, minute, prior):
        ts = parsed["timestamp"]
        if minute - ts > MAX_LAG:
            return (f"stale: timestamp {parsed['stamp']} is "
                    f"{int((minute - ts).total_seconds())} s before the minute")
        if len(parsed["points"]) != parsed["taxi_count"]:
            return (f"points missing: {len(parsed['points'])} points for "
                    f"taxi_count {parsed['taxi_count']}")
        last = [o["island"] for o in prior[-MEDIAN_OF:]]
        if last:
            floor = VALID_FRACTION * _median(last)
            if parsed["taxi_count"] < floor:
                return (f"low: taxi_count {parsed['taxi_count']} below 30% of the "
                        f"median {_median(last):g} of the last {len(last)} valid Fridays")
        return None

    def measure(self, parsed):
        pa = sg_common.planning_areas(self.root)
        n = sum(1 for lon, lat in parsed["points"] if pa.locate(lon, lat) == AREA)
        return {"value": float(n), "island": parsed["taxi_count"],
                "timestamp": parsed["stamp"]}


def source(root=None):
    return Taxi(root)


def lock_instant(friday):
    """Wednesday 23:00 SGT, 48 hours before the target minute."""
    t = Taxi()
    return t.target_instant(t.check_day(friday)) - timedelta(hours=48)


def lock_reference(friday, root=None, rows=None):
    """The ranked and shown references from the archive as it stood at lock:
    only Fridays before the target, only bodies fetched by the lock.

    Raises NotPostable when policy (2) fails (fewer than 4 valid Fridays in
    the last 6) or the spread cannot find 13 past targets.
    """
    t = Taxi(root)
    day = t.check_day(friday)
    lock = lock_instant(day)
    outcomes = t.evaluate(rows, before=day, cutoff=lock)
    series = t.series(outcomes)
    ranked = baselines.reference(ZERO_POINT, series, day)
    try:
        shown = baselines.reference(SHOWN, series, day)
    except NotPostable as e:
        shown = {"method": SHOWN["name"], "unavailable": str(e)}
    return {"zero_point": ZERO_POINT["name"], "lock_at": sg_common.iso(lock),
            "ranked": ranked, "shown": shown,
            "archive_targets": len(outcomes),
            "valid_targets": len(series),
            "backfilled": sorted(d.isoformat() for d, o in outcomes.items()
                                 if o.get("backfill"))}


def resolve(friday, now, root=None, rows=None):
    return Taxi(root).resolve(friday, now, rows)


def capture(now=None, get=None, sleep=None, backfill=None, root=None):
    return Taxi(root).capture(now=now, get=get, sleep=sleep, backfill=backfill)


if __name__ == "__main__":                                   # pragma: no cover
    if sys.argv[1:2] == ["capture"]:
        try:
            print(json.dumps(capture(), indent=2))
        except Exception as e:                               # noqa: BLE001
            sys.exit(f"sg_taxi capture failed: {type(e).__name__}: {e}")
    elif sys.argv[1:2] == ["reference"] and len(sys.argv) == 3:
        print(json.dumps(lock_reference(sys.argv[2]), indent=2, default=str))
    elif sys.argv[1:2] == ["resolve"] and len(sys.argv) == 3:
        print(json.dumps(resolve(sys.argv[2], sg_common.archive_store.utcnow()),
                         indent=2, default=str))
    else:
        sys.exit("usage: python -m ssa.adapters.sg_taxi capture | reference <friday> "
                 "| resolve <friday>")
