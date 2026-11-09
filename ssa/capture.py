"""Write-once daily captures and manual ingests, shared by every archive-first source.

A source whose live endpoint cannot be addressed as of a past date resolves on
its own archive (docs/sources.md §1.2). This module is that archive, taken out
of ssa/adapters/seattle_cad.py so a new urban source adds an adapter and an
inventory row, not a fourth copy of the same file handling:

    store = capture.DailyArchive("nyc_311", ext="json")
    row = store.capture(lambda day: capture.get(url_for(day), name="nyc_311"),
                        parse=parse, describe=lambda parsed: {"newest_day": ...})
    body, row = store.read(day)                  # sha256 checked
    day = store.snapshot_at_or_before(lock_instant)

    capture.ingest_manual("yougov_xtab", path, ext="xlsx", check=check_wave)

**Layout.** `sources/<name>/<fetch day, UTC>.<ext>`, the body exactly as
served, and `sources/<name>/fetches.json`: one row per day with the fetch
instant, URL, sha256, size and file, because a lock or resolution rule stated
in *instants* ("the newest capture at or before the lock") cannot be answered
from the provenance manifest, which keeps only the newest fetch per source.
The manifest is updated too (`provenance.record`), so health checks see the
source like any other.

**Write-once per UTC day.** The first capture of a day wins and a later run the
same day costs no request. The body is parsed *before* it is filed, so a
malformed answer fails the run instead of entering the record. A file on disk
with no fetches row is refused rather than overwritten: its fetch time is
unknown, and a lock rule would read it at the wrong instant.

**Fetching.** An honest User-Agent and a timeout on every request. An HTTP error
is raised as it came -- a 403 or 429 from a datacenter runner is a finding (the
source then needs a local courier, lessons 3 and 5) and is never retried; a
dropped connection is retried once, never more (docs/sources.md §2.5).
Redirects are not followed unless the caller says so. Response headers that
say how fresh the body is are recorded with the capture, and a Socrata body
whose `X-SODA2-Data-Out-Of-Date` says `true` is refused: the portal is serving
a replica behind its own source, and filing it would archive a stale answer
under today's date (card nyc-311-requests).

**Manual ingest** is the same record for a file a person downloaded
(tools/ingest_yougov_xtab.py is the model): hash, a check hook that validates
the content before anything is written, a capture day that may not predate the
newest one held (vintages are read in name order), write-once with identical
bytes accepted as a no-op, a fetches row marked `"manual": true`, and the
manifest entry.
"""
import json
import os
from datetime import datetime, timezone

import requests

from . import provenance

UA = "social-simulation-arena/1.0 (research benchmark; contact via repository)"
TIMEOUT = 120

# Response headers kept with a capture: what an auditor needs to say how fresh
# the body was when it was filed. Lower-cased on the way in.
FRESHNESS_HEADERS = (
    "date", "last-modified", "etag", "age", "cache-control", "content-type",
    # Socrata (data.cityofnewyork.us, cos-data.seattle.gov, data.cityofchicago.org)
    "x-soda2-data-out-of-date", "x-soda2-truth-last-modified",
    "x-soda2-secondary-last-modified", "x-soda2-dataset-version",
)


class Stale(RuntimeError):
    """The server says the body is behind its own source; it is not filed."""


class Fetched(tuple):
    """(body, url) -- unpacks like the pair the adapters always returned --
    carrying the recorded freshness headers as `.headers`."""

    def __new__(cls, body, url, headers=None):
        self = super().__new__(cls, (body, url))
        self.headers = dict(headers or {})
        return self

    @property
    def body(self):
        return self[0]

    @property
    def url(self):
        return self[1]


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def freshness(headers):
    """The FRESHNESS_HEADERS present in a response, lower-cased keys, sorted."""
    got = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    return {k: got[k] for k in FRESHNESS_HEADERS if k in got}


def get(url, *, name, accept=None, timeout=TIMEOUT, headers=None,
        allow_redirects=False, refuse_stale=True, get=requests.get):
    """One honest GET. Returns Fetched(body bytes, url, freshness headers).

    Raises requests.HTTPError on any status but 200 (never retried), retries a
    dropped connection or a timeout once, and raises Stale when a Socrata
    server flags its own body as out of date.
    """
    h = {"User-Agent": UA}
    if accept:
        h["Accept"] = accept
    h.update(headers or {})
    for attempt in (1, 2):
        try:
            r = get(url, headers=h, timeout=timeout, allow_redirects=allow_redirects)
        except (requests.ConnectionError, requests.Timeout):
            if attempt == 2:
                raise
            continue
        if r.status_code != 200:
            raise requests.HTTPError(f"{name}: HTTP {r.status_code} from {url}",
                                     response=r)
        kept = freshness(getattr(r, "headers", None))
        if refuse_stale and kept.get("x-soda2-data-out-of-date", "").strip().lower() == "true":
            raise Stale(f"{name}: {url} answered with X-SODA2-Data-Out-Of-Date: "
                        "true; a stale replica is not filed as today's capture")
        return Fetched(r.content, url, kept)


class DailyArchive:
    """`sources/<name>/<day>.<ext>` plus `fetches.json`, write-once per day.

    Paths are resolved when the object is built, so an adapter whose tests
    point its module globals at a sandbox builds one per call.
    """

    def __init__(self, name, ext="json", archive=None, fetches=None, root=None):
        self.name, self.ext = name, ext
        self.root = root or provenance.ROOT
        self.archive = archive or os.path.join(self.root, "sources", name)
        self.fetches = fetches or os.path.join(self.archive, "fetches.json")

    # -- the record
    def path(self, day, suffix=""):
        return os.path.join(self.archive, f"{day}{suffix}.{self.ext}")

    def load(self):
        try:
            with open(self.fetches) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def save(self, rows):
        os.makedirs(os.path.dirname(self.fetches), exist_ok=True)
        tmp = self.fetches + ".tmp"
        with open(tmp, "w") as f:
            json.dump(rows, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, self.fetches)

    def _rel(self, path):
        return os.path.relpath(path, self.root).replace(os.sep, "/")

    # -- capturing
    def capture(self, fetch, parse=None, describe=None, now=None):
        """Archive today's body, write-once per UTC day. Returns the row.

        `fetch(day)` returns (body, url) or a Fetched; `parse(body)` validates
        it and raises on anything malformed; `describe(parsed)` adds fields to
        the row (the data's own as-of, counts the adapter wants on record).
        """
        now = (now or utcnow()).astimezone(timezone.utc)
        day = now.date().isoformat()
        rows = self.load()
        path = self.path(day)
        if day in rows and os.path.exists(path):
            return rows[day]
        if os.path.exists(path):
            raise RuntimeError(f"{self.name}: {self._rel(path)} exists without a "
                               "fetches.json row; refusing to overwrite a capture "
                               "whose fetch time is unknown")
        got = fetch(day)
        body, url = got[0], got[1]
        parsed = parse(body) if parse else None
        if os.path.abspath(provenance.archive_path(self.name, day, self.ext)) \
                != os.path.abspath(path):
            raise RuntimeError(f"{self.name}: the archive {self.archive} is not "
                               "where provenance files it; refusing a split record")
        block = provenance.record(self.name, url, body, fetched_at=iso(now),
                                  ext=self.ext, day=day)
        row = {"fetched_at": block["fetched_at"], "url": url,
               "sha256": block["sha256"], "bytes": block["bytes"],
               "file": block["file"]}
        if describe:
            row.update(describe(parsed))
        headers = getattr(got, "headers", None)
        if headers:
            row["headers"] = dict(headers)
        rows[day] = row
        self.save(rows)
        return row

    # -- reading
    def read(self, day, fetches=None):
        """(body bytes, row) for one archived day; refuses a changed file."""
        fetches = fetches if fetches is not None else self.load()
        row = fetches[day]
        with open(os.path.join(self.root, row["file"]), "rb") as f:
            body = f.read()
        if provenance.digest(body) != row["sha256"]:
            raise RuntimeError(f"{self.name}: {row['file']} does not match its "
                               "recorded sha256; refusing a changed archive")
        return body, row

    @staticmethod
    def snapshot_at_or_before(fetches, instant):
        """The capture day with the newest fetched_at at or before `instant`."""
        days = [d for d, r in fetches.items() if parse_iso(r["fetched_at"]) <= instant]
        return max(days, key=lambda d: fetches[d]["fetched_at"]) if days else None

    @staticmethod
    def snapshots_between(fetches, start, end):
        """Capture days with start <= fetched_at <= end, earliest first."""
        days = [d for d, r in fetches.items()
                if start <= parse_iso(r["fetched_at"]) <= end]
        return sorted(days, key=lambda d: fetches[d]["fetched_at"])


def ingest_manual(name, path, *, ext=None, check=None, capture_day=None,
                  now=None, url=None, describe=None):
    """File a manually downloaded file as `sources/<name>/<capture day>.<ext>`.

    `check(body)` validates the content and raises before anything is written
    (the expected wave, the required cells); whatever it returns goes to
    `describe` (default: nothing) for the row. Returns
    {"archive", "sha256", "created", "row"}.

    Refuses a capture day older than the newest one already held, and a
    second, different file for a day already held; the same bytes again are
    a no-op (`created` False).
    """
    now = (now or utcnow()).astimezone(timezone.utc)
    ext = ext or os.path.splitext(str(path))[1].lstrip(".")
    if not ext:
        raise ValueError(f"{name}: cannot tell the file type of {path}; pass ext")
    day = datetime.strptime(capture_day or now.date().isoformat(), "%Y-%m-%d").date()
    day = day.isoformat()
    with open(path, "rb") as f:
        body = f.read()
    if not body:
        raise ValueError(f"{name}: {path} is empty; archive unchanged")
    checked = check(body) if check else None
    store = DailyArchive(name, ext=ext,
                         archive=os.path.join(provenance.ARCHIVE, name))
    sha = provenance.digest(body)
    target = store.path(day)
    rows = store.load()
    if os.path.exists(target):
        with open(target, "rb") as f:
            if provenance.digest(f.read()) != sha:
                raise ValueError(f"{name}: {store._rel(target)} already exists with "
                                 "different bytes; archive unchanged")
        return {"archive": store._rel(target), "sha256": sha, "created": False,
                "row": rows.get(day)}
    held = sorted(fn.split(".")[0] for fn in os.listdir(store.archive)
                  if fn.endswith("." + ext) and not fn.startswith(".")) \
        if os.path.isdir(store.archive) else []
    if held and day < held[-1]:
        raise ValueError(f"{name}: capture day {day} predates the newest archive "
                         f"{held[-1]}; vintages are read in name order, so a "
                         "back-dated one would rewrite what that day knew")
    block = provenance.record(name, url or f"manual:{os.path.basename(str(path))}",
                              body, fetched_at=iso(now), ext=ext, day=day,
                              note="manual download")
    row = {"fetched_at": block["fetched_at"], "url": block["url"], "sha256": sha,
           "bytes": block["bytes"], "file": block["file"], "manual": True}
    if describe:
        row.update(describe(checked))
    rows[day] = row
    store.save(rows)
    return {"archive": block["file"], "sha256": sha, "created": True, "row": row}
