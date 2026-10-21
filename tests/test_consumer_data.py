"""Offline contract and failure tests. Generated samples are test data only."""
import copy
import importlib.util
import json
import sys
import tempfile
import unittest
import os
import requests
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ssa import consumer
from ssa.adapters import consumer as adapter
from tools import acsi_benchmarks

spec = importlib.util.spec_from_file_location("consumer_tool", ROOT / "tools/consumer_data.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

FINALS = b"Month,YYYY,PX_MD,PX5_MD\nAugust,2026,4.0,3.3\nSeptember,2026,4.6,3.4\n"
CURRENT = (b",,,EXPECTED CHANGE IN PRICES\nDATE OF SURVEY,,,NEXT YEAR,,NEXT 5 YEARS\n"
           b"September,2026,,4.6,,3.4\nOctober (P),2026,,4.7,,3.5\n")


class ConsumerTests(unittest.TestCase):
    def test_nyt_local_key_is_private_used_automatically_and_never_logged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(tool.sys.stdin, "isatty", return_value=True), \
                    mock.patch.object(tool.getpass, "getpass", return_value="example-nyt-key"), \
                    mock.patch.dict(os.environ, {"NYT_API_KEY": ""}):
                tool.configure_nyt(root)
                path = root / "credentials/nyt-api-key"
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
                self.assertEqual(tool.nyt_key(root), "example-nyt-key")
                error = requests.ConnectionError("Request failed: ?api-key=example-nyt-key")
                with mock.patch.object(tool.requests, "get", side_effect=error) as fetch:
                    tool.collect_source("nyt", root, 1)
                self.assertEqual(fetch.call_args.kwargs["params"], {"api-key": "example-nyt-key"})
                self.assertFalse(fetch.call_args.kwargs["allow_redirects"])
                self.assertNotIn("example-nyt-key", (root / "index.json").read_text())
                with mock.patch.dict(os.environ, {"NYT_API_KEY": "environment-key"}):
                    self.assertEqual(tool.nyt_key(root), "environment-key")
            with mock.patch.object(tool.sys.stdin, "isatty", return_value=False), \
                    mock.patch.object(tool.getpass, "getpass") as prompt, self.assertRaisesRegex(ValueError, "terminal"):
                tool.configure_nyt(root)
            prompt.assert_not_called()

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

    def netflix(self, count=10, country="US", category="TV"):
        header = "country_name\tcountry_iso2\tweek\tcategory\tweekly_rank\tshow_title\tseason_title\n"
        return (header + "".join(f"United States\t{country}\t2026-10-18\t{category}\t{i}\tTitle {i}\tSeason 1\n" for i in range(1, count + 1))).encode()

    def test_netflix_country_lists_require_complete_ranks(self):
        parsed = adapter.netflix_country(self.netflix())
        row = parsed["lists"]["netflix_us_tv"][0]
        self.assertEqual(len(row["items"]), 10)
        self.assertEqual(json.loads(row["items"][0]), ["Title 1", "Season 1"])
        self.assertNotIn("views", parsed["series"])
        for body in (self.netflix(9), self.netflix(country="JP"), self.netflix(category="Global")):
            with self.assertRaises(ValueError):
                adapter.netflix_country(body)

    def test_nyt_preserves_publication_and_sales_dates(self):
        books = [{"rank": i, "primary_isbn13": f"97800000000{i:02}", "title": f"Book {i}", "dagger": i == 1} for i in range(1, 11)]
        body = json.dumps({"status": "OK", "results": {"published_date": "2026-10-25", "bestsellers_date": "2026-10-10",
                                                     "list_name_encoded": "hardcover-fiction", "books": books}}).encode()
        parsed = adapter.nyt_books(body)
        self.assertEqual(parsed["lists"]["nyt_hardcover_fiction"][0]["week_end"], "2026-10-25")
        self.assertEqual(parsed["metadata"]["bestsellers_date"], "2026-10-10")
        self.assertTrue(parsed["metadata"]["books"][books[0]["primary_isbn13"]]["dagger"])

    def nyt_list(self, published="2026-10-25", slug="hardcover-fiction"):
        return json.dumps({"status": "OK", "results": {"published_date": published,
            "bestsellers_date": "2026-10-10", "list_name_encoded": slug,
            "books": [{"rank": i, "primary_isbn13": f"97800000000{i:02}"} for i in range(1, 11)]}}).encode()

    def test_nyt_history_rejects_a_different_returned_date_or_list(self):
        for body in (self.nyt_list(), self.nyt_list("2026-10-18", "hardcover-nonfiction")):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                consumer.ingest("nyt", self.nyt_list(), tool.URLS["nyt"], root)
                response = mock.Mock(status_code=200, content=body)
                with mock.patch.dict(os.environ, {"NYT_API_KEY": "example-key"}), \
                        mock.patch.object(tool.requests, "get", return_value=response):
                    tool.collect_source("nyt", root, 1, url=tool.URLS["nyt"].replace("current", "2026-10-18"))
                bundle = consumer.build_all(root)
                self.assertEqual(bundle["sources"]["nyt"]["status"], "parse_failure")
                self.assertTrue(bundle["sources"]["nyt"]["using_previous_vintage"])
                self.assertEqual(len(bundle["lists"]["nyt_hardcover_fiction"]), 1)

    def test_nyt_history_is_bounded_paced_and_resumes_from_newest_period(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            consumer.ingest("nyt", self.nyt_list(), tool.URLS["nyt"], root)
            responses = [mock.Mock(status_code=200, content=self.nyt_list(day))
                         for day in ("2026-10-18", "2026-10-11")]
            with mock.patch.dict(os.environ, {"NYT_API_KEY": "example-key"}), \
                    mock.patch.object(tool.requests, "get", side_effect=responses) as fetch, \
                    mock.patch.object(tool.time, "sleep") as pause:
                tool.collect_history("nyt", root, 1, weeks=3)
                self.assertEqual(fetch.call_count, 2)
                self.assertEqual(pause.call_args_list, [mock.call(13), mock.call(13)])
                tool.collect_history("nyt", root, 1, weeks=3)
                self.assertEqual(fetch.call_count, 2)
                self.assertEqual(pause.call_count, 2)
            rows = consumer.build_all(root)["lists"]["nyt_hardcover_fiction"]
            self.assertEqual([row["week_end"] for row in rows], ["2026-10-11", "2026-10-18", "2026-10-25"])

    def test_nyt_history_stops_on_rate_limit_without_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            consumer.ingest("nyt", self.nyt_list(), tool.URLS["nyt"], root)
            response = mock.Mock(status_code=429, content=b"Rate limit exceeded")
            with mock.patch.dict(os.environ, {"NYT_API_KEY": "example-key"}), \
                    mock.patch.object(tool.requests, "get", return_value=response) as fetch, \
                    mock.patch.object(tool.time, "sleep"):
                tool.collect_history("nyt", root, 1, weeks=52)
            fetch.assert_called_once()
            self.assertEqual(consumer.index(root)["sources"]["nyt"]["last_attempt"]["http_status"], 429)
            self.assertEqual(len(consumer.build_all(root)["lists"]["nyt_hardcover_fiction"]), 1)

    def test_steam_preserves_region_period_and_entity_type(self):
        body = ("country,week_start,week_end_exclusive,rank,entity_type,entity_id\n" +
                "".join(f"US,2026-10-06,2026-10-13,{i},app,{i}\n" for i in range(1, 11))).encode()
        parsed = adapter.steam_transcription(body)
        self.assertEqual(parsed["lists"]["steam_us_top_sellers"][0]["week_end"], "2026-10-12")
        for bad in (body.replace(b"US,", b"JP,"), body.replace(b"2026-10-06", b"2026-10-05")):
            with self.assertRaises(ValueError):
                adapter.steam_transcription(bad)

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

    def test_acsi_has_independent_company_cadence(self):
        body = (b"industry_id,company_id,company_name,study_year,published_date,score,evidence_url,evidence_quote\n"
                b"retail,example,Example,2025,2025-10-07,80,https://theacsi.org/example-2025/,Example scores 80.\n"
                b"retail,example,Example,2026,2026-10-06,82,https://theacsi.org/example-2026/,Example scores 82.\n")
        parsed = adapter.acsi_transcription(body)
        self.assertEqual(len(parsed["series"]["acsi_retail_example"]), 2)
        self.assertFalse(parsed["metadata"]["historical_first_print"])
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
            consumer.ingest("acsi", body, tool.URLS["acsi"], Path(directory))

    def test_acsi_publication_window_keeps_earlier_study_year(self):
        header = "industry_id,company_id,company_name,study_year,published_date,score,evidence_url,evidence_quote\n"
        row = "food,example,Example,2020,2021-01-26,82,https://theacsi.com/2021/01/26/example/,Example scores 82.\n"
        parsed = adapter.acsi_transcription((header + row).encode())
        self.assertEqual(parsed["series"]["acsi_food_example"], [{"date": "2021-01-26", "value": 82}])
        self.assertEqual(parsed["metadata"]["studies"][0]["study_year"], "2020")
        with self.assertRaisesRegex(ValueError, "scope"):
            adapter.acsi_transcription((header + row.replace("2021-01-26", "2020-01-26")).encode())
        with self.assertRaisesRegex(ValueError, "predates study"):
            adapter.acsi_transcription((header + row.replace(",2020,", ",2022,")).encode())

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
        for source in ("netflix", "mint"):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                response = mock.Mock(status_code=200, content=b"<html>Just a moment</html>")
                with mock.patch.object(tool.requests, "get", return_value=response):
                    tool.collect_source(source, root, timeout=1)
                bundle = consumer.build_all(root)
                self.assertFalse(bundle["series"] or bundle["lists"])
                self.assertNotEqual(bundle["sources"][source]["status"], "collected")

    def test_missing_key_is_explicit_and_offline_verify_never_fetches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.dict("os.environ", {}, clear=True), mock.patch.object(tool.requests, "get", side_effect=AssertionError("Unexpected network call")):
                tool.collect_source("nyt", root, 1)
                tool.main(["--archive", str(root), "verify"])
            self.assertEqual(consumer.index(root)["sources"]["nyt"]["last_attempt"]["status"], "credential_required")

    def test_archive_paths_and_credential_urls_are_rejected(self):
        with self.assertRaises(ValueError):
            consumer.official_url("nyt", tool.URLS["nyt"] + "?api-key=secret")
        with self.assertRaises(ValueError):
            consumer.archive_path(Path("/tmp/example"), "../../private")

    def steam_html(self, week=1791849600, country="US"):
        payload = {"route": "/charts/topsellers/:country?/:week?", "loaderData": [json.dumps({
            "weekly": {"strCountryCode": country, "rtWeekStart": week,
                       "rgRanks": [{"nRank": i, "itemKey": {"appid": i}} for i in range(1, 21)]}})]}
        return ('<html><script id="valve-ssr-data" type="application/json">' + json.dumps(payload) + '</script></html>').encode()

    def test_official_steam_parser_reads_chart_country_not_browsing_country(self):
        parsed = adapter.steam_official(self.steam_html())
        self.assertEqual(parsed["metadata"]["country"], "US")
        self.assertEqual(len(parsed["lists"]["steam_us_top_sellers"][0]["items"]), 10)
        with self.assertRaises(ValueError):
            adapter.steam_official(self.steam_html(country="JP"))

    def test_steam_history_keeps_all_vintages_and_rejects_wrong_returned_week(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            consumer.ingest("steam", self.steam_html(1791849600), tool.URLS["steam"] + "/2026-10-13", root)
            consumer.ingest("steam", self.steam_html(1791244800), tool.URLS["steam"] + "/2026-10-06", root)
            self.assertEqual(len(consumer.build_all(root)["lists"]["steam_us_top_sellers"]), 2)
            response = mock.Mock(status_code=200, content=self.steam_html(1791849600))
            with mock.patch.object(tool.requests, "get", return_value=response):
                tool.collect_source("steam", root, 1, url=tool.URLS["steam"] + "/2025-12-02")
            state = consumer.build_all(root)["sources"]["steam"]
            self.assertEqual(state["status"], "parse_failure")
            self.assertTrue(state["using_previous_vintage"])

    def test_steam_history_resume_uses_newest_period_not_last_fetched_page(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            consumer.ingest("steam", self.steam_html(1791849600), tool.URLS["steam"] + "/2026-10-13", root)
            consumer.ingest("steam", self.steam_html(1791244800), tool.URLS["steam"] + "/2026-10-06", root)
            with mock.patch.object(tool.requests, "get", side_effect=AssertionError("Already archived week was fetched")):
                tool.collect_history("steam", root, 1, weeks=2)

    def test_movement_uses_consecutive_changes_on_short_histories(self):
        bundle = {"series": {"test": [{"date": f"2026-0{i + 1}-01", "value": i} for i in range(4)]}, "lists": {}}
        self.assertEqual(consumer.measurements(bundle)["scalar"]["test"]["mean_absolute_change_36"], 1)

    def test_acsi_score_quotes_and_original_artifact_hashes_are_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = b"<html><p>Example scores 82.</p></html>"
            digest = consumer.sha256(raw)
            relative = f"corpus/acsi/{digest}.html"
            consumer.immutable(root / relative, raw)
            url = "https://theacsi.com/news-and-resources/press-releases/2026/10/06/example/"
            corpus = {"format": "ssa.consumer.corpus.v1", "documents": {url: {"status": "captured", "file": relative, "sha256": digest}}}
            row = {"company_id": "example", "study_year": "2026", "published_date": "2026-10-06", "score": "82", "evidence_url": url, "evidence_quote": "Example scores 82."}
            parsed = {"metadata": {"studies": [row]}}
            consumer.verify_acsi_evidence(parsed, consumer.encoded(corpus), root)
            row["score"] = "80"
            with self.assertRaisesRegex(ValueError, "score"):
                consumer.verify_acsi_evidence(parsed, consumer.encoded(corpus), root)
            row["score"] = "82"
            row["evidence_quote"] = "Example scores 85."
            with self.assertRaisesRegex(ValueError, "quotation"):
                consumer.verify_acsi_evidence(parsed, consumer.encoded(corpus), root)
            row["evidence_quote"] = "Example scores 82."
            (root / relative).write_bytes(raw + b"tampered")
            with self.assertRaisesRegex(ValueError, "hash"):
                consumer.verify_acsi_evidence(parsed, consumer.encoded(corpus), root)

    def test_acsi_tables_keep_cells_and_reject_selected_spanning_headers(self):
        body = (b"<table><tr><td>Outside</td></tr></table><main>"
                b"<table><tr><th>Company</th><th>2026</th></tr>"
                b"<tr><td><b>AT&amp;T</b></td><td>82</td></tr></table>"
                b"<table><tr><th colspan='2'>Other dimension</th></tr></table></main>")
        self.assertEqual(adapter.publisher_tables(body, selected=0)[1], ["AT&T", "82"])
        with self.assertRaisesRegex(ValueError, "Spanning"):
            adapter.publisher_tables(body, selected=1)

    def test_acsi_table_evidence_binds_score_year_company_and_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            body = (b"<main><table><tr><th>Company</th><th>2025</th><th>2026</th></tr>"
                    b"<tr><td>Example</td><td>80</td><td>82</td></tr></table></main>")
            url = "https://theacsi.com/industries/manufacturing/cell-phones/"
            release = "https://theacsi.com/news-and-resources/press-releases/2026/05/19/example/"
            documents = {}
            for artifact_url, content in [(url, body), (release, b"<main>Dated study release</main>")]:
                digest = consumer.sha256(content)
                file = f"corpus/acsi/{digest}.html"
                consumer.immutable(root / file, content)
                documents[artifact_url] = {"file": file, "sha256": digest, "status": "captured"}
            evidence = consumer.encoded({"format": "ssa.consumer.corpus.v1", "documents": documents})
            row = {"company_id": "example", "company_name": "Example", "score": "82",
                "study_year": "2026", "published_date": "2026-05-19", "evidence_url": url,
                "evidence_quote": "Example 80 82", "evidence_kind": "benchmark_table",
                "evidence_table": "0", "evidence_row": "1", "evidence_column": "2",
                "evidence_name_column": "0", "evidence_header": '["Company", "2025", "2026"]',
                "release_url": release}
            parsed = {"metadata": {"studies": [row]}}
            consumer.verify_acsi_evidence(parsed, evidence, root)
            for field, value, error in [("score", "80", "cell"), ("evidence_column", "1", "year"),
                    ("company_name", "Someone Else", "company"), ("published_date", "2026-05-20", "date")]:
                original = row[field]
                row[field] = value
                with self.subTest(field=field), self.assertRaisesRegex(ValueError, error):
                    consumer.verify_acsi_evidence(parsed, evidence, root)
                row[field] = original

    def test_acsi_snapshot_skips_na_and_keeps_unmapped_dates_pending(self):
        url = acsi_benchmarks.PREFIX + "manufacturing/cell-phones/"
        body = (b"<main><table><tr><th>Company</th><th>2025</th><th>2026</th><th>% Change</th></tr>"
                b"<tr><td>Example</td><td>NA</td><td>82</td><td>NA</td></tr></table></main>")
        rows, pending = acsi_benchmarks.selected_rows(url, body, {})
        self.assertEqual(rows, [])
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["year"], 2026)
        release = "https://theacsi.com/news-and-resources/press-releases/2026/05/19/telecommunications-study-2026/"
        rows, pending = acsi_benchmarks.selected_rows(url, body, {release: {"status": "captured"}})
        self.assertEqual(pending, [])
        self.assertEqual((rows[0]["score"], rows[0]["published_date"]), (82, "2026-05-19"))
        self.assertIn("first_print_unverified", rows[0]["value_vintage"])

    def test_acsi_reused_url_and_ambiguous_release_require_review(self):
        url = acsi_benchmarks.PREFIX + "interactive-media-subscription-entertainment/search-engines-and-information/"
        body = (b"<main><table><tr><th>Brand</th><th>2025</th><th>2026</th></tr>"
                b"<tr><td>Search Engines</td><td>80</td><td>82</td></tr></table></main>")
        with self.assertRaisesRegex(ValueError, "population"):
            acsi_benchmarks.selected_rows(url, body, {})
        releases = {f"https://theacsi.com/news-and-resources/press-releases/2026/05/{day}/telecommunications/":
                    {"status": "captured"} for day in ("19", "20")}
        with self.assertRaisesRegex(ValueError, "found 2"):
            acsi_benchmarks.release_for(releases, 2026, "telecommunications")

    def test_acsi_offline_rebuild_reuses_identical_vintage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            suffix = "manufacturing/cell-phones/"
            url = acsi_benchmarks.PREFIX + suffix
            release = "https://theacsi.com/news-and-resources/press-releases/2026/05/19/telecommunications-study-2026/"
            table = (b"<main><table><tr><th>Company</th><th>2026</th></tr>"
                     b"<tr><td>Example</td><td>82</td></tr></table></main>")
            for label, artifact_url, body in [("acsi", release, b"<main>Study release</main>"),
                    ("acsi-benchmarks", url, table)]:
                digest = consumer.sha256(body)
                relative = f"corpus/{label}/{digest}.html"
                consumer.immutable(root / relative, body)
                consumer.write_json(root / f"corpus/{label}.json", {"format": "ssa.consumer.corpus.v1",
                    "documents": {artifact_url: {"status": "captured", "file": relative, "sha256": digest}}})
            with mock.patch.object(acsi_benchmarks, "PROFILES", {suffix: ("telecommunications", (0,))}):
                with mock.patch.object(consumer, "now", return_value="2026-10-21T12:00:00Z"):
                    acsi_benchmarks.build(root)
                with mock.patch.object(consumer, "now", return_value="2026-10-21T12:01:00Z"):
                    acsi_benchmarks.build(root)
            self.assertEqual(len(consumer.index(root)["sources"]["acsi"]["vintages"]), 1)
            self.assertEqual(consumer.build_all(root)["series"]["acsi_cell_phones_example"],
                             [{"date": "2026-05-19", "value": 82}])

    def test_acsi_release_selection_preserves_reviewed_identity_and_publisher_date(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            url = "https://theacsi.com/news-and-resources/press-releases/2021/08/24/example/"
            body = b"<main>Example scores 82.</main>"
            digest = consumer.sha256(body)
            relative = f"corpus/acsi/{digest}.html"
            consumer.immutable(root / relative, body)
            consumer.write_json(root / "corpus/acsi.json", {"format": "ssa.consumer.corpus.v1", "documents": {
                url: {"status": "captured", "file": relative, "sha256": digest}}})
            selection = root / "selection.json"
            consumer.write_json(selection, {"format": "ssa.consumer.acsi.release-selection.v1", "industry_id": "automobiles",
                "aliases": {"Example": "canonical_brand", "car_rentals:Example": "rental_brand"},
                "studies": [{"study_year": 2021, "release_url": url, "selections": [
                {"quote": "Example scores 82.", "scores": {"Example": 82}},
                {"industry_id": "car_rentals", "quote": "Example scores 82.", "scores": {"Example": 82}}]}]})
            tool.transcribe_acsi(selection, root)
            bundle = consumer.build_all(root)
            self.assertEqual(bundle["series"]["acsi_automobiles_canonical_brand"], [{"date": "2021-08-24", "value": 82}])
            self.assertEqual(bundle["series"]["acsi_car_rentals_rental_brand"], [{"date": "2021-08-24", "value": 82}])
            self.assertFalse(bundle["sources"]["acsi"]["metadata"]["historical_first_print"])


if __name__ == "__main__":
    unittest.main()
