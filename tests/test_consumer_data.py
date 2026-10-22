"""Offline contract and failure tests. Generated samples are test data only."""
import copy
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ssa import consumer
from ssa.adapters import consumer as adapter

spec = importlib.util.spec_from_file_location("consumer_tool", ROOT / "tools/consumer_data.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

FINALS = b"Month,YYYY,PX_MD,PX5_MD\nAugust,2026,4.0,3.3\nSeptember,2026,4.6,3.4\n"
# Layout of the real tbcpx1px5.csv: CRLF, 15 columns, blank padding rows.
PAD = b"," * 14 + b"\r\n"
CURRENT = (b",,,EXPECTED CHANGE IN PRICES,,,,,,,,,,,\r\n" + PAD + PAD +
           b"DATE OF SURVEY,,,NEXT YEAR,,NEXT 5 YEARS,,,,,,,,,\r\n" + PAD +
           b"September,2026,,4.6,,3.4,,,,,,,,,\r\nOctober (P),2026,,4.7,,3.5,,,,,,,,,\r\n" + PAD)


class ConsumerTests(unittest.TestCase):
    def test_cli_accepts_only_the_four_candidate_sources(self):
        self.assertEqual(sorted(adapter.PARSERS), ["michigan_current", "michigan_finals", "mint", "sce"])
        self.assertEqual(sorted(tool.URLS), sorted(adapter.PARSERS))
        with tempfile.TemporaryDirectory() as directory:
            sample = Path(directory) / "sample.csv"
            sample.write_bytes(FINALS)
            for command in (["collect", "--sources", "netflix"], ["collect", "--sources", "nyt"],
                            ["fetch", "steam", "https://store.steampowered.com/charts/topsellers/US"],
                            ["import", "acsi", str(sample), "--url", "https://theacsi.com/"],
                            ["history", "nyt"]):
                with self.subTest(command=command), mock.patch.object(tool.requests, "get",
                        side_effect=AssertionError("Unexpected network request")), \
                        mock.patch.object(sys, "stderr", io.StringIO()), self.assertRaises(SystemExit):
                    tool.main(["--archive", directory, *command])

    def test_michigan_preliminary_appends_but_does_not_overwrite_finals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            consumer.ingest("michigan_finals", FINALS, tool.URLS["michigan_finals"], root)
            consumer.ingest("michigan_current", CURRENT, tool.URLS["michigan_current"], root)
            bundle = consumer.build_all(root)
            points = bundle["series"]["umich_inflation_1y"]
            self.assertEqual(points[-1], {"date": "2026-10-01", "value": 4.7})
            final_october = FINALS + b"October,2026,4.8,3.6\n"
            consumer.ingest("michigan_finals", final_october, tool.URLS["michigan_finals"], root)
            points = consumer.build_all(root)["series"]["umich_inflation_1y"]
            self.assertEqual(points[-1]["value"], 4.8)
            self.assertEqual(len(points), 3)
            self.assertFalse(bundle["sources"]["michigan_current"]["metadata"]["historical_first_print"])
            tool.validate(bundle)

    def test_michigan_missing_month_is_explicit_not_zero(self):
        parsed = adapter.michigan_finals(b"Month,YYYY,PX_MD,PX5_MD\nJanuary,1978,5.2,\nFebruary,1978,6.4,7.2\n")
        self.assertEqual(len(parsed["series"]["umich_inflation_5_10y"]), 1)
        self.assertEqual(parsed["metadata"]["missing_months"]["umich_inflation_5_10y"], ["1978-01-01"])

    def test_bad_data_fails_instead_of_becoming_an_empty_success(self):
        for body in (b"<html>Login</html>", b"Month,YYYY,PX_MD,PX5_MD\nSeptember,2026,nan,3\n",
                     b"Month,YYYY,PX_MD,PX5_MD\nSeptember,2026,4,3\nSeptember,2026,5,3\n",
                     b"Month,YYYY,PX_MD,PX5_MD\nSeptembre,2026,4,3\n"):
            with self.subTest(body=body), self.assertRaises(ValueError):
                adapter.michigan_finals(body)
        with self.assertRaises(ValueError):
            adapter.michigan_current(CURRENT.replace(b"NEXT YEAR", b"RENAMED"))

    def test_sce_exact_headers_survive_column_reordering(self):
        fixture_spec = importlib.util.spec_from_file_location("sce_fixture", ROOT / "tests/test_sce.py")
        fixture = importlib.util.module_from_spec(fixture_spec)
        fixture_spec.loader.exec_module(fixture)
        body = fixture.workbook([(201306, {"B": 1, "C": 2, "D": 3})])
        fields = {"candidate": ("Inflation expectations", "Median one-year ahead expected inflation rate", "percent")}
        self.assertEqual(adapter.sce_extensions(body, fields)["series"]["candidate"][0]["value"], 3)
        with self.assertRaises(ValueError):
            adapter.sce_extensions(body, {"candidate": ("Inflation expectations", "Renamed", "percent")})

    def test_mint_keeps_negative_adjustments_and_does_not_bridge_missing_weeks(self):
        body = (b"product_id,period_end,cumulative_net_sales\nSET,2026-10-04,100\n"
                b"SET,2026-10-11,90\nSET,2026-10-25,130\n")
        parsed = adapter.mint_transcription(body)
        self.assertEqual(parsed["series"]["mint_SET_weekly_net_sales"], [{"date": "2026-10-11", "value": -10}])
        self.assertEqual(parsed["metadata"]["skipped_nonweekly_differences"]["SET"], ["2026-10-25"])

    def mint_csv(self, day="10/18/2026", value=90):
        return ("Program Name,Item,Item Description,Adj. Net Demand,Date Sales Report is Valid\r\n"
                f"Annual Sets,26RH,Example Silver Set,{value},{day}\r\n").encode()

    def test_mint_official_csv_preserves_labels_and_rejects_invalid_reports(self):
        body = self.mint_csv()
        parsed = adapter.mint(b"\xef\xbb\xbf" + body)
        self.assertEqual(parsed["series"]["mint_26RH_cumulative"], [{"date": "2026-10-18", "value": 90}])
        self.assertEqual(parsed["metadata"]["products"]["26RH"]["description"], "Example Silver Set")
        self.assertNotIn("mint_26RH_weekly_net_sales", parsed["series"])
        for invalid in (self.mint_csv("10/19/2026"), self.mint_csv(value=1.5),
                        body + body.splitlines(keepends=True)[1],
                        body.replace(b"Adj. Net Demand", b"Gross Demand"),
                        body.replace(b"Example Silver Set", b"")):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                adapter.mint(invalid)

    def test_mint_weekly_changes_join_separate_reports_in_calendar_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for body in (self.mint_csv(), self.mint_csv("10/11/2026", 100), self.mint_csv("10/04/2026", 100)):
                consumer.ingest("mint", body, tool.URLS["mint"], root, evidence_body=body, evidence_suffix=".csv")
            bundle = consumer.build_all(root)
            self.assertEqual(bundle["series"]["mint_26RH_weekly_net_sales"], [
                {"date": "2026-10-11", "value": 0}, {"date": "2026-10-18", "value": -10}])
            self.assertEqual(len(bundle["series"]["mint_26RH_cumulative"]), 3)
            self.assertEqual(len(bundle["provenance"]["mint_26RH_weekly_net_sales"]), 3)
            tool.validate(bundle)

    def test_mint_join_keeps_first_capture_flags_revisions_and_skips_gaps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for body in (self.mint_csv("10/04/2026", 100), self.mint_csv(), self.mint_csv("10/18/2026", 95)):
                consumer.ingest("mint", body, tool.URLS["mint"], root, evidence_body=body, evidence_suffix=".csv")
            bundle = consumer.build_all(root)
            self.assertNotIn("mint_26RH_weekly_net_sales", bundle["series"])
            self.assertEqual(bundle["series"]["mint_26RH_cumulative"][-1]["value"], 90)
            self.assertEqual(len(bundle["sources"]["mint"]["revision_conflicts"]), 1)
            self.assertEqual(bundle["sources"]["mint"]["metadata"]["skipped_nonweekly_differences"]["26RH"], ["2026-10-18"])

    def test_failed_attempt_keeps_previous_vintage_and_tampering_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = consumer.ingest("michigan_finals", FINALS, tool.URLS["michigan_finals"], root)
            consumer.record_attempt(root, "michigan_finals", "http_failure", "HTTP 403")
            bundle = consumer.build_all(root)
            self.assertTrue(bundle["sources"]["michigan_finals"]["using_previous_vintage"])
            self.assertEqual(len(bundle["series"]), 2)
            (root / manifest["raw_file"]).write_bytes(FINALS + b"tampered")
            with self.assertRaisesRegex(ValueError, "hash/length"):
                consumer.build_all(root)

    def test_append_preserves_live_data_and_refuses_collisions(self):
        base = {"series_tail": {"existing": [{"date": "2026-10-01", "value": 1}]}, "rounds": [{"round_id": "locked"}]}
        before = copy.deepcopy(base)
        bundle = {"series": {"new": [{"date": "2026-10-01", "value": 2}]}, "lists": {}, "review_status": "pending"}
        appended = consumer.append_review(base, bundle)
        self.assertEqual(base, before)
        self.assertEqual(appended["rounds"], base["rounds"])
        self.assertEqual(appended["series_tail"]["existing"], base["series_tail"]["existing"])
        with self.assertRaises(ValueError):
            consumer.append_review(appended, bundle)

    def test_http_challenge_and_login_html_are_not_observations(self):
        for source in ("michigan_finals", "mint"):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                response = mock.Mock(status_code=200, content=b"<html>Just a moment</html>")
                with mock.patch.object(tool.requests, "get", return_value=response):
                    tool.collect_source(source, root, timeout=1)
                bundle = consumer.build_all(root)
                self.assertFalse(bundle["series"] or bundle["lists"])
                self.assertNotEqual(bundle["sources"][source]["status"], "collected")

    def test_offline_verify_never_fetches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            consumer.ingest("michigan_finals", FINALS, tool.URLS["michigan_finals"], root)
            with mock.patch.object(tool.requests, "get", side_effect=AssertionError("Unexpected network call")), \
                    mock.patch.object(sys, "stdout", io.StringIO()):
                tool.main(["--archive", str(root), "verify"])

    def test_archive_paths_and_credential_urls_are_rejected(self):
        with self.assertRaises(ValueError):
            consumer.official_url("michigan_finals", tool.URLS["michigan_finals"] + "?api-key=secret")
        with self.assertRaises(KeyError):
            consumer.official_url("nyt", "https://api.nytimes.com/svc/books/v3/lists/current/hardcover-fiction.json")
        with self.assertRaises(ValueError):
            consumer.archive_path(Path("/tmp/example"), "../../private")

    def test_movement_uses_consecutive_changes_on_short_histories(self):
        bundle = {"series": {"test": [{"date": f"2026-0{i + 1}-01", "value": i} for i in range(4)]}, "lists": {}}
        self.assertEqual(consumer.measurements(bundle)["scalar"]["test"]["mean_absolute_change_36"], 1)


if __name__ == "__main__":
    unittest.main()
