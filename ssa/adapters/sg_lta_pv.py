"""LTA DataMall monthly Passenger Volume files: train stations and bus stops.

**Not wired into any workflow.** DataMall needs an AccountKey, which SSA does
not have yet. This module is written and tested against fixtures shaped like
DataMall's documented response (API User Guide v6.11, sections 2.5 and 2.8,
Annex A); it reads the key from the environment variable LTA_API_KEY and
refuses to run without it. See docs/sources/singapore-passenger-volume.md for
the maintainer's registration steps and why archiving must start the day the
key exists: DataMall serves only the last three months.

  GET https://datamall2.mytransport.sg/ltaodataservice/PV/Train?Date=YYYYMM
  GET https://datamall2.mytransport.sg/ltaodataservice/PV/Bus?Date=YYYYMM
      header AccountKey: <LTA_API_KEY>
  -> {"value": [{"Link": "https://...s3.../transport_node_train_YYYYMM.zip?X-Amz-..."}]}

The link expires within minutes (guide: 15; owner doc measured 300 s), so the
zip is downloaded at once. It holds one CSV (Annex A):

  YEAR_MONTH, DAY_TYPE, TIME_PER_HOUR, PT_TYPE, PT_CODE, TOTAL_TAP_IN_VOLUME,
  TOTAL_TAP_OUT_VOLUME

The rules are the approved Data Cards `sg-lta-pv-train-jurong-east@v2`
(sha256 643a964dace5d09507d8660c97d921636f5110a0806f568ada10951704f5003e)
and `sg-lta-pv-bus-boon-lay-int@v2` (sha256
67073fb81a518a661b19fd9c0ac17e6f937332f6c664f3b9190632abaa84ba9f).

**Archive.** `sources/lta-pv/<train|bus>/<YYYYMM>.zip`, the zip as served,
write-once per month (different bytes for a held month are refused), with
url (signature removed), fetched_at, sha256 and bytes in
`sources/lta-pv/<kind>/fetches.json` and the manifest. The ingest refuses a
file whose YEAR_MONTH is not the expected month, whose PT_TYPE is not the
kind, or with too few PT_CODEs (train 150, bus 4,500); PT_CODE stays text,
and rows with an empty TIME_PER_HOUR are dropped and counted.

**Metric.** Sum TOTAL_TAP_IN_VOLUME over WEEKDAY rows of the code, divided by
the Monday-Friday dates of the month that are not gazetted public holidays
(the round freezes its list at posting; `PUBLIC_HOLIDAYS` is MOM's list),
rounded to a whole tap. The station code is the card's, 'EW24/NS1'. The
guide's own sample writes a merged interchange as 'EW14-NS26' (hyphen), so
the real file may say 'EW24-NS1': by owner decision (integration, 2026-11-08)
both spellings name the station, through `ALIASES` -- an announced alias in
the card's sense (policy (4)), not a guess at a renamed code. A bus stop
code is five digits kept as text; a file that drops the leading zero names
the same stop (`code_forms`). A file holding the same station under two
spellings cancels the round rather than adding them.

**Zero point** mean_k(k=1, lag=1) on months: the newest archived month at
lock, two before the target. Spread: an interim relative sd (train 5.43%,
bus 6.66%, LTA's system-wide two-month change 2023-2025) until 12 resolved
months exist in the archive, then rms_relative over the last 12.
"""
import csv
import io
import json
import os
import sys
import zipfile
from datetime import date, datetime, timedelta, timezone

from .. import baselines
from .. import capture as archive_store
from .. import provenance
from . import sg_common

ROOT = sg_common.ROOT
BASE = "https://datamall2.mytransport.sg/ltaodataservice/PV"
KEY_ENV = "LTA_API_KEY"
COLUMNS = ("YEAR_MONTH", "DAY_TYPE", "TIME_PER_HOUR", "PT_TYPE", "PT_CODE",
           "TOTAL_TAP_IN_VOLUME", "TOTAL_TAP_OUT_VOLUME")
WEEKDAY = "WEEKDAY"
CHECK_HOURS = range(6, 23)          # policy (2): zero tap-ins in any hour 06-22

KINDS = {
    "train": {"path": "Train", "pt_type": "TRAIN", "min_codes": 150,
              "code": "EW24/NS1", "series": "sg_pv_train_ew24ns1_weekday_tapin",
              "card": "sg-lta-pv-train-jurong-east@v2", "interim_cv": 0.0543},
    "bus": {"path": "Bus", "pt_type": "BUS", "min_codes": 4500,
            "code": "22009", "series": "sg_pv_bus_22009_weekday_tapin",
            "card": "sg-lta-pv-bus-boon-lay-int@v2", "interim_cv": 0.0666},
}
# Other spellings of a card's code, by kind and code (card policy (4): an
# alias announced before the lock). 'EW24-NS1' is the DataMall sample's
# hyphenated form of the card's 'EW24/NS1' (owner decision, 2026-11-08). A
# code LTA actually changes (a new line stage) is added here, with the date
# it was announced, before the first lock it applies to.
ALIASES = {"train": {"EW24/NS1": ("EW24-NS1",)}, "bus": {}}


def code_forms(kind, code):
    """Every spelling of `code` a file may carry, the card's own first.

    Train: the card's code plus its ALIASES. Bus: a stop code is five digits
    kept as text (Annex A), so '01012' and '1012' are one stop; plus ALIASES.
    """
    forms = [code] + list(ALIASES.get(kind, {}).get(code, ()))
    if kind == "bus" and code.isdigit():
        for f in (code.zfill(5), code.lstrip("0") or "0"):
            if f not in forms:
                forms.append(f)
    return tuple(forms)

INTERIM_AFTER = 12                  # resolved months before the own-error spread

# Singapore gazetted public holidays (Ministry of Manpower,
# https://www.mom.gov.sg/employment-practices/public-holidays, read
# 2026-11-08). A Sunday holiday's Monday in lieu is listed as the MOM page
# lists it. Only weekdays matter to the divisor.
PUBLIC_HOLIDAYS = frozenset(date.fromisoformat(d) for d in (
    "2026-01-01", "2026-02-17", "2026-02-18", "2026-03-21", "2026-04-03",
    "2026-05-01", "2026-05-27", "2026-05-31", "2026-06-01", "2026-08-09",
    "2026-08-10", "2026-11-08", "2026-11-09", "2026-12-25",
    "2027-01-01", "2027-02-06", "2027-02-07", "2027-02-08", "2027-03-10",
    "2027-03-26", "2027-05-01", "2027-05-17", "2027-05-20", "2027-08-09",
    "2027-10-28", "2027-12-25",
))
HOLIDAY_YEARS = (2026, 2027)

NotPostable = baselines.NotPostable


class MissingKey(RuntimeError):
    """No DataMall AccountKey in the environment: nothing is fetched."""


# ---------------------------------------------------------------- calendar

def month_key(m):
    """'2026-10' / '202610' / date -> '2026-10'."""
    if isinstance(m, date):
        return f"{m.year:04d}-{m.month:02d}"
    s = str(m).strip()
    if len(s) == 6 and s.isdigit():
        return f"{s[:4]}-{s[4:]}"
    return baselines._as_month(s)


def compact(m):
    return month_key(m).replace("-", "")


def weekdays(month, holidays=None):
    """Monday-Friday dates of the month that are not public holidays."""
    m = month_key(month)
    y, mo = int(m[:4]), int(m[5:7])
    if holidays is None:
        if y not in HOLIDAY_YEARS:
            raise ValueError(f"no public-holiday list for {y}; pass the round's frozen list")
        holidays = PUBLIC_HOLIDAYS
    holidays = {date.fromisoformat(str(h)) if not isinstance(h, date) else h
                for h in holidays}
    d, out = date(y, mo, 1), []
    while d.month == mo:
        if d.weekday() < 5 and d not in holidays:
            out.append(d)
        d += timedelta(days=1)
    return out


def lock_instant(month):
    """23:59 SGT on the last day before the target month starts."""
    m = month_key(month)
    first = date(int(m[:4]), int(m[5:7]), 1)
    return sg_common.sgt(first - timedelta(days=1), 23, 59)


def _next_month(m):
    return baselines._month_shift(month_key(m), -1)


def release_instant(month):
    """00:00 SGT on the 10th of the following month."""
    n = _next_month(month)
    return sg_common.sgt(f"{n}-10", 0, 0)


def cancel_instant(month):
    """23:59 SGT on the 17th of the following month (policy (1))."""
    n = _next_month(month)
    return sg_common.sgt(f"{n}-17", 23, 59)


# ---------------------------------------------------------------- parsing

def read_zip(body):
    """The one CSV inside a PV zip, as text."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(body))
    except zipfile.BadZipFile as e:
        raise ValueError(f"not a zip: {e}") from None
    names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
    if len(names) != 1:
        raise ValueError(f"expected one CSV in the zip, found {names}")
    return zf.read(names[0]).decode("utf-8-sig")


def parse(body, kind, expected_month):
    """Zip bytes -> {"rows": [...], "dropped_empty_hour": n, "codes": n}.

    Refuses (ValueError) a file for another month, of another PT_TYPE, with
    fewer distinct PT_CODEs than the kind's floor, or with a malformed row.
    """
    spec = KINDS[kind]
    want = month_key(expected_month)
    reader = csv.reader(io.StringIO(read_zip(body)))
    header = [h.strip() for h in next(reader, [])]
    if tuple(header) != COLUMNS:
        raise ValueError(f"header {header} is not the documented {list(COLUMNS)}")
    rows, dropped, codes = [], 0, set()
    for n, raw in enumerate(reader, start=2):
        if not raw or all(not c.strip() for c in raw):
            continue
        if len(raw) != len(COLUMNS):
            raise ValueError(f"line {n}: {len(raw)} fields")
        r = dict(zip(COLUMNS, (c.strip() for c in raw)))
        if r["YEAR_MONTH"] != want:
            raise ValueError(f"line {n}: YEAR_MONTH {r['YEAR_MONTH']!r} is not {want}")
        if r["PT_TYPE"] != spec["pt_type"]:
            raise ValueError(f"line {n}: PT_TYPE {r['PT_TYPE']!r} is not {spec['pt_type']}")
        codes.add(r["PT_CODE"])
        if r["TIME_PER_HOUR"] == "":
            dropped += 1
            continue
        try:
            r["TIME_PER_HOUR"] = int(r["TIME_PER_HOUR"])
            r["TOTAL_TAP_IN_VOLUME"] = int(r["TOTAL_TAP_IN_VOLUME"])
            r["TOTAL_TAP_OUT_VOLUME"] = int(r["TOTAL_TAP_OUT_VOLUME"])
        except ValueError:
            raise ValueError(f"line {n}: a count or hour is not an integer: {raw}") from None
        rows.append(r)
    if len(codes) < spec["min_codes"]:
        raise ValueError(f"{len(codes)} PT_CODEs, fewer than {spec['min_codes']}: "
                         "not a whole month's file")
    return {"rows": rows, "dropped_empty_hour": dropped, "codes": len(codes),
            "month": want}


class Cancel(Exception):
    pass


def weekday_average(parsed, kind, holidays=None, code=None):
    """The card's value for the file's month. Raises Cancel under policy (2)."""
    spec = KINDS[kind]
    codes = code_forms(kind, code or spec["code"])
    mine = [r for r in parsed["rows"] if r["PT_CODE"] in codes and r["DAY_TYPE"] == WEEKDAY]
    if not mine:
        raise Cancel(f"{spec['code']} (or {', '.join(codes[1:]) or 'no alias'}) "
                     f"has no WEEKDAY rows in the {parsed['month']} file")
    spellings = sorted({r["PT_CODE"] for r in mine})
    if len(spellings) > 1:
        raise Cancel(f"the {parsed['month']} file carries {spec['code']} under "
                     f"{len(spellings)} spellings {spellings}: one station, "
                     "never summed twice")
    by_hour = {}
    for r in mine:
        by_hour[r["TIME_PER_HOUR"]] = by_hour.get(r["TIME_PER_HOUR"], 0) + r["TOTAL_TAP_IN_VOLUME"]
    zero = [h for h in CHECK_HOURS if by_hour.get(h, 0) == 0]
    if zero:
        raise Cancel(f"{spec['code']} has zero WEEKDAY tap-ins at hour(s) {zero}")
    days = weekdays(parsed["month"], holidays)
    total = sum(by_hour.values())
    return {"value": float(round(total / len(days))), "total_tap_in": total,
            "weekdays": len(days)}


# ---------------------------------------------------------------- archive

def _dir(kind):
    return os.path.join(ROOT, "sources", "lta-pv", kind)


def _fetches(kind):
    return os.path.join(_dir(kind), "fetches.json")


def load_fetches(kind):
    try:
        with open(_fetches(kind)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(kind, rows):
    os.makedirs(_dir(kind), exist_ok=True)
    tmp = _fetches(kind) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rows, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, _fetches(kind))


def file_month(kind, month, body, url, now=None, manual=False):
    """Archive one month's zip, write-once. Returns (row, created)."""
    spec = KINDS[kind]
    m = compact(month)
    parsed = parse(body, kind, month)          # refuse before anything is written
    now = (now or archive_store.utcnow()).astimezone(timezone.utc)
    rows = load_fetches(kind)
    path = os.path.join(_dir(kind), f"{m}.zip")
    sha = provenance.digest(body)
    if os.path.exists(path):
        with open(path, "rb") as f:
            if provenance.digest(f.read()) != sha:
                raise ValueError(f"{kind} {m}: already archived with different bytes; "
                                 "the first archived file wins")
        return rows.get(m), False
    clean = url.split("?")[0]
    block = provenance.record(f"lta-pv/{kind}", clean, body,
                              fetched_at=sg_common.iso(now), ext="zip", day=m,
                              note="manual download" if manual else None)
    row = {"fetched_at": block["fetched_at"], "url": clean, "sha256": sha,
           "bytes": block["bytes"], "file": block["file"], "codes": parsed["codes"],
           "dropped_empty_hour": parsed["dropped_empty_hour"], "card": spec["card"]}
    if manual:
        row["manual"] = True
    rows[m] = row
    _save(kind, rows)
    return row, True


def read_month(kind, month, rows=None):
    rows = rows if rows is not None else load_fetches(kind)
    row = rows[compact(month)]
    with open(os.path.join(ROOT, row["file"]), "rb") as f:
        body = f.read()
    if provenance.digest(body) != row["sha256"]:
        raise RuntimeError(f"{row['file']} does not match its sha256")
    return parse(body, kind, month), row


# ---------------------------------------------------------------- fetching

def account_key(env=None):
    key = (env if env is not None else os.environ).get(KEY_ENV, "").strip()
    if not key:
        raise MissingKey(f"{KEY_ENV} is not set: no DataMall AccountKey, nothing fetched "
                         "(docs/sources/singapore-passenger-volume.md, registration)")
    return key


def request_url(kind, month):
    return f"{BASE}/{KINDS[kind]['path']}?Date={compact(month)}"


def fetch(kind, month, get=None, env=None):
    """(zip bytes, the S3 link it came from). The key travels only as a
    header and never into the archive or a log line."""
    import requests
    get = get or requests.get
    key = account_key(env)
    meta = archive_store.get(request_url(kind, month), name=f"lta-pv/{kind}",
                             accept="application/json",
                             headers={"AccountKey": key}, get=get)
    try:
        link = json.loads(meta[0])["value"][0]["Link"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise ValueError(f"PV/{KINDS[kind]['path']} answer has no value[0].Link") from None
    got = archive_store.get(link, name=f"lta-pv/{kind}", get=get)
    return got[0], link


def available_months(now):
    """The three months DataMall serves at `now` (guide: 'up to last three
    months'): the previous month only once the 10th has passed."""
    sg = now.astimezone(sg_common.SGT)
    newest = baselines._month_shift(f"{sg.year:04d}-{sg.month:02d}", 1)
    if now < release_instant(newest):
        newest = baselines._month_shift(newest, 1)
    return [baselines._month_shift(newest, i) for i in (2, 1, 0)]


def capture(kind, now=None, get=None, env=None):
    """Archive every month DataMall still serves that the archive lacks."""
    now = (now or archive_store.utcnow()).astimezone(timezone.utc)
    account_key(env)
    held = load_fetches(kind)
    out = []
    for m in available_months(now):
        if compact(m) in held:
            continue
        body, link = fetch(kind, m, get=get, env=env)
        row, created = file_month(kind, m, body, link, now=now)
        out.append({"month": m, "created": created, "sha256": row["sha256"]})
    return out


def ingest(kind, month, path, now=None):
    """Route B: a key holder downloaded the zip by hand."""
    with open(path, "rb") as f:
        body = f.read()
    return file_month(kind, month, body, f"manual:{os.path.basename(path)}",
                      now=now, manual=True)


# ---------------------------------------------------------------- rounds

def zero_point(kind, resolved_months):
    """The card's spec: interim relative sd until INTERIM_AFTER resolved months."""
    if resolved_months >= INTERIM_AFTER:
        spread = {"rule": "rms_relative", "n": INTERIM_AFTER, "search": INTERIM_AFTER}
        name = "mean1_lag1_rmsrel12"
    else:
        spread = {"rule": "relative_fixed", "cv": KINDS[kind]["interim_cv"]}
        name = "mean1_lag1_interim"
    return {"name": name, "family": "mean_k", "period": "month", "k": 1, "lag": 1,
            "search": 1, "digits": 0, "spread": spread}


def history(kind, rows=None, cutoff=None, holidays=None):
    """{month: value} for every archived month fetched by `cutoff` that
    settles; months that cancel are absent."""
    rows = rows if rows is not None else load_fetches(kind)
    out = {}
    for m, row in sorted(rows.items()):
        if cutoff is not None and sg_common.parse_iso(row["fetched_at"]) > cutoff:
            continue
        parsed, _ = read_month(kind, m, rows)
        try:
            out[month_key(m)] = weekday_average(parsed, kind, holidays)["value"]
        except Cancel:
            continue
    return out


def lock_reference(kind, month, rows=None, holidays=None):
    """The ranked reference from the archive as it stood at lock. Policy (3):
    the reference month (two back) missing from the archive -> NotPostable."""
    target = month_key(month)
    lock = lock_instant(target)
    series = {m: v for m, v in history(kind, rows, cutoff=lock, holidays=holidays).items()
              if m < target}
    # Resolved months whose own reference (two back) is also archived: what
    # the own-error spread can use.
    usable = sum(1 for m in series if baselines._month_shift(m, 2) in series)
    spec = zero_point(kind, usable)
    ref = baselines.reference(spec, series, target)
    return {"zero_point": spec["name"], "zero_point_spec": spec,
            "lock_at": sg_common.iso(lock), "ranked": ref, "card": KINDS[kind]["card"]}


def resolve(kind, month, now, rows=None, holidays=None):
    target = month_key(month)
    now = now.astimezone(timezone.utc)
    start, end = release_instant(target), cancel_instant(target)
    if now < start:
        return {"status": "pending", "reason": f"released from {sg_common.iso(start)}"}
    rows = rows if rows is not None else load_fetches(kind)
    row = rows.get(compact(target))
    if row and start <= sg_common.parse_iso(row["fetched_at"]) <= end:
        parsed, row = read_month(kind, target, rows)
        try:
            got = weekday_average(parsed, kind, holidays)
        except Cancel as e:
            return {"status": "cancelled", "reason": str(e), "file": row["file"]}
        got.update(status="resolved", file=row["file"], sha256=row["sha256"],
                   fetched_at=row["fetched_at"])
        return got
    if now > end:
        return {"status": "cancelled",
                "reason": f"no {target} file archived by {sg_common.iso(end)}"}
    return {"status": "pending", "reason": f"inside the window until {sg_common.iso(end)}"}


if __name__ == "__main__":                                   # pragma: no cover
    args = sys.argv[1:]
    try:
        if args[:1] == ["capture"] and len(args) == 2:
            print(json.dumps(capture(args[1]), indent=2))
        elif args[:1] == ["ingest"] and len(args) == 4:
            row, created = ingest(args[1], args[2], args[3])
            print(json.dumps({"created": created, "row": row}, indent=2))
        elif args[:1] == ["reference"] and len(args) == 3:
            print(json.dumps(lock_reference(args[1], args[2]), indent=2))
        else:
            sys.exit("usage: python -m ssa.adapters.sg_lta_pv capture <train|bus> | "
                     "ingest <train|bus> <YYYY-MM> <file.zip> | reference <train|bus> <YYYY-MM>")
    except MissingKey as e:
        sys.exit(str(e))
