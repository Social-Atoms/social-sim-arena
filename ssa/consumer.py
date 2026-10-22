"""Private review archive and an opt-in bridge to existing arena data shapes.

Nothing in this module schedules rounds, invokes models, or changes rights.
Raw and normalized bodies stay under .local until redistribution is reviewed.
"""
import copy
import fcntl
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from . import baselines, scoring
from .adapters import consumer as adapters

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARCHIVE = ROOT / ".local" / "consumer"
HOSTS = {
    "michigan_finals": {"www.sca.isr.umich.edu", "sca.isr.umich.edu"},
    "michigan_current": {"www.sca.isr.umich.edu", "sca.isr.umich.edu"},
    "mint": {"www.usmint.gov", "usmint.gov"},
    "sce": {"www.newyorkfed.org", "newyorkfed.org"},
}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n").encode()


def sha256(body):
    return hashlib.sha256(body).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".writing-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded(value))
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def immutable(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(body)
    except FileExistsError:
        if path.read_bytes() != body:
            raise ValueError(f"Immutable archive conflict: {path}")


def official_url(source, url):
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.hostname not in HOSTS[source]
            or parts.username or parts.password or parts.query or parts.fragment):
        raise ValueError("Provenance requires a credential-free HTTPS publisher URL without query parameters")
    return url


def archive_path(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Archive path escapes root")
    return path


def index(root=DEFAULT_ARCHIVE):
    path = Path(root) / "index.json"
    return json.loads(path.read_text()) if path.exists() else {"format": "ssa.consumer.index.v1", "sources": {}}


def update_index(root, source, attempt, vintage=None):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    attempt_file = f"attempts/{source}/{sha256(encoded(attempt))}.json"
    immutable(root / attempt_file, encoded(attempt))
    with (root / ".index.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        doc = index(root)
        entry = doc["sources"].setdefault(source, {})
        entry["last_attempt"] = attempt
        if vintage is not None:
            entry["latest_valid"] = vintage
            history = entry.setdefault("vintages", [])
            if vintage not in history:
                history.append(vintage)
        write_json(root / "index.json", doc)


def record_attempt(root, source, status, detail, **evidence):
    if source not in adapters.PARSERS:
        raise ValueError("Unknown consumer source")
    update_index(root, source, {"at": now(), "status": status, "detail": detail, **evidence})


def ingest(source, body, url, root=DEFAULT_ARCHIVE, captured_at=None,
           evidence_body=None, evidence_suffix=".html", published_at=None):
    """Validate before archiving. A failed parse cannot replace a good vintage."""
    url = official_url(source, url)
    parsed = adapters.PARSERS[source](body)
    if source == "mint" and not evidence_body:
        raise ValueError("A manual transcription needs the original publisher artifact as evidence")
    if published_at is not None:
        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("published_at requires a timezone")
    root = Path(root)
    raw_hash, parsed_body = sha256(body), encoded(parsed)
    raw_file = f"raw/{source}/{raw_hash}.bin"
    parsed_file = f"parsed/{source}/{sha256(parsed_body)}.json"
    immutable(root / raw_file, body)
    immutable(root / parsed_file, parsed_body)
    evidence_file = None
    if evidence_body:
        if evidence_suffix not in {".html", ".pdf", ".csv", ".json", ".xlsx"}:
            raise ValueError("Unsupported evidence artifact extension")
        evidence_file = f"evidence/{source}/{sha256(evidence_body)}{evidence_suffix}"
        immutable(root / evidence_file, evidence_body)
    manifest = {
        "format": "ssa.consumer.vintage.v1", "source": source,
        "url": url, "captured_at": captured_at or now(), "published_at": published_at,
        "raw_file": raw_file, "raw_sha256": raw_hash, "raw_bytes": len(body),
        "parsed_file": parsed_file, "parsed_sha256": sha256(parsed_body),
        "evidence_file": evidence_file,
        "historical_first_print": False, "rights": "review-required",
        "public_redistribution": False, "parser_version": 1,
    }
    manifest_file = f"vintages/{source}/{sha256(encoded(manifest))}.json"
    immutable(root / manifest_file, encoded(manifest))
    update_index(root, source, {"at": now(), "status": "collected", "detail": "Validated and archived"}, manifest_file)
    return manifest


def read_vintage(root, relative):
    path = archive_path(root, relative)
    body = path.read_bytes()
    if sha256(body) != path.stem:
        raise ValueError("Vintage manifest hash mismatch")
    manifest = json.loads(body)
    source = manifest["source"]
    official_url(source, manifest["url"])
    raw = archive_path(root, manifest["raw_file"]).read_bytes()
    if len(raw) != manifest["raw_bytes"] or sha256(raw) != manifest["raw_sha256"]:
        raise ValueError("Raw archive hash/length mismatch")
    parsed = archive_path(root, manifest["parsed_file"]).read_bytes()
    if sha256(parsed) != manifest["parsed_sha256"]:
        raise ValueError("Parsed archive hash mismatch")
    rebuilt = adapters.PARSERS[source](raw)
    if encoded(rebuilt) != parsed:
        raise ValueError("Parser replay differs from archived normalized data")
    if manifest["evidence_file"]:
        path = archive_path(root, manifest["evidence_file"])
        if sha256(path.read_bytes()) != path.stem:
            raise ValueError("Publisher evidence hash mismatch")
    return rebuilt, manifest


def build_all(root=DEFAULT_ARCHIVE):
    """Read only the local archive and append compatible candidate histories."""
    doc = index(root)
    series, lists, provenance, states = {}, {}, {}, {}
    for source, entry in sorted(doc["sources"].items(), key=lambda item: (item[0] == "michigan_current", item[0])):
        states[source] = copy.deepcopy(entry["last_attempt"])
        if "latest_valid" not in entry:
            continue
        parsed, manifest = read_vintage(root, entry["latest_valid"])
        if manifest["source"] != source:
            raise ValueError("Index points to a different source's vintage")
        if source == "mint":
            combined_series, combined_lists, revisions = {}, {}, []
            for vintage in entry.get("vintages", [entry["latest_valid"]]):
                prior, _ = read_vintage(root, vintage)
                for field, target in (("series", combined_series), ("lists", combined_lists)):
                    for sid, points in prior[field].items():
                        by_date = target.setdefault(sid, {})
                        for point in points:
                            day = point.get("date", point.get("week_end"))
                            if day in by_date and point != by_date[day]:
                                revisions.append({"id": sid, "date": day, "vintage": vintage})
                            by_date.setdefault(day, point)
            parsed = {**parsed, "series": {sid: [points[k] for k in sorted(points)] for sid, points in combined_series.items()},
                      "lists": {sid: [points[k] for k in sorted(points)] for sid, points in combined_lists.items()}}
            products = {sid[len("mint_"):-len("_cumulative")]: points
                        for sid, points in parsed["series"].items() if sid.endswith("_cumulative")}
            derived = adapters.mint_series(products)
            parsed["series"] = derived["series"]
            parsed["metadata"] = {**parsed["metadata"],
                "skipped_nonweekly_differences": derived["metadata"]["skipped_nonweekly_differences"],
                "report_dates": sorted({point["date"] for points in products.values() for point in points})}
            states[source]["vintages"] = entry.get("vintages", [entry["latest_valid"]])
            states[source]["revision_conflicts"] = revisions
        states[source]["vintage"] = entry["latest_valid"]
        states[source]["metadata"] = parsed["metadata"]
        if states[source]["status"] != "collected":
            states[source]["using_previous_vintage"] = True
        for sid, points in parsed["series"].items():
            if source == "michigan_current":
                finals = series.get(sid, [])
                have = {row["date"] for row in finals}
                extra = [row for row in points if row["date"] not in have
                         and parsed["metadata"]["release_types"][row["date"]] == "preliminary"]
                series[sid] = sorted(finals + extra, key=lambda row: row["date"])
                provenance[sid] = [item for item in (provenance.get(sid), entry["latest_valid"]) if item]
            elif sid in series:
                raise ValueError(f"Duplicate series ID {sid}")
            else:
                series[sid], provenance[sid] = points, states[source].get("vintages", entry["latest_valid"])
        for sid, points in parsed["lists"].items():
            if sid in lists:
                raise ValueError(f"Duplicate list ID {sid}")
            lists[sid], provenance[sid] = points, states[source].get("vintages", entry["latest_valid"])
    return {"format": "ssa.consumer.review.v1", "series": series, "lists": lists,
            "provenance": provenance, "sources": states, "review_status": "pending"}


def append_review(base, bundle):
    """Return a local copy with extra histories, preserving every live record."""
    out = copy.deepcopy(base)
    for field, incoming in (("series_tail", bundle["series"]), ("lists", bundle["lists"])):
        target = out.setdefault(field, {})
        overlap = set(target) & set(incoming)
        if overlap:
            raise ValueError(f"Refusing to overwrite existing {field}: {sorted(overlap)}")
        target.update(incoming)
    out["consumer_review"] = {key: value for key, value in bundle.items() if key not in {"series", "lists"}}
    return out


def measurements(bundle):
    """Current-vintage diagnostics, explicitly not a leakage-free backtest."""
    out = {"scalar": {}, "ranking": {}, "historical_first_print": False}
    for sid, rows in bundle["series"].items():
        values = [row["value"] for row in rows]
        entry = {"count": len(rows), "first": rows[0]["date"], "last": rows[-1]["date"],
                 "latest_value": values[-1]}
        if len(rows) >= 4:
            noise, movement = scoring.noise_floor(values[-60:])
            recent = values[-37:]
            changes = [abs(b - a) for a, b in zip(recent, recent[1:])]
            entry.update(noise=noise, real_movement=movement,
                         mean_absolute_change_36=sum(changes) / len(changes),
                         movement_exceeds_noise=movement > noise)
        if len(rows) >= 13:
            replay = []
            for i in range(max(12, len(rows) - 36), len(rows)):
                forecast = baselines.persistence(rows[:i])
                replay.append(scoring.crps_normal(forecast["mean"], forecast["sd"], rows[i]["value"]))
            entry["current_vintage_persistence_crps"] = sum(replay) / len(replay)
        out["scalar"][sid] = entry
    for sid, rows in bundle["lists"].items():
        churn = [len(set(b["items"]) - set(a["items"])) for a, b in zip(rows, rows[1:])
                 if (adapters.iso_day(b["week_end"]) - adapters.iso_day(a["week_end"])).days == 7]
        out["ranking"][sid] = {"count": len(rows), "first": rows[0]["week_end"],
                                "last": rows[-1]["week_end"],
                                "consecutive_week_pairs": len(churn),
                                "mean_new_items": sum(churn) / len(churn) if churn else None}
    return out
