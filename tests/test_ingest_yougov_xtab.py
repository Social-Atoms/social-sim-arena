"""Offline manual-ingest freshness and archive integrity checks."""
import tempfile
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.test_yougov_xtab import workbook
from tools.ingest_yougov_xtab import ingest

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    downloaded = root / "download.xlsx"
    archive = root / "archive"
    downloaded.write_bytes(workbook()[0])
    try:
        ingest(downloaded, "2026-09-07", "2026-09-08", archive)
    except ValueError as error:
        assert "Expected newest wave" in str(error)
    else:
        raise AssertionError("A stale wave was accepted")
    assert not list(archive.glob("*.xlsx"))
    target, digest, created = ingest(downloaded, "2026-08-31", "2026-09-03", archive)
    assert created and len(digest) == 64 and target.read_bytes() == downloaded.read_bytes()
    _, same, created = ingest(downloaded, "2026-08-31", "2026-09-03", archive)
    assert not created and same == digest
    downloaded.write_bytes(workbook(malformed="Democrat")[0])
    try:
        ingest(downloaded, "2026-08-31", "2026-09-03", archive)
    except RuntimeError:
        pass
    else:
        raise AssertionError("Incomplete demographics were accepted")
    assert target.read_bytes() != downloaded.read_bytes()

print("Manual YouGov ingest checks passed")
