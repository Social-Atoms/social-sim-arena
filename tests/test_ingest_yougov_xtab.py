"""Offline manual-ingest freshness and archive integrity checks."""
import io
import tempfile
import zipfile
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
    good = downloaded.read_bytes()
    downloaded.write_bytes(workbook(malformed="Democrat")[0])
    try:
        ingest(downloaded, "2026-08-31", "2026-09-03", archive)
    except RuntimeError:
        pass
    else:
        raise AssertionError("Incomplete demographics were accepted")
    assert target.read_bytes() == good
    assert [f.name for f in archive.iterdir()] == ["2026-09-03.xlsx"]

    # Same day, different bytes: refused, archive untouched.
    other = io.BytesIO(good)
    with zipfile.ZipFile(other, "a") as book:
        book.comment = b"re-saved"
    other = other.getvalue()
    downloaded.write_bytes(other)
    try:
        ingest(downloaded, "2026-08-31", "2026-09-03", archive)
    except ValueError as error:
        assert "different bytes" in str(error)
    else:
        raise AssertionError("A same-day capture with different bytes was accepted")
    assert target.read_bytes() == good

    # Back-dated capture: refused even with fresh bytes.
    try:
        ingest(downloaded, "2026-08-31", "2026-09-02", archive)
    except ValueError as error:
        assert "predates newest archive" in str(error)
    else:
        raise AssertionError("A back-dated capture was accepted")
    assert [f.name for f in archive.iterdir()] == ["2026-09-03.xlsx"]
    later, _, created = ingest(downloaded, "2026-08-31", "2026-09-04", archive)
    assert created and later.read_bytes() == other

# The default capture day is the UTC date, not the local one.
import tools.ingest_yougov_xtab as tool
from datetime import datetime, timezone
from unittest import mock


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        # 23:30 on the 2nd in New York is already the 3rd in UTC.
        if tz is None:
            return datetime(2026, 9, 2, 23, 30)
        return datetime(2026, 9, 3, 3, 30, tzinfo=timezone.utc).astimezone(tz)


with tempfile.TemporaryDirectory() as directory, mock.patch.object(tool, "datetime", Clock):
    root = Path(directory)
    downloaded = root / "download.xlsx"
    downloaded.write_bytes(workbook()[0])
    target, _, _ = ingest(downloaded, "2026-08-31", archive_dir=root / "archive")
    assert target.name == "2026-09-03.xlsx", target.name

print("Manual YouGov ingest checks passed")
