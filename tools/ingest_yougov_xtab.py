"""Validate a manually downloaded crosstab wave before filing its raw vintage."""
import argparse
import hashlib
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ssa.adapters import yougov_xtab


def ingest(path, expected_wave, capture_day=None, archive_dir=None):
    expected = date.fromisoformat(expected_wave).isoformat()
    capture_day = capture_day or date.today().isoformat()
    date.fromisoformat(capture_day)
    body = Path(path).read_bytes()
    waves = yougov_xtab.parse(body)
    if not waves or waves[-1]["date"] != expected:
        got = waves[-1]["date"] if waves else "none"
        raise ValueError(f"Expected newest wave {expected}, got {got}; archive unchanged")
    if len(waves[-1]["cells"]) != 1 + len(yougov_xtab.SCORED_CELLS):
        raise ValueError("Latest wave lacks required demographic cells")
    archive_dir = Path(archive_dir or yougov_xtab.ARCHIVE)
    archive_dir.mkdir(parents=True, exist_ok=True)
    target = archive_dir / f"{capture_day}.xlsx"
    digest = hashlib.sha256(body).hexdigest()
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError("Capture-day archive already exists with different bytes")
        return target, digest, False
    temporary = target.with_suffix(".xlsx.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(body)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target, digest, True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--expected-wave", required=True)
    parser.add_argument("--capture-day")
    args = parser.parse_args(argv)
    target, digest, created = ingest(args.file, args.expected_wave, args.capture_day)
    print(json.dumps({"archive": str(target.relative_to(ROOT)),
                      "expected_wave": args.expected_wave,
                      "sha256": digest, "created": created}, sort_keys=True))


if __name__ == "__main__":
    main()
