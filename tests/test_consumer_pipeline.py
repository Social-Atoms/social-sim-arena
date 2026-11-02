"""Synthetic publisher artifacts through production freeze, resolution and scores."""
import copy
import io
import json
import os
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from types import SimpleNamespace
from xml.etree import ElementTree as ET

from ssa import agent_api, bundle, consumer, consumer_round, inventory, ranking_round, refresh, resolve, scoring, season, stamps
from tools import validate_submission
from ssa.adapters import consumer as adapters
from tools import consumer_data, run_consumer_cycle


def moment(value):
    return consumer_round.timestamp(value)


def survey_round(source="michigan_current"):
    sid = {"sce": "sce_spending_growth", "michigan_current": "umich_inflation_1y_preliminary",
           "michigan_finals": "umich_inflation_1y_final"}[source]
    return {"round_id": "consumer-" + sid.replace("_", "-"), "series": sid, "tracker": source,
            "question": "Predict the next published survey value", "unit": "percent",
            "target_type": "continuous_normal", "release_estimated": False,
            "lock_at": "2026-10-21T14:00:00Z", "release_at": "2026-10-23T14:00:00Z",
            "resolve": "first archived official publisher artifact for the named period",
            "consumer": {"source": source, "reference_date": "2026-10-01",
                         "capture_until": "2026-10-24T14:00:00Z",
                         "population": "US households", "calendar_url": "https://example.org/calendar"}}


def chart_round(source="nyt"):
    r = survey_round()
    start, end = ("2026-10-04", "2026-10-10") if source == "nyt" else ("2026-10-06", "2026-10-12")
    r.update(round_id="consumer-" + source, series="nyt_bestsellers" if source == "nyt"
             else "steam_weekly_top_sellers", tracker=source, target_type="ranking_list",
             lock_at="2026-10-02T00:00:00Z", release_at="2026-10-14T14:00:00Z",
             question="Predict next week's top three, in rank order", unit="ordered publisher IDs")
    r["ranking"] = {"kind": "nyt_bestsellers" if source == "nyt" else "steam_weekly",
                    "length": 3, "loss": "rbo", "rbo_p": 0.9, "week_start": start, "week_end": end}
    r["consumer"] = {"source": source, "reference_date": end,
                     "capture_until": "2026-10-15T14:00:00Z", "population": "US purchasers",
                     "calendar_url": "https://example.org/calendar",
                     "measurement_start": start + "T00:00:00Z",
                     "measurement_end": ("2026-10-11" if source == "nyt" else "2026-10-13") + "T00:00:00Z"}
    if source == "nyt":
        r["consumer"].update(list_name="hardcover-fiction", published_date="2026-10-18")
    else:
        r["consumer"]["country"] = "US"
    return r


def mint_round():
    r = survey_round()
    r.update(round_id="consumer-mint-example", tracker="mint", series="mint_weekly_net_sales",
             lock_at="2026-10-11T23:00:00Z", release_at="2026-10-20T16:00:00Z")
    r["consumer"] = {"source": "mint", "product_id": "EXAMPLE", "reference_date": "2026-10-18",
                     "capture_until": "2026-10-21T16:00:00Z", "population": "General public buyers",
                     "calendar_url": "https://www.usmint.gov/about/production-sales-figures/cumulative-sales",
                     "measurement_start": "2026-10-12T00:00:00Z",
                     "measurement_end": "2026-10-19T00:00:00Z",
                     "product": {"fixed_supply": True, "general_public_buyers": True,
                                 "batches_known": True, "not_sold_out": True, "supply_limit": 10000,
                                 "evidence_url": "https://www.usmint.gov/example"}}
    return r


def current(values):
    rows = [",,,EXPECTED CHANGE IN PRICES", "DATE OF SURVEY,,,NEXT YEAR,,NEXT 5 YEARS"]
    rows += [f"{month} (P),2026,,{value},,3.0" for month, value in values]
    return ("\n".join(rows) + "\n").encode()


def sce_workbook(months):
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    wb = ET.Element("workbook", xmlns=ns)
    sheets = ET.SubElement(wb, "sheets")
    relationships = ET.Element("Relationships")
    strings = list(dict.fromkeys(field[1] for field in adapters.SCE_FIELDS.values()))
    shared = ET.Element("sst", xmlns=ns)
    for text in strings:
        ET.SubElement(ET.SubElement(shared, "si"), "t").text = text
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for i, (name, header, _) in enumerate(adapters.SCE_FIELDS.values(), 1):
            ET.SubElement(sheets, "sheet", name=name, **{f"{{{rel}}}id": f"rId{i}"})
            ET.SubElement(relationships, "Relationship", Id=f"rId{i}", Target=f"worksheets/sheet{i}.xml")
            sheet = ET.Element("worksheet", xmlns=ns)
            data = ET.SubElement(sheet, "sheetData")
            row = ET.SubElement(data, "row")
            ET.SubElement(ET.SubElement(row, "c", r="C1", t="s"), "v").text = str(strings.index(header))
            for j, (month, value) in enumerate(months, 2):
                row = ET.SubElement(data, "row")
                for col, val in (("A", month), ("C", value)):
                    ET.SubElement(ET.SubElement(row, "c", r=f"{col}{j}"), "v").text = str(val)
            archive.writestr(f"xl/worksheets/sheet{i}.xml", ET.tostring(sheet))
        archive.writestr("xl/workbook.xml", ET.tostring(wb))
        archive.writestr("xl/_rels/workbook.xml.rels", ET.tostring(relationships))
        archive.writestr("xl/sharedStrings.xml", ET.tostring(shared))
    return out.getvalue()


def nyt_body(end="2026-10-10", published="2026-10-18", ids=(4, 5, 6)):
    return consumer.encoded({"status": "OK", "results": {
        "list_name_encoded": "hardcover-fiction", "published_date": published, "bestsellers_date": end,
        "books": [{"rank": i, "primary_isbn13": str(1000000000000 + value)}
                  for i, value in enumerate(ids, 1)]}})


def steam_body(start="2026-10-06", ids=(4, 5, 6)):
    stamp = int(moment(start + "T00:00:00Z").timestamp())
    return consumer.encoded({"response": {"start_date": stamp,
        "ranks": [{"rank": i, "appid": value} for i, value in enumerate(ids, 1)]}}), \
        {"country_code": "US", "start_date": stamp}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.patch = mock.patch.object(consumer, "DEFAULT_ARCHIVE", self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def ingest(self, source, body, at, context=None):
        return consumer.ingest(source, body, consumer_data.URLS[source], self.root,
                               captured_at=at, request_context=context,
                               evidence_body=body if source == "mint" else None,
                               evidence_suffix=".csv")

    def test_sce_all_three_priority_fields_reach_native_resolution_and_crps(self):
        initial = [(202600 + m, 2 + m / 10) for m in range(6, 10)]
        self.ingest("sce", sce_workbook(initial), "2026-10-01T00:00:00Z")
        for sid in ("sce_spending_growth", "sce_stock_prices_rise", "sce_unemployment_rise"):
            r = survey_round("sce")
            r.update(series=sid, round_id="consumer-" + sid.replace("_", "-"))
            self.assertEqual(len(consumer_round.history(r)), 4)
        self.ingest("sce", sce_workbook(initial + [(202610, 3.5)]), "2026-10-23T14:01:00Z")
        for sid in ("sce_spending_growth", "sce_stock_prices_rise", "sce_unemployment_rise"):
            r = survey_round("sce")
            r.update(series=sid, round_id="consumer-" + sid.replace("_", "-"))
            outcome, why = resolve.resolve_round(r, {}, moment("2026-10-23T15:00:00Z"))
            self.assertIsNone(why)
            self.assertEqual(outcome["value"], 3.5)
            self.assertGreater(scoring.crps_normal(3, 1, outcome["value"]), 0)

    def test_preliminary_and_unchanged_final_are_distinct_answers(self):
        self.ingest("michigan_current", current([("October", 4.7)]), "2026-10-23T14:01:00Z")
        body = b"Month,YYYY,PX_MD,PX5_MD\nOctober,2026,4.7,3.0\n"
        self.ingest("michigan_finals", body, "2026-10-23T14:02:00Z")
        prelim = consumer_round.resolution(survey_round(), moment("2026-10-23T15:00:00Z"))
        final = consumer_round.resolution(survey_round("michigan_finals"), moment("2026-10-23T15:00:00Z"))
        self.assertEqual(prelim["value"], final["value"])
        self.assertNotEqual(prelim["raw_sha256"], final["raw_sha256"])
        new, skipped = resolve.resolve_all({"rounds": [survey_round(), survey_round("michigan_finals")]},
                                          {}, {}, moment("2026-10-23T15:00:00Z"))
        self.assertEqual(len(new), 2)
        self.assertFalse(skipped)

    def test_first_vintage_wins_over_revision(self):
        self.ingest("michigan_current", current([("October", 4.7)]), "2026-10-23T14:01:00Z")
        self.ingest("michigan_current", current([("October", 9.9)]), "2026-10-23T14:02:00Z")
        self.assertEqual(consumer_round.resolution(survey_round(), moment("2026-10-24T00:00:00Z"))["value"], 4.7)

    def test_a_known_answer_cannot_be_reused_as_a_future_release(self):
        self.ingest("michigan_current", current([("October", 4.7)]), "2026-10-20T00:00:00Z")
        self.ingest("michigan_current", current([("October", 4.7)]), "2026-10-23T14:01:00Z")
        with self.assertRaisesRegex(ValueError, "already captured"):
            consumer_round.resolution(survey_round(), moment("2026-10-23T15:00:00Z"))

    def test_late_capture_wrong_period_and_wrong_stage_cannot_settle(self):
        for source, body, at in (
                ("michigan_current", current([("September", 4.7)]), "2026-10-23T14:01:00Z"),
                ("michigan_current", current([("October", 4.7)]), "2026-10-25T00:00:00Z")):
            self.ingest(source, body, at)
        with self.assertRaisesRegex(ValueError, "exact-period"):
            consumer_round.resolution(survey_round(), moment("2026-10-26T00:00:00Z"))
        with self.assertRaises(ValueError):
            consumer_round.resolution(survey_round("michigan_finals"), moment("2026-10-26T00:00:00Z"))

    def test_history_excludes_later_capture_even_with_an_old_reference_date(self):
        self.ingest("michigan_current", current([("August", 4.1)]), "2026-10-01T00:00:00Z")
        self.ingest("michigan_current", current([("August", 8.0), ("September", 8.1)]), "2026-10-20T14:00:00Z")
        hist = consumer_round.history(survey_round())
        self.assertEqual([(p["date"], p["value"]) for p in hist], [("2026-08-01", 4.1)])

    def test_mint_two_reports_settle_net_change_including_returns(self):
        r = mint_round()
        for day, value, at in (("2026-10-11", 100, "2026-10-13T17:00:00Z"),
                               ("2026-10-18", 90, "2026-10-20T17:00:00Z")):
            self.ingest("mint", f"product_id,period_end,cumulative_net_sales\nEXAMPLE,{day},{value}\n".encode(), at)
        answer = consumer_round.resolution(r, moment("2026-10-20T18:00:00Z"))
        self.assertEqual(answer["value"], -10)
        self.assertIn("previous_raw_sha256", answer)
        r["consumer"]["product"]["fixed_supply"] = False
        with self.assertRaisesRegex(ValueError, "fixed-supply"):
            consumer_round.spec_for(r)

    def test_equal_sales_in_distinct_mint_products_are_distinct_targets(self):
        rounds = []
        for product in ("EXAMPLE", "OTHER"):
            r = mint_round()
            r["round_id"] += "-" + product.lower()
            r["consumer"]["product_id"] = product
            rounds.append(r)
        for day, value, at in (("2026-10-11", 100, "2026-10-13T17:00:00Z"),
                               ("2026-10-18", 90, "2026-10-20T17:00:00Z")):
            body = ("product_id,period_end,cumulative_net_sales\n" + "".join(
                f"{product},{day},{value}\n" for product in ("EXAMPLE", "OTHER"))).encode()
            self.ingest("mint", body, at)
        new, skipped = resolve.resolve_all({"rounds": rounds}, {}, {}, moment("2026-10-20T18:00:00Z"))
        self.assertEqual(len(new), 2)
        self.assertFalse(skipped)

    def test_weekly_windows_follow_local_calendar_days_across_dst(self):
        r = chart_round("steam")
        r.update(lock_at="2026-10-26T23:00:00Z", release_at="2026-11-03T16:00:00Z")
        r["ranking"].update(week_start="2026-10-27", week_end="2026-11-02")
        r["consumer"].update(reference_date="2026-11-02", measurement_timezone="America/Los_Angeles",
                             measurement_start="2026-10-27T01:00:00-07:00",
                             measurement_end="2026-11-03T01:00:00-08:00",
                             capture_until="2026-11-04T16:00:00Z")
        self.assertEqual(consumer_round.spec_for(r)["reference_date"], "2026-11-02")

    def test_missing_mint_report_is_never_filled(self):
        self.ingest("mint", b"product_id,period_end,cumulative_net_sales\nEXAMPLE,2026-10-18,90\n",
                    "2026-10-20T17:00:00Z")
        with self.assertRaisesRegex(ValueError, "both adjacent"):
            consumer_round.resolution(mint_round(), moment("2026-10-20T18:00:00Z"))

    def test_both_chart_sources_allow_all_new_entries_and_score_membership_and_order(self):
        for source in ("nyt", "steam"):
            r = chart_round(source)
            if source == "nyt":
                self.ingest(source, nyt_body(), "2026-10-14T14:01:00Z")
            else:
                body, context = steam_body()
                self.ingest(source, body, "2026-10-14T14:01:00Z", context)
            spec = ranking_round.spec_for(r)
            outcome = consumer_round.resolution(r, moment("2026-10-14T15:00:00Z"))["items"]
            perfect = ranking_round.score_submission({"ranking": outcome}, outcome, spec)
            reversed_score = ranking_round.score_submission({"ranking": list(reversed(outcome))}, outcome, spec)
            absent = ["isbn:1000000000001", "isbn:1000000000002", "isbn:1000000000003"] if source == "nyt" \
                else ["app:1", "app:2", "app:3"]
            self.assertEqual(perfect["loss"], 0)
            self.assertGreater(reversed_score["loss"], 0)
            self.assertEqual(ranking_round.score_list(absent, outcome, spec)["loss"], 1)
            with self.assertRaises(ValueError):
                ranking_round.submission_list({"ranking": outcome[:2] + outcome[:1]}, spec)

    def test_steam_rejects_empty_response_and_countryless_or_wrong_week_provenance(self):
        body, context = steam_body()
        for data, ctx in ((b'{"response":{}}', context), (body, None),
                          (body, {**context, "start_date": context["start_date"] + 604800})):
            with self.assertRaises((ValueError, KeyError)):
                adapters.steam_weekly(data, ctx)

    def test_steam_preserves_store_item_types(self):
        body, context = steam_body()
        data = json.loads(body)
        data["response"]["ranks"] = [{"rank": i + 1, "item": {"item_type": i, "id": 123}}
                                      for i in range(3)]
        point = adapters.steam_weekly(consumer.encoded(data), context)["lists"]["steam_US"][0]
        self.assertEqual(point["items"], ["app:123", "package:123", "bundle:123"])

    def test_submission_api_and_bundle_describe_and_accept_publisher_ids(self):
        import jsonschema
        for source in ("nyt", "steam"):
            r = chart_round(source)
            items = ["isbn:1000000000001", "isbn:1000000000002", "isbn:1000000000003"] if source == "nyt" \
                else ["app:1", "package:1", "bundle:1"]
            validate_submission.check_ranking("test.json", {"ranking": items}, r)
            with mock.patch.object(validate_submission, "fail", side_effect=ValueError), \
                    self.assertRaises(ValueError):
                validate_submission.check_ranking("test.json", {"ranking": ["Main_Page", "not-an-id", "other"]}, r)
            envelope = agent_api.build_envelope("test-entrant", r)
            schema = json.loads((consumer.ROOT / "schema/agent-api-request.schema.json").read_text())
            jsonschema.validate(envelope, schema)
            self.assertEqual(envelope["round"]["context"]["consumer"], r["consumer"])
            self.assertIn("isbn:" if source == "nyt" else "package:", envelope["round"]["ranking"]["item_format"])
            question = bundle.question_from_round(r)
            self.assertEqual(question["consumer"], r["consumer"])
            self.assertIn("New entries", question["item_format"])

    def test_ranking_lock_must_precede_week_and_nyt_publication_must_match(self):
        r = chart_round()
        r["lock_at"] = "2026-10-04T00:00:00Z"
        with self.assertRaisesRegex(ValueError, "lock before"):
            ranking_round.spec_for(r)
        self.ingest("nyt", nyt_body(published="2026-10-25"), "2026-10-14T14:01:00Z")
        with self.assertRaisesRegex(ValueError, "publication date"):
            consumer_round.resolution(chart_round(), moment("2026-10-14T15:00:00Z"))

    def test_rights_are_still_required_and_non_us_uses_the_same_domain(self):
        r = survey_round()
        self.assertTrue(any("rights" in reason for reason in season.validate_document({"rounds": [r]})))
        r["consumer"]["population"] = "Households outside the United States"
        self.assertEqual(refresh.domains.domain_of(r["series"]), "consumer")
        bad = copy.deepcopy(r)
        bad["consumer"]["source"] = "netflix"
        with self.assertRaises(ValueError):
            consumer_round.spec_for(bad)

    def test_native_cycle_freezes_scores_and_exports_only_public_evidence(self):
        r = survey_round()
        self.ingest("michigan_current", current([("June", 4), ("July", 4.1), ("August", 4.2)]),
                    "2026-09-01T00:00:00Z")
        with mock.patch.dict(inventory.INVENTORY["consumer_michigan_inflation"], {"rights": inventory.APPROVED}), \
                mock.patch.object(refresh, "FORECASTS", str(self.root / "forecasts")):
            built = run_consumer_cycle.cycle({"rounds": [r]}, moment("2026-10-19T14:00:00Z"))
            self.assertTrue(built["rounds"][0]["scoreable"])
            self.assertTrue((self.root / "locks" / (r["round_id"] + ".json")).exists())
            self.assertNotEqual(Path(refresh.lock_snapshot_path(r["round_id"])).parent, Path(refresh.LOCKS))
            manifest = stamps.build_manifest(r["round_id"], r["lock_at"], forecasts_dir=str(self.root / "forecasts"))
            self.assertIsNotNone(manifest["lock_snapshot_sha256"])
            self.ingest("michigan_current", current([("October", 5)]), "2026-10-23T14:01:00Z")
            path = self.root / "forecasts" / r["round_id"] / "test-entrant.json"
            consumer.write_json(path, {"entrant": "test-entrant", "model": "test", "round_id": r["round_id"],
                                       "topline": {"mean": 4.9, "sd": 0.5}})
            built = run_consumer_cycle.cycle({"rounds": [r]}, moment("2026-10-23T15:00:00Z"))
            self.assertEqual(built["scalar"][0]["entrant"], "test-entrant")
            self.assertGreater(built["rounds"][0]["scores"]["test-entrant"]["skill"], 0)
            public = json.dumps(built["public_results"])
            self.assertIn("raw_sha256", public)
            for field in ("raw_file", "parsed_file", "history", "raw_bytes"):
                self.assertNotIn(field, public)

    def test_native_chart_cycle_scores_new_membership_and_rank_for_both_sources(self):
        for source in ("nyt", "steam"):
            r = chart_round(source)
            if source == "nyt":
                self.ingest(source, nyt_body("2026-09-26", "2026-10-04", (1, 2, 3)), "2026-09-30T00:00:00Z")
            else:
                body, context = steam_body("2026-09-22", (1, 2, 3))
                self.ingest(source, body, "2026-09-29T00:00:00Z", context)
            with mock.patch.dict(inventory.INVENTORY[consumer_round.source_for(r)], {"rights": inventory.APPROVED}), \
                    mock.patch.object(refresh, "FORECASTS", str(self.root / "forecasts")):
                before = run_consumer_cycle.cycle({"rounds": [r]}, moment("2026-09-30T12:00:00Z"))
                self.assertTrue(before["rounds"][0]["scoreable"])
                if source == "nyt":
                    self.ingest(source, nyt_body(), "2026-10-14T14:01:00Z")
                else:
                    body, context = steam_body()
                    self.ingest(source, body, "2026-10-14T14:01:00Z", context)
                outcome = consumer_round.resolution(r, moment("2026-10-14T15:00:00Z"))["items"]
                consumer.write_json(self.root / "forecasts" / r["round_id"] / "test-entrant.json",
                                    {"entrant": "test-entrant", "round_id": r["round_id"], "model": "test",
                                     "ranking": outcome})
                result = run_consumer_cycle.cycle({"rounds": [r]}, moment("2026-10-14T15:00:00Z"))
                scores = result["rounds"][0]["scores"]["test-entrant"]
                self.assertEqual(scores["loss"], 0)
                self.assertEqual(scores["skill"], 1)
                self.assertEqual(result["rounds"][0]["status"], "resolved")
                self.assertNotIn("items", json.dumps(result["public_results"]))

    def test_missing_credentials_and_mint_never_make_network_requests(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(consumer_data.requests, "get", side_effect=AssertionError("network")):
            for source in ("nyt", "steam", "mint"):
                consumer_data.collect_source(source, self.root, 1)
        states = consumer.index(self.root)["sources"]
        self.assertEqual(states["nyt"]["last_attempt"]["status"], "missing_archive")
        self.assertEqual(states["steam"]["last_attempt"]["status"], "missing_archive")
        self.assertEqual(states["mint"]["last_attempt"]["status"], "manual_import_required")

    def test_api_keys_are_request_parameters_and_never_archive_metadata(self):
        body = nyt_body()
        response = SimpleNamespace(content=body, status_code=200, headers={})
        with mock.patch.dict(os.environ, {"NYT_API_KEY": "synthetic-test-key"}), \
                mock.patch.object(consumer_data.requests, "get", return_value=response) as request:
            consumer_data.collect_source("nyt", self.root, 1)
        self.assertEqual(request.call_args.kwargs["params"], {"api-key": "synthetic-test-key"})
        manifest = consumer.index(self.root)["sources"]["nyt"]["latest_valid"]
        _, doc = consumer.read_vintage(self.root, manifest)
        self.assertNotIn("synthetic-test-key", json.dumps(doc))
        self.assertNotIn("?", doc["url"])

    def test_archive_cannot_write_to_the_public_tree(self):
        with self.assertRaisesRegex(ValueError, "ignored"):
            consumer.ingest("nyt", nyt_body(), consumer_data.URLS["nyt"],
                            root=consumer.ROOT / "site" / "forbidden-consumer-archive")


if __name__ == "__main__":
    unittest.main()
