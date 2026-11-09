"""Shared pieces of the Singapore sources (Urban Track, five cards).

- the clock: Singapore time (UTC+8, no daylight saving);
- the pinned reference files the taxi and carpark metrics are computed
  against: URA Master Plan 2019 Planning Area Boundary (No Sea) and HDB
  Carpark Information, both keyless data.gov.sg files archived once for the
  season under `sources/sg-ref/` and refused if their bytes change;
- point-in-polygon in file order (cards sg-taxi-downtown-fri2300@v2 and
  sg-hdb-carpark-clementi-sat2000@v2, target.calculation) and the SVY21 to
  WGS84 conversion the carpark card states;
- `MinuteArchive`: the write-once record of past-minute snapshots,
  `sources/<name>/<YYYY-MM-DD>/<HHMM>.json` keyed by the Singapore-time
  minute *requested*, with `sources/<name>/fetches.json` holding each body's
  fetch instant, URL, sha256 and size. `ssa/capture.DailyArchive` keys a
  capture by its UTC fetch day, which cannot hold the five fallback minutes
  of one target; this is the same contract keyed by the minute instead.

Rights for all four data.gov.sg datasets: Singapore Open Data Licence v1.0
and the data.gov.sg API Terms of Service (inventory rows `sg_coe`, `sg_taxi`,
`sg_carpark_hdb`); the honest User-Agent is `ssa.capture.UA`.
"""
import json
import math
import os
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .. import capture as archive_store
from .. import provenance

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SGT = ZoneInfo("Asia/Singapore")

LICENCE_NOTICE = (
    "Contains information from {datasets} accessed on {date} from data.gov.sg "
    "which is made available under the terms of the Singapore Open Data "
    "Licence version 1.0 https://data.gov.sg/open-data-licence")

# Pinned for the season (cards: "committed and pinned by that hash"). The
# boundary hash is the one both cards cite; the HDB table's three pages are
# the 2026-11-09 card-stage fetch the carpark card measured (2,273 rows).
BOUNDARY = {
    "dataset": "d_4765db0e87b9c86336792efe8a1f7a66",
    "title": "URA Master Plan 2019 Planning Area Boundary (No Sea)",
    "file": "sources/sg-ref/ura-mp2019-planning-area-nosea/2026-11-09.geojson",
    "sha256": "f23856251b467089f788d0fff72ef5a38e753f21aa69b4352401d7ed50d380cc",
}
HDB_INFO = {
    "dataset": "d_23f946fa557947f93a8043bbef41dd09",
    "title": "HDB Carpark Information",
    "files": (
        ("sources/sg-ref/hdb-carpark-info/2026-11-09-p0.json",
         "43541fbbd606c8e53830cd19ba679e12cea472540c426614eb3a04058f3c569a"),
        ("sources/sg-ref/hdb-carpark-info/2026-11-09-p1.json",
         "d2cdfcee4b39631618344322c7bec5d2b0b8b772094614fc922e62889c704ae6"),
        ("sources/sg-ref/hdb-carpark-info/2026-11-09-p2.json",
         "36a36709bf50eba31b2f26b386962769b2d6355b036ccf22781128b83993f5b9"),
    ),
    "rows": 2273,
}


class PinMismatch(RuntimeError):
    """A pinned reference file is missing or its bytes changed."""


def read_pinned(rel, sha256, root=None):
    path = os.path.join(root or ROOT, rel)
    try:
        with open(path, "rb") as f:
            body = f.read()
    except OSError as e:
        raise PinMismatch(f"pinned file {rel} is missing: {e}") from None
    if provenance.digest(body) != sha256:
        raise PinMismatch(f"pinned file {rel} does not match sha256 {sha256[:16]}...; "
                          "the season's boundary is fixed and is not re-read")
    return body


# ---------------------------------------------------------------- the clock

def sgt(day, hh, mm, ss=0):
    """A Singapore wall-clock instant as an aware UTC datetime."""
    if isinstance(day, str):
        day = datetime.strptime(day[:10], "%Y-%m-%d").date()
    return datetime.combine(day, time(hh, mm, ss), SGT).astimezone(timezone.utc)


def parse_sg_stamp(s):
    """'2026-11-07T19:59:36+08:00' -> aware UTC; a stamp with no zone is
    Singapore time (carpark update_datetime)."""
    dt = datetime.fromisoformat(str(s))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SGT)
    return dt.astimezone(timezone.utc)


iso = archive_store.iso
parse_iso = archive_store.parse_iso


# ---------------------------------------------------------------- geometry

def _ring_contains(x, y, ring):
    inside, j = False, len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


class PlanningAreas:
    """The pinned planning-area polygons, in file order.

    `locate(lon, lat)` returns PLN_AREA_N of the first feature whose polygon
    contains the point (ray casting, holes excluded), or None: "A point on a
    boundary goes to the first matching polygon in file order" (taxi card,
    target.calculation (4)).
    """

    def __init__(self, body):
        doc = json.loads(body)
        self.areas = []
        for f in doc["features"]:
            g = f["geometry"]
            polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
            xs = [p[0] for poly in polys for p in poly[0]]
            ys = [p[1] for poly in polys for p in poly[0]]
            self.areas.append((f["properties"]["PLN_AREA_N"], polys,
                               (min(xs), min(ys), max(xs), max(ys))))
        self.names = [a[0] for a in self.areas]
        self._seen = {}

    @classmethod
    def pinned(cls, root=None):
        return cls(read_pinned(BOUNDARY["file"], BOUNDARY["sha256"], root))

    def locate(self, lon, lat):
        key = (lon, lat)
        if key not in self._seen:
            self._seen[key] = self._locate(lon, lat)
        return self._seen[key]

    def _locate(self, lon, lat):
        for name, polys, bb in self.areas:
            if not (bb[0] <= lon <= bb[2] and bb[1] <= lat <= bb[3]):
                continue
            for poly in polys:
                if _ring_contains(lon, lat, poly[0]) and \
                        not any(_ring_contains(lon, lat, h) for h in poly[1:]):
                    return name
        return None


_AREAS = {}


def planning_areas(root=None):
    key = root or ROOT
    if key not in _AREAS:
        _AREAS[key] = PlanningAreas.pinned(key)
    return _AREAS[key]


# SVY21 (EPSG:3414) inverse transverse Mercator, as the carpark card states:
# origin 1.366666 N 103.833333 E, false northing 38,744.572 m, false easting
# 28,001.642 m, scale 1, WGS84 ellipsoid.
_A = 6378137.0
_F = 1 / 298.257223563
_O_LAT, _O_LON, _O_N, _O_E, _K = 1.366666, 103.833333, 38744.572, 28001.642, 1.0
_B = _A * (1 - _F)
_E2 = 2 * _F - _F * _F
_E4, _E6 = _E2 * _E2, _E2 ** 3
_A0 = 1 - _E2 / 4 - 3 * _E4 / 64 - 5 * _E6 / 256
_A2 = 3 / 8 * (_E2 + _E4 / 4 + 15 * _E6 / 128)
_A4 = 15 / 256 * (_E4 + 3 * _E6 / 4)
_A6 = 35 * _E6 / 3072


def _meridian(lat):
    r = math.radians(lat)
    return _A * (_A0 * r - _A2 * math.sin(2 * r) + _A4 * math.sin(4 * r)
                 - _A6 * math.sin(6 * r))


def svy21_to_wgs84(northing, easting):
    """(lat, lon) in degrees for an SVY21 (N, E) pair in metres."""
    mprime = _meridian(_O_LAT) + (northing - _O_N) / _K
    n = (_A - _B) / (_A + _B)
    n2, n3, n4 = n * n, n ** 3, n ** 4
    g = _A * (1 - n) * (1 - n2) * (1 + 9 * n2 / 4 + 225 * n4 / 64) * (math.pi / 180)
    sigma = mprime * math.pi / (180 * g)
    lat_p = (sigma + (3 * n / 2 - 27 * n3 / 32) * math.sin(2 * sigma)
             + (21 * n2 / 16 - 55 * n4 / 32) * math.sin(4 * sigma)
             + 151 * n3 / 96 * math.sin(6 * sigma)
             + 1097 * n4 / 512 * math.sin(8 * sigma))
    s = math.sin(lat_p)
    rho = _A * (1 - _E2) / (1 - _E2 * s * s) ** 1.5
    v = _A / math.sqrt(1 - _E2 * s * s)
    psi, t = v / rho, math.tan(lat_p)
    eprime = easting - _O_E
    x = eprime / (_K * v)
    lat = (lat_p - t / (_K * rho) * (x * eprime / 2)
           + t / (_K * rho) * (x ** 3 * eprime / 24)
           * (-4 * psi ** 2 + 9 * psi * (1 - t * t) + 12 * t * t))
    sec = 1 / math.cos(lat_p)
    lon = math.radians(_O_LON) + sec * x - sec * (x ** 3 / 6) * (psi + 2 * t * t)
    return math.degrees(lat), math.degrees(lon)


def hdb_carparks(root=None):
    """{car_park_no: record} from the pinned HDB Carpark Information pages."""
    out = {}
    for rel, sha in HDB_INFO["files"]:
        doc = json.loads(read_pinned(rel, sha, root))
        for rec in doc["result"]["records"]:
            out[rec["car_park_no"]] = rec
    if len(out) != HDB_INFO["rows"]:
        raise PinMismatch(f"HDB Carpark Information holds {len(out)} carparks, "
                          f"the pin says {HDB_INFO['rows']}")
    return out


def carparks_in(area, root=None):
    """Sorted car_park_no of the pinned HDB carparks inside `area`."""
    pa = planning_areas(root)
    keep = []
    for no, rec in hdb_carparks(root).items():
        lat, lon = svy21_to_wgs84(float(rec["y_coord"]), float(rec["x_coord"]))
        if pa.locate(lon, lat) == area:
            keep.append(no)
    return sorted(keep)


# ---------------------------------------------------------------- the archive

class MinuteArchive:
    """Write-once past-minute snapshots: `sources/<name>/<day>/<HHMM>.json`.

    The key is the Singapore-time minute requested (`2026-12-11T23:00`).
    Every 200 body is filed as served, valid or not (the cards: "archives
    every body fetched, valid or not"); a key already held costs no request,
    and a file on disk without its fetches row is refused, as in
    ssa/capture.DailyArchive.
    """

    def __init__(self, name, root=None):
        self.name = name
        self.root = root or ROOT
        self.archive = os.path.join(self.root, "sources", name)
        self.fetches = os.path.join(self.archive, "fetches.json")

    @staticmethod
    def key(day, hhmm):
        return f"{day}T{hhmm}"

    def path(self, key):
        day, hhmm = key.split("T")
        return os.path.join(self.archive, day, hhmm.replace(":", "") + ".json")

    def load(self):
        try:
            with open(self.fetches) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def save(self, rows):
        os.makedirs(self.archive, exist_ok=True)
        tmp = self.fetches + ".tmp"
        with open(tmp, "w") as f:
            json.dump(rows, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, self.fetches)

    def capture(self, key, fetch, now=None, extra=None):
        """File the body `fetch()` returns under `key`, once. Returns the row."""
        rows = self.load()
        path = self.path(key)
        if key in rows and os.path.exists(path):
            return rows[key]
        if os.path.exists(path):
            raise RuntimeError(f"{self.name}: {path} exists without a fetches row; "
                               "refusing to overwrite a capture whose fetch time "
                               "is unknown")
        got = fetch()
        body, url = got[0], got[1]
        now = (now or archive_store.utcnow()).astimezone(timezone.utc)
        day, hhmm = key.split("T")
        rel_day = f"{day}/{hhmm.replace(':', '')}"
        if os.path.abspath(provenance.archive_path(self.name, rel_day, "json")) \
                != os.path.abspath(path):
            raise RuntimeError(f"{self.name}: the archive {self.archive} is not "
                               "where provenance files it; refusing a split record")
        block = provenance.record(self.name, url, body, fetched_at=iso(now),
                                  ext="json", day=rel_day)
        row = {"fetched_at": block["fetched_at"], "url": url,
               "sha256": block["sha256"], "bytes": block["bytes"],
               "file": block["file"]}
        headers = getattr(got, "headers", None)
        if headers:
            row["headers"] = dict(headers)
        row.update(extra or {})
        rows[key] = row
        self.save(rows)
        return row

    def read(self, key, rows=None):
        rows = rows if rows is not None else self.load()
        row = rows[key]
        with open(os.path.join(self.root, row["file"]), "rb") as f:
            body = f.read()
        if provenance.digest(body) != row["sha256"]:
            raise RuntimeError(f"{self.name}: {row['file']} does not match its "
                               "recorded sha256; refusing a changed archive")
        return body, row


# ---------------------------------------------------------------- weekly minute

class WeeklyMinute:
    """One SSA-defined metric read at a fixed weekly minute, with fallbacks.

    Subclasses give the endpoint, the minute list and three hooks:
    `parse(body)` (raise ValueError on a schema change), `judge(parsed,
    minute, prior)` -> None if valid or the reason it is not, and
    `measure(parsed)` -> the value and its parts, raising `Cancel` when a
    valid snapshot still cannot settle the round (coverage rules).

    The chain is evaluated oldest target first, because a snapshot's validity
    can depend on earlier valid ones (the taxi 30%-of-median rule).
    """

    name = None
    endpoint = None
    weekday = None              # Monday = 0
    minutes = ()                # "HH:MM", first is the target minute
    capture_delay = timedelta(minutes=30)
    retry_delay = None
    grace = timedelta(hours=13)  # after the target minute; see docs
    backfill_weeks = 0
    backfill_per_run = 12
    spacing_seconds = 3.0

    class Cancel(Exception):
        pass

    def __init__(self, root=None):
        self.root = root

    def store(self):
        return MinuteArchive(self.name, self.root)

    # -- hooks
    def parse(self, body):                                # pragma: no cover
        raise NotImplementedError

    def judge(self, parsed, minute, prior):               # pragma: no cover
        raise NotImplementedError

    def measure(self, parsed):                            # pragma: no cover
        raise NotImplementedError

    # -- the clock
    def target_instant(self, day, hhmm=None):
        hh, mm = (hhmm or self.minutes[0]).split(":")
        return sgt(day, int(hh), int(mm))

    def check_day(self, day):
        if isinstance(day, str):
            day = datetime.strptime(day[:10], "%Y-%m-%d").date()
        if day.weekday() != self.weekday:
            raise ValueError(f"{day} is not the card's weekday")
        return day

    def capture_instant(self, day):
        return self.target_instant(day) + self.capture_delay

    def grace_end(self, day):
        return self.target_instant(day) + self.grace

    def latest_target(self, now):
        """The newest target day whose capture time has passed at `now`."""
        d = now.astimezone(SGT).date()
        d -= timedelta(days=(d.weekday() - self.weekday) % 7)
        while self.capture_instant(d) > now:
            d -= timedelta(days=7)
        return d

    def request_url(self, day, hhmm):
        return f"{self.endpoint}?date_time={day}T{hhmm}:00"

    # -- evaluating the archive
    # A body is immutable once filed (its sha256 is checked on every read),
    # so what it parses and measures to is memoised by that hash: the chain
    # is re-walked on every capture and every lock, and placing ~3,000 taxi
    # points in 55 polygons per snapshot is the slow part.
    _parsed = {}
    _measures = {}

    def _snapshot(self, key, rows):
        body, row = self.store().read(key, rows)
        memo = (self.name, row["sha256"])
        if memo not in self._parsed:
            try:
                self._parsed[memo] = (self.parse(body), None)
            except ValueError as e:
                self._parsed[memo] = (None, f"schema: {e}")
        parsed, why = self._parsed[memo]
        return parsed, row, why

    def _measured(self, sha, parsed):
        memo = (self.name, sha, self.root or ROOT)
        if memo not in self._measures:
            try:
                self._measures[memo] = ("ok", self.measure(parsed))
            except self.Cancel as e:
                self._measures[memo] = ("cancel", str(e))
        kind, got = self._measures[memo]
        if kind == "cancel":
            raise self.Cancel(got)
        return dict(got)

    def judge_target(self, day, rows, prior):
        """Outcome of one target day from the archive rows given.

        {"status": "valid", "minute", "value", ...} on the first valid minute
        in order; "cancelled" if every minute is archived and invalid, or the
        first valid one fails `measure`; "incomplete" when an earlier minute
        in the order is not archived yet (the next capture fills it).
        """
        tried = []
        for hhmm in self.minutes:
            key = MinuteArchive.key(day.isoformat(), hhmm)
            if key not in rows:
                return {"status": "incomplete", "missing": hhmm, "tried": tried}
            parsed, row, why = self._snapshot(key, rows)
            if why is None:
                why = self.judge(parsed, self.target_instant(day, hhmm), prior)
            if why is None:
                out = {"status": "valid", "minute": hhmm, "file": row["file"],
                       "sha256": row["sha256"], "fetched_at": row["fetched_at"]}
                if row.get("backfill"):
                    out["backfill"] = True
                try:
                    out.update(self._measured(row["sha256"], parsed))
                except self.Cancel as e:
                    return {"status": "cancelled", "minute": hhmm, "reason": str(e),
                            "file": row["file"], "tried": tried}
                return out
            tried.append({"minute": hhmm, "file": row["file"], "reason": why})
        return {"status": "cancelled", "tried": tried,
                "reason": "no valid snapshot among " + ", ".join(self.minutes)}

    def evaluate(self, rows=None, before=None, cutoff=None):
        """{target day: outcome}, oldest first, for every target in the
        archive strictly before `before` (a date), using only bodies fetched
        at or before `cutoff` (the archive as it stood at a lock)."""
        rows = rows if rows is not None else self.store().load()
        if cutoff is not None:
            rows = {k: r for k, r in rows.items() if parse_iso(r["fetched_at"]) <= cutoff}
        days = sorted({datetime.strptime(k[:10], "%Y-%m-%d").date() for k in rows})
        out, prior = {}, []
        for d in days:
            if before is not None and d >= before:
                continue
            if d.weekday() != self.weekday:
                continue
            res = self.judge_target(d, rows, prior)
            out[d] = res
            if res["status"] == "valid":
                prior.append(res)
        return out

    def series(self, outcomes):
        """{day: value} of the valid targets: what the zero point reads."""
        return {d: o["value"] for d, o in outcomes.items() if o["status"] == "valid"}

    # -- capturing
    def _fetch_minute(self, day, hhmm, get, now, extra=None):
        url = self.request_url(day.isoformat(), hhmm)
        return self.store().capture(
            MinuteArchive.key(day.isoformat(), hhmm),
            lambda: archive_store.get(url, name=self.name, accept="application/json",
                                      get=get),
            now=now() if callable(now) else now, extra=extra)

    def capture_target(self, day, get, now, sleep, extra=None):
        """Fetch the minutes of one target in order until one is valid.
        Returns the list of keys fetched (not those already held)."""
        fetched = []
        while True:
            rows = self.store().load()
            prior_outcomes = self.evaluate(rows, before=day)
            prior = [o for o in prior_outcomes.values() if o["status"] == "valid"]
            res = self.judge_target(day, rows, prior)
            if res["status"] != "incomplete":
                return fetched
            if fetched:
                sleep(self.spacing_seconds)
            self._fetch_minute(day, res["missing"], get, now, extra)
            fetched.append(res["missing"])

    def capture(self, now=None, get=None, sleep=None, backfill=None):
        """The scheduled run: up to `backfill_per_run` missing past targets,
        oldest first, labelled `backfill` (fetched later than the card's
        capture time), then the newest due target. Past targets come first
        because a target's validity can depend on them (the taxi median)."""
        import time as _time
        import requests
        get = get or requests.get
        sleep = sleep or _time.sleep
        clock = (lambda: now) if now is not None else archive_store.utcnow
        at = clock()
        target = self.latest_target(at)
        weeks = self.backfill_weeks if backfill is None else backfill
        rows = self.store().load()
        missing = []
        for i in range(weeks, 0, -1):
            d = target - timedelta(days=7 * i)
            if MinuteArchive.key(d.isoformat(), self.minutes[0]) not in rows:
                missing.append(d)
        done = {"target": target.isoformat(), "backfill": []}
        for d in missing[: self.backfill_per_run]:
            got = self.capture_target(d, get, clock, sleep, {"backfill": True})
            done["backfill"].append({"target": d.isoformat(), "fetched": got})
            sleep(self.spacing_seconds)
        late = at > self.grace_end(target)
        done["fetched"] = self.capture_target(target, get, clock, sleep,
                                              {"late": True} if late else None)
        return done

    # -- rounds
    def resolve(self, day, now, rows=None):
        """pending / resolved / cancelled for one target, from the archive.

        Before the capture time: pending. After it, the first valid minute in
        order resolves; every minute archived and invalid cancels. With a
        minute still missing (the capture and its retry failed to connect),
        pending until `grace_end`, then cancelled.
        """
        day = self.check_day(day)
        now = now.astimezone(timezone.utc)
        if now < self.capture_instant(day):
            return {"status": "pending",
                    "reason": f"captured from {iso(self.capture_instant(day))}"}
        rows = rows if rows is not None else self.store().load()
        prior = [o for o in self.evaluate(rows, before=day).values()
                 if o["status"] == "valid"]
        res = self.judge_target(day, rows, prior)
        if res["status"] == "valid":
            res["status"] = "resolved"
            return res
        if res["status"] == "cancelled":
            return res
        if now > self.grace_end(day):
            res.update(status="cancelled",
                       reason=f"minute {res['missing']} was never archived by "
                              f"{iso(self.grace_end(day))}")
            return res
        res["status"] = "pending"
        return res
