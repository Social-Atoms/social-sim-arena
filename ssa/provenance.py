"""Where every number came from, recorded at the moment it arrived.

The arena's headline claim is that a reader can check it. That holds for the
forecasts -- hashed, locked, committed -- and it did not hold for the *inputs*.
Civiqs was the exception: it archives a dated snapshot of what the dashboard
showed, because a modelled tracker revises its own history nightly and a
resolution computed against it could otherwise never be rechecked.

Everything else had no vintage at all, and that is the larger gap. **Twenty-one
of the twenty-four registered series arrive through one published Google Sheet**
(`ssa/adapters/silverbulletin.py`), which is revised in place: rows are added,
and existing rows can be corrected. A resolution computed from it last week
cannot be reproduced today, and if the sheet moved or its share link were
revoked, seven eighths of the arena would go dark in a way that presents as "no
new polls this week" rather than as an outage.

So every upstream body is written here as it arrived, one file per source per
day, alongside a manifest recording the URL, the fetch time, the SHA-256 and the
size. `site/data.json` then carries a per-series block naming which of these a
number came from, which is what lets a page say "this figure is from *that* file
fetched at *that* time" rather than crediting a brand.

**Bodies are stored uncompressed, deliberately.** These files are append-mostly:
a day adds a handful of poll rows to five thousand. Git delta-compresses similar
text blobs, so consecutive daily vintages of a 1.5 MB CSV cost a few kilobytes
each in the pack. Gzipping them first would look thriftier in `ls` and defeat
delta compression entirely, turning a few KB a day into a couple of hundred.

**A day is the unit, not a run.** The refresh runs every six hours; four
identical vintages of the same file are three files of noise. A second fetch on
a day whose stored body already matches is recorded in the manifest and not
rewritten.
"""
import hashlib
import json
import os
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE = os.path.join(ROOT, "sources")
MANIFEST = os.path.join(ARCHIVE, "manifest.json")


def _utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def digest(body):
    if isinstance(body, str):
        body = body.encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def archive_path(name, day, ext="csv"):
    return os.path.join(ARCHIVE, name, f"{day}.{ext}")


def rel(path):
    """A repository-relative path, which is what a reader can act on."""
    return os.path.relpath(path, ROOT)


def load_manifest():
    try:
        with open(MANIFEST) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def current(name, expected_url=None):
    """Return ``(raw bytes, manifest row)`` for the last validated vintage.

    This is the only fallback the reliability path permits: the exact same
    source, from the committed provenance archive.  Callers must run the same
    parser again before use.  A different publisher or a reconstructed body is
    not a current vintage and does not belong here.
    """
    row = (load_manifest().get(name) or {}).copy()
    if expected_url is not None and row.get("url") != expected_url:
        raise RuntimeError(
            f"{name}: manifest URL {row.get('url')!r} does not match the "
            f"required same-source URL {expected_url!r}")
    relative = row.get("file")
    if not relative:
        raise RuntimeError(f"{name}: no validated provenance vintage exists")
    root = os.path.realpath(ROOT)
    path = os.path.realpath(os.path.join(ROOT, relative))
    if os.path.commonpath((root, path)) != root:
        raise RuntimeError(f"{name}: provenance path escapes the repository")
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as error:
        raise RuntimeError(
            f"{name}: validated provenance vintage is unavailable: {relative}") from error
    expected = row.get("sha256")
    actual = digest(raw)
    if not expected or actual != expected:
        raise RuntimeError(
            f"{name}: archived vintage hash mismatch for {relative}; refusing "
            "an artifact that is not the validated manifest body")
    row["source"] = name
    row["file"] = relative
    return raw, row


def save_manifest(m):
    os.makedirs(ARCHIVE, exist_ok=True)
    tmp = MANIFEST + ".tmp"
    with open(tmp, "w") as f:
        json.dump(m, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, MANIFEST)


def record(name, url, body, fetched_at=None, ext="csv", day=None, note=None):
    """Archive one upstream body and return its provenance block.

    The block is what `site/data.json` carries and what a page renders:

        {"source": "sb_approval",
         "url": "https://docs.google.com/.../pub?output=csv",
         "fetched_at": "2026-08-15T02:11:07Z",
         "sha256": "f54536230e8e...",
         "bytes": 1550431,
         "file": "sources/sb_approval/2026-08-15.csv"}

    `fetched_at` is always the *current* fetch, even when the body was already
    on disk -- the question "when did we last confirm this" has a different
    answer from "when did this content first appear", and both matter.
    """
    at = fetched_at or _utcnow()
    day = day or at[:10]
    raw = body.encode("utf-8") if isinstance(body, str) else body
    sha = digest(raw)
    path = archive_path(name, day, ext)

    m = load_manifest()
    # Two different questions, and conflating them is a real bug: whether *this
    # day's vintage* is already on disk decides whether to write a file, while
    # whether the *content* moved decides `changed_at`. A source that stopped
    # updating still gets a new file on a new day, and reading the day's file to
    # answer the second question reports every such day as a change -- which
    # would hide exactly the staleness this is here to surface.
    on_disk = None
    if os.path.exists(path):
        with open(path, "rb") as f:
            on_disk = digest(f.read())
    if on_disk != sha:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(raw)
        os.replace(tmp, path)
    content_moved = (m.get(name) or {}).get("sha256") != sha

    block = {"source": name, "url": url, "fetched_at": at, "sha256": sha,
             "bytes": len(raw), "file": rel(path)}
    if note:
        block["note"] = note

    row = m.setdefault(name, {})
    row.update({k: block[k] for k in ("url", "fetched_at", "sha256", "bytes", "file")})
    if note:
        row["note"] = note
    # When the content last moved, as distinct from when it was last seen. A
    # source that stops updating looks identical to a healthy one in
    # `fetched_at` alone, and that is exactly the failure this file exists to
    # make visible.
    if content_moved:
        row["changed_at"] = at
    row.setdefault("changed_at", at)
    m[name] = row
    save_manifest(m)
    return block


def unchanged_since(name, now=None):
    """Days since this source's content last changed, or None if unknown.

    A stale upstream is the failure mode that hides: the pipeline keeps
    running, the site keeps rendering, and the numbers quietly stop moving.
    """
    row = load_manifest().get(name) or {}
    changed = row.get("changed_at")
    if not changed:
        return None
    then = datetime.strptime(changed, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return ((now or datetime.now(timezone.utc)) - then).days
