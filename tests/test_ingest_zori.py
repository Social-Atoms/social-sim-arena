"""Zillow ZORI manual ingest (card zillow-zori-metro@v3): checks, private raw file, public derived record.

**The fixture is synthetic.** Zillow's terms bar automated queries, so no real
file was downloaded for this test and none may be: `synthetic_csv` writes a
small made-up file with the real layout (51 metros plus the United States,
month-end columns from 2015-01-31) and invented, deterministic values. No
number here is Zillow data.
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from ssa import consumer, inventory, source_notices             # noqa: E402
from ssa.adapters import zillow_zori as z                        # noqa: E402
import ingest_zori as tool                                       # noqa: E402

PASSED = []


def ok(fn):
    PASSED.append(fn.__name__)
    print("ok", fn.__name__)


def synthetic_csv(newest, bump=None):
    """A made-up file in ZORI's layout ending at `newest`. `bump` maps a
    RegionID to a factor applied to its value for the month before `newest`,
    to fake a revised history."""
    months = []
    m = z.FIRST_MONTH
    while True:
        months.append(m)
        if m == newest:
            break
        m = z.shift(m, 1)
    head = z.HEADER + [z.month_end(x).isoformat() for x in months]
    lines = [",".join(head)]
    rows = [("102001", 0, "United States", "country", "")]
    rows += [(str(900000 + i), i, f"Metro {i:02d}, XX", "msa", "XX") for i in range(1, 52)]
    for rid, rank, name, kind, state in rows:
        vals = []
        for k, mm in enumerate(months):
            v = 1000.0 + 10 * rank + 3.0 * k + (k % 12) * 0.5
            if bump and rid in bump and mm == z.shift(newest, -1):
                v *= bump[rid]
            vals.append(f"{v:.6f}")
        lines.append(",".join([rid, str(rank), f'"{name}"', kind, state] + vals))
    return ("\n".join(lines) + "\n").encode()


class Sandbox:
    def __enter__(self):
        self.dir = Path(tempfile.mkdtemp())
        self.public = self.dir / "public"
        self.archive = self.dir / "private"
        self.downloads = self.dir / "dl"
        self.downloads.mkdir()
        return self

    def file(self, body, name=z.FILE_NAME):
        p = self.downloads / name
        p.write_bytes(body)
        return p

    def ingest(self, body, month, day, name=z.FILE_NAME):
        return tool.ingest(self.file(body, name), month, capture_day=day,
                           archive=self.archive, public=self.public,
                           now=datetime(2027, 1, 17, tzinfo=timezone.utc))

    def __exit__(self, *exc):
        shutil.rmtree(self.dir)


def refuses(fn, words):
    try:
        fn()
    except ValueError as e:
        assert words in str(e), (words, str(e))
        return
    raise AssertionError(f"accepted; expected a refusal naming {words!r}")


def test_the_rights_verdict_and_its_conditions():
    assert inventory.rights_table()["zillow_zori"] == inventory.APPROVED_NO_REDISTRIBUTION
    assert inventory.PUBLISH_BLOCKLIST["zillow_zori"] == ".local/consumer"
    assert source_notices.notice_for("zillow_zori")["attribution"] == "Data Provided by Zillow Group."
    card = json.load(open(os.path.join(ROOT, "docs", "data-cards", "zillow-zori-metro.json")))
    assert card["revision"] == 3 and card["source"]["archive_policy"].startswith(
        "GRANTED by Haoyang Shang (project lead), 2026-11-15")
    ok(test_the_rights_verdict_and_its_conditions)


def test_nothing_fetches_from_zillow():
    """Section 5 of Zillow's terms: no automated query, ever. No module, tool,
    collector or workflow may reach a Zillow host or import an HTTP client."""
    for path in ("ssa/adapters/zillow_zori.py", "tools/ingest_zori.py"):
        src = open(os.path.join(ROOT, path)).read()
        assert "import requests" not in src and "urllib.request" not in src, path
        assert "def capture" not in src and "def fetch" not in src, path
    from tools import fetch_track_data
    assert not any("zillow" in " ".join(x[2]) or x[1] == "zillow_zori"
                   for x in fetch_track_data.COLLECTORS)
    wf = os.path.join(ROOT, ".github", "workflows")
    for fn in os.listdir(wf):
        body = open(os.path.join(wf, fn)).read().lower()
        assert "zillow" not in body and "ingest_zori" not in body, fn
    ok(test_nothing_fetches_from_zillow)


def test_first_ingest_freezes_the_universe_and_keeps_the_raw_file_private():
    body = synthetic_csv("2026-12")
    with Sandbox() as sb:
        out = sb.ingest(body, "2026-12", "2027-01-17")
        assert out["created"] and out["universe_frozen"]
        digest = hashlib.sha256(body).hexdigest()
        raw = sb.archive / "raw" / "zillow_zori" / f"{digest}.csv"
        assert raw.read_bytes() == body
        universe = json.loads((sb.public / "universe.json").read_text())
        assert universe["profile"] == [str(900000 + i) for i in range(1, 11)]
        assert len(universe["universe"]) == 50 and "900051" not in universe["universe"]
        record = json.loads((sb.public / "2027-01-17.json").read_text())
        assert record["sha256"] == digest and record["newest_month"] == "2026-12"
        assert record["attribution"] == "Data Provided by Zillow Group"
        # Only derived changes are public: no level from the file appears.
        public_text = "".join(p.read_text() for p in sb.public.iterdir())
        assert "1013.0" not in public_text and z.FILE_NAME in public_text
        assert not any(p.suffix == ".csv" for p in sb.public.rglob("*"))
        assert len(record["profile"]["900001"]) == z.HISTORY_MONTHS
        ref = z.persistence_reference(record, "900001")
        assert ref["sd"] >= 0 and ref["mean"] == record["profile"]["900001"]["2026-12"]
        # The same bytes again are a no-op; other bytes on that day are refused.
        assert sb.ingest(body, "2026-12", "2027-01-17")["created"] is False
        refuses(lambda: sb.ingest(synthetic_csv("2026-12", {"900002": 1.001}),
                                  "2026-12", "2027-01-17"), "check (g)")
    ok(test_first_ingest_freezes_the_universe_and_keeps_the_raw_file_private)


def test_each_check_refuses_and_writes_nothing():
    body = synthetic_csv("2026-12")
    with Sandbox() as sb:
        refuses(lambda: sb.ingest(body, "2026-12", "2027-01-17",
                                  name="Metro_zori_uc_sfrcondomfr_sm_month.csv"), "check (a)")
        refuses(lambda: sb.ingest(body, "2027-01", "2027-01-17"), "check (c)")
        broken = body.replace(b"RegionID,SizeRank", b"RegionId,SizeRank", 1)
        refuses(lambda: sb.ingest(broken, "2026-12", "2027-01-17"), "header")
        no_us = b"\n".join(l for l in body.split(b"\n") if b"United States" not in l)
        refuses(lambda: sb.ingest(no_us, "2026-12", "2027-01-17"), "check (d)")
        assert not sb.public.exists() and not sb.archive.exists()
    ok(test_each_check_refuses_and_writes_nothing)


def test_continuity_against_the_previous_file():
    with Sandbox() as sb:
        sb.ingest(synthetic_csv("2026-12"), "2026-12", "2027-01-17")
        # A December change moved by ~0.5 points for one profile metro: the
        # wrong variant or a method change, refused for a maintainer.
        revised = synthetic_csv("2027-01", {"900003": 0.995})
        refuses(lambda: sb.ingest(revised, "2027-01", "2027-02-17"), "check (f)")
        out = sb.ingest(synthetic_csv("2027-01"), "2027-01", "2027-02-17")
        assert out["created"] and not out["universe_frozen"]
        refuses(lambda: sb.ingest(synthetic_csv("2027-01"), "2027-01", "2027-02-10"),
                "older than the newest")
        captures = json.loads((sb.public / "captures.json").read_text())
        assert sorted(captures) == ["2027-01-17", "2027-02-17"]
    ok(test_continuity_against_the_previous_file)


def test_the_raw_file_travels_encrypted_with_the_consumer_archive():
    """`tools/sync_consumer_archive.py push` packs every file under raw/ and
    stores the pack as age ciphertext on `sealed`; the ZORI raw file is in it."""
    import gzip
    from tools import sync_consumer_archive as sync
    body = synthetic_csv("2026-12")
    with Sandbox() as sb:
        sb.ingest(body, "2026-12", "2027-01-17")
        files = json.loads(gzip.decompress(sync.pack(sb.archive)))
        name = f"raw/zillow_zori/{hashlib.sha256(body).hexdigest()}.csv"
        assert sync.safe_path(name) and name in files
    # Inside the repository the private archive must stay under ignored .local.
    refuses(lambda: consumer.private_archive(os.path.join(ROOT, "sources", "zillow_zori")),
            ".local")
    ok(test_the_raw_file_travels_encrypted_with_the_consumer_archive)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"{len(PASSED)} passed")
