"""Singapore COE Category A quota premium per bidding exercise (LTA via data.gov.sg).

  GET https://data.gov.sg/api/action/datastore_search
      ?resource_id=d_69b3380ad7e51aff3a7dcc84eba52b8a&limit=1000&offset=<0|1000|...>
  GET https://api-production.data.gov.sg/v2/public/api/datasets/
      d_69b3380ad7e51aff3a7dcc84eba52b8a/metadata          (lastUpdatedAt)

Keyless. Every row is one (month, bidding_no, vehicle_class) with quota,
bids_success, bids_received and premium, all as text.

The rules are the approved Data Card `sg-coe-cat-a-premium@v2`
(docs/data-cards/sg-coe-cat-a-premium.json, sha256
54eb7de69bb6c10b0f465dd0d9314a55cc514009d82d1ee2c932a8fc4cf9e0e6).

**Archive.** `sources/coe/<YYYY-MM-DDTHHMM>Z/`: the metadata body and every
datastore page exactly as served, each hashed into `sources/coe/fetches.json`
under the capture instant. A capture is taken only when a rule needs one,
so the 6-hourly refresh costs no request most of the time (`due`):

- the *lock capture*: the first refresh inside the 12 hours before a lock
  (lock = exercise start - 1 h, normally Monday 11:00 SGT);
- the *resolution capture*: every refresh from close + 2 h until a capture
  holds the exercise's Category A row, up to close + 48 h.

A capture whose pages disagree on `total`, or that holds fewer rows than
`total`, is refused (the dataset moved between pages).

**Schedule.** `SCHEDULE` is LTA's published calendar (OneMotoring, "Schedule
of COE Open Bidding Exercises From January to December 2026", Annex A).
2027 is not posted yet; an exercise not in `SCHEDULE` has no lock and no
round (card: the generator refuses to post a round without a published
schedule entry).

**Zero point** `mean1_rms26` = baselines mean_k(k=1, lag=0) on an exercise
index (two per month): the previous exercise's premium, sd = RMS of the 26
most recent exercise-to-exercise changes, all from the lock capture. Policy
(5): 27 consecutive exercises are needed, so a gap makes it NotPostable.
Shown, unranked: the mean of the last two exercises.

**Resolution.** The first capture at or after close + 2 h whose rows hold the
exercise's Category A row with a premium > 0; none by close + 48 h cancels.
A later change to the row never changes a resolved round.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

from .. import baselines
from .. import capture as archive_store
from .. import provenance
from . import sg_common

NAME = "coe"
DATASET = "d_69b3380ad7e51aff3a7dcc84eba52b8a"
DATASTORE = "https://data.gov.sg/api/action/datastore_search"
METADATA = (f"https://api-production.data.gov.sg/v2/public/api/datasets/"
            f"{DATASET}/metadata")
PAGE = 1000
CATEGORY = "Category A"
FIELDS = ("month", "bidding_no", "vehicle_class", "quota", "bids_success",
          "bids_received", "premium")

LOCK_BEFORE_START = timedelta(hours=1)
LOCK_CAPTURE_WINDOW = timedelta(hours=12)
MAX_LOCK_SNAPSHOT_AGE = timedelta(hours=24)
RESOLVE_AFTER_CLOSE = timedelta(hours=2)
GRACE_AFTER_CLOSE = timedelta(hours=48)

ZERO_POINT = {"name": "mean1_rms26", "family": "mean_k", "period": "index",
              "k": 1, "lag": 0, "search": 1, "digits": 0,
              "spread": {"rule": "rms", "n": 26, "search": 26}}
SHOWN = {"name": "mean2_rms26", "family": "mean_k", "period": "index",
         "k": 2, "lag": 0, "search": 2, "digits": 0,
         "spread": {"rule": "rms", "n": 26, "search": 26}}

NotPostable = baselines.NotPostable

# (month, bidding_no) -> (start, end), Singapore wall clock, from LTA's 2026
# schedule PDF (fetched 2026-11-09T01:00Z): Monday 12 noon to Wednesday 4pm,
# except February (2), Monday to Friday (Chinese New Year on Tue/Wed), and
# June (1), Tuesday to Thursday (Vesak Day in lieu on Monday).
_S = {
    "2026-01": ("01-05", "01-07", "01-19", "01-21"),
    "2026-02": ("02-02", "02-04", "02-16", "02-20"),
    "2026-03": ("03-02", "03-04", "03-16", "03-18"),
    "2026-04": ("04-06", "04-08", "04-20", "04-22"),
    "2026-05": ("05-04", "05-06", "05-18", "05-20"),
    "2026-06": ("06-02", "06-04", "06-15", "06-17"),
    "2026-07": ("07-06", "07-08", "07-20", "07-22"),
    "2026-08": ("08-03", "08-05", "08-17", "08-19"),
    "2026-09": ("09-07", "09-09", "09-21", "09-23"),
    "2026-10": ("10-05", "10-07", "10-19", "10-21"),
    "2026-11": ("11-02", "11-04", "11-16", "11-18"),
    "2026-12": ("12-07", "12-09", "12-21", "12-23"),
}
SCHEDULE = {}
for _m, (_s1, _e1, _s2, _e2) in _S.items():
    _y = _m[:4]
    SCHEDULE[(_m, "1")] = (f"{_y}-{_s1}T12:00", f"{_y}-{_e1}T16:00")
    SCHEDULE[(_m, "2")] = (f"{_y}-{_s2}T12:00", f"{_y}-{_e2}T16:00")
SCHEDULE_SOURCE = ("https://onemotoring.lta.gov.sg/content/dam/onemotoring/Buying/"
                   "PDF/COE/COE_Bidding_Schedule_for_Year_2026.pdf")


# ---------------------------------------------------------------- exercises

def exercise_index(month, bidding_no):
    """Consecutive integers for consecutive exercises (two a month), so the
    `index` axis sees a missing exercise as a gap."""
    y, m = int(month[:4]), int(month[5:7])
    b = int(bidding_no)
    if b not in (1, 2):
        raise ValueError(f"bidding_no {bidding_no!r} is not 1 or 2")
    return (y * 12 + m - 1) * 2 + b - 1


def exercise_of(index):
    y, rem = divmod(index // 2, 12)
    return f"{y:04d}-{rem + 1:02d}", str(index % 2 + 1)


def _sgt(stamp):
    day, hm = stamp.split("T")
    return sg_common.sgt(day, int(hm[:2]), int(hm[3:5]))


def window(month, bidding_no):
    """(start, close) in UTC from the published schedule; KeyError if the
    exercise is not on it."""
    s, e = SCHEDULE[(month, str(bidding_no))]
    return _sgt(s), _sgt(e)


def lock_instant(month, bidding_no):
    return window(month, bidding_no)[0] - LOCK_BEFORE_START


def resolution_instant(month, bidding_no):
    return window(month, bidding_no)[1] + RESOLVE_AFTER_CLOSE


def grace_end(month, bidding_no):
    return window(month, bidding_no)[1] + GRACE_AFTER_CLOSE


# ---------------------------------------------------------------- parsing

def _int(v, what):
    s = str(v).replace(",", "").strip()        # thousands commas (owner doc)
    if not s.lstrip("-").isdigit():
        raise ValueError(f"{what} {v!r} is not an integer")
    return int(s)


def parse_page(body):
    """One datastore page -> (records, total, offset). Fails loud."""
    try:
        doc = json.loads(body)
    except (TypeError, ValueError) as e:
        raise ValueError(f"coe page is not JSON: {e}") from None
    if not isinstance(doc, dict) or doc.get("success") is not True:
        raise ValueError("coe page does not say success: true")
    res = doc.get("result") or {}
    recs, total = res.get("records"), res.get("total")
    if not isinstance(recs, list) or isinstance(total, bool) or not isinstance(total, int):
        raise ValueError("coe page lacks result.records / result.total")
    for r in recs:
        missing = [f for f in FIELDS if f not in r]
        if missing:
            raise ValueError(f"coe row lacks {missing}: {r!r}"[:200])
    return recs, total, res.get("offset", 0)


def table(records):
    """{(month, bidding_no, vehicle_class): row with integer counts}.
    A repeated key raises; a row whose premium does not parse is kept with
    premium None (policy (4) treats it as missing)."""
    out = {}
    for r in records:
        key = (str(r["month"]), str(r["bidding_no"]), str(r["vehicle_class"]))
        if key in out:
            raise ValueError(f"coe table repeats {key}")
        row = {"quota": None, "bids_success": None, "bids_received": None,
               "premium": None}
        for f in row:
            try:
                row[f] = _int(r[f], f)
            except ValueError:
                row[f] = None
        out[key] = row
    return out


def category_series(tab, category=CATEGORY):
    """{exercise index: premium} for one category, premium > 0 only."""
    out = {}
    for (m, b, c), row in tab.items():
        if c == category and row["premium"] and row["premium"] > 0:
            out[exercise_index(m, b)] = row["premium"]
    return out


# ---------------------------------------------------------------- archive

ROOT = sg_common.ROOT


def _archive_dir():
    return os.path.join(ROOT, "sources", NAME)


def _fetches_path():
    return os.path.join(_archive_dir(), "fetches.json")


def load_fetches():
    try:
        with open(_fetches_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_fetches(rows):
    os.makedirs(_archive_dir(), exist_ok=True)
    tmp = _fetches_path() + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rows, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, _fetches_path())


def capture_key(now):
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H%MZ")


def page_url(offset):
    return f"{DATASTORE}?resource_id={DATASET}&limit={PAGE}&offset={offset}"


def capture(now=None, get=None, reason=None):
    """Fetch metadata + every datastore page now and file them, write-once
    under the capture instant. Returns the fetches row."""
    import requests
    get = get or requests.get
    now = (now or archive_store.utcnow()).astimezone(timezone.utc)
    key = capture_key(now)
    rows = load_fetches()
    if key in rows:
        return rows[key]
    meta = archive_store.get(METADATA, name=NAME, accept="application/json", get=get)
    pages, offset, total = [], 0, None
    while True:
        got = archive_store.get(page_url(offset), name=NAME,
                                accept="application/json", get=get)
        recs, t, _ = parse_page(got[0])
        if total is None:
            total = t
        elif t != total:
            raise ValueError(f"coe total moved from {total} to {t} between pages; "
                             "not a consistent capture")
        pages.append((offset, got))
        offset += PAGE
        if offset >= total or not recs:
            break
    held = sum(len(parse_page(g[0])[0]) for _, g in pages)
    if held != total:
        raise ValueError(f"coe capture holds {held} rows of {total}")
    tab = table([r for _, g in pages for r in parse_page(g[0])[0]])
    try:
        updated = json.loads(meta[0])["data"]["lastUpdatedAt"]
    except (ValueError, KeyError, TypeError):
        raise ValueError("coe metadata lacks data.lastUpdatedAt") from None
    at = sg_common.iso(now)
    files = {}
    for part, (url, body) in [("metadata", (meta[1], meta[0]))] + \
            [(f"page-{off:05d}", (g[1], g[0])) for off, g in pages]:
        block = provenance.record(NAME, url, body, fetched_at=at, ext="json",
                                  day=f"{key}/{part}")
        files[part] = {"url": url, "sha256": block["sha256"],
                       "bytes": block["bytes"], "file": block["file"]}
    newest = max(exercise_index(m, b) for (m, b, _c) in tab)
    row = {"fetched_at": at, "files": files, "rows": total,
           "last_updated_at": updated,
           "newest_exercise": "%s (%s)" % exercise_of(newest)}
    if reason:
        row["reason"] = reason
    rows[key] = row
    _save_fetches(rows)
    return row


def read_capture(key, rows=None):
    """The parsed table of one capture; every file's sha256 is checked."""
    rows = rows if rows is not None else load_fetches()
    row = rows[key]
    recs = []
    for part in sorted(p for p in row["files"] if p.startswith("page-")):
        f = row["files"][part]
        with open(os.path.join(ROOT, f["file"]), "rb") as fh:
            body = fh.read()
        if provenance.digest(body) != f["sha256"]:
            raise RuntimeError(f"coe: {f['file']} does not match its sha256")
        recs.extend(parse_page(body)[0])
    return table(recs), row


# ---------------------------------------------------------------- when to fetch

def _has_row(tab, month, bidding_no):
    r = tab.get((month, str(bidding_no), CATEGORY))
    return bool(r and r["premium"] and r["premium"] > 0)


def due(now, rows=None, reader=read_capture):
    """Why this refresh must capture, or None. One reason per run."""
    rows = rows if rows is not None else load_fetches()
    stamps = sorted((sg_common.parse_iso(r["fetched_at"]), k) for k, r in rows.items())
    for (m, b) in sorted(SCHEDULE):
        lock = lock_instant(m, b)
        if lock - LOCK_CAPTURE_WINDOW <= now <= lock:
            if not any(lock - LOCK_CAPTURE_WINDOW <= t <= lock for t, _ in stamps):
                return f"lock capture for {m} ({b})"
        start, end = resolution_instant(m, b), grace_end(m, b)
        if start <= now <= end:
            after = [k for t, k in stamps if start <= t <= end]
            if not any(_has_row(reader(k, rows)[0], m, b) for k in after):
                return f"resolution capture for {m} ({b})"
    return None


def capture_if_due(now=None, get=None):
    now = (now or archive_store.utcnow()).astimezone(timezone.utc)
    why = due(now)
    if why is None:
        return {"captured": False, "reason": "no lock or resolution window open"}
    return {"captured": True, "reason": why, "row": capture(now, get, reason=why)}


# ---------------------------------------------------------------- rounds

def lock_reference(month, bidding_no, rows=None, reader=read_capture):
    """Both references from the capture archived at lock (no older than 24 h)."""
    rows = rows if rows is not None else load_fetches()
    lock = lock_instant(month, bidding_no)
    held = [(sg_common.parse_iso(r["fetched_at"]), k) for k, r in rows.items()
            if sg_common.parse_iso(r["fetched_at"]) <= lock]
    if not held:
        raise NotPostable(f"no COE capture at or before the lock of {month} ({bidding_no})")
    at, key = max(held)
    if lock - at > MAX_LOCK_SNAPSHOT_AGE:
        raise NotPostable(f"the newest capture before the lock is {sg_common.iso(at)}, "
                          f"more than {MAX_LOCK_SNAPSHOT_AGE} old")
    tab, row = reader(key, rows)
    series = category_series(tab)
    target = exercise_index(month, bidding_no)
    series = {i: v for i, v in series.items() if i < target}
    ranked = baselines.reference(ZERO_POINT, series, target)
    try:
        shown = baselines.reference(SHOWN, series, target)
    except NotPostable as e:
        shown = {"method": SHOWN["name"], "unavailable": str(e)}
    for ref in (ranked, shown):
        if "window" in ref:
            ref["exercises"] = ["%s (%s)" % exercise_of(i) for i in ref["window"]]
    return {"zero_point": ZERO_POINT["name"], "lock_at": sg_common.iso(lock),
            "capture": key, "fetched_at": row["fetched_at"],
            "files": {p: f["sha256"] for p, f in row["files"].items()},
            "ranked": ranked, "shown": shown}


def resolve(month, bidding_no, now, rows=None, reader=read_capture):
    rows = rows if rows is not None else load_fetches()
    start, end = resolution_instant(month, bidding_no), grace_end(month, bidding_no)
    now = now.astimezone(timezone.utc)
    if now < start:
        return {"status": "pending", "reason": f"resolves from {sg_common.iso(start)}"}
    tried = []
    for t, key in sorted((sg_common.parse_iso(r["fetched_at"]), k) for k, r in rows.items()):
        if not (start <= t <= min(now, end)):
            continue
        tab, row = reader(key, rows)
        r = tab.get((month, str(bidding_no), CATEGORY))
        if r and r["premium"] and r["premium"] > 0:
            return {"status": "resolved", "value": float(r["premium"]),
                    "quota": r["quota"], "bids_received": r["bids_received"],
                    "capture": key, "fetched_at": row["fetched_at"],
                    "files": {p: f["sha256"] for p, f in row["files"].items()}}
        tried.append(key)
    if now > end:
        return {"status": "cancelled", "tried": tried,
                "reason": f"no capture held a Category A premium by {sg_common.iso(end)}"}
    return {"status": "pending", "tried": tried,
            "reason": f"inside the grace window until {sg_common.iso(end)}"}


if __name__ == "__main__":                                   # pragma: no cover
    if sys.argv[1:2] == ["capture"]:
        try:
            print(json.dumps(capture_if_due(), indent=2))
        except Exception as e:                               # noqa: BLE001
            sys.exit(f"sg_coe capture failed: {type(e).__name__}: {e}")
    elif sys.argv[1:2] == ["reference"] and len(sys.argv) == 4:
        print(json.dumps(lock_reference(sys.argv[2], sys.argv[3]), indent=2))
    elif sys.argv[1:2] == ["resolve"] and len(sys.argv) == 4:
        print(json.dumps(resolve(sys.argv[2], sys.argv[3], archive_store.utcnow()),
                         indent=2))
    else:
        sys.exit("usage: python -m ssa.adapters.sg_coe capture | reference <YYYY-MM> <1|2> "
                 "| resolve <YYYY-MM> <1|2>")
