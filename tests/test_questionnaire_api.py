"""End-to-end contract tests for the questionnaire manifest and intake core."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from ssa.questionnaire_api import (
    IdempotencyConflict,
    SubmissionError,
    build_manifest,
    store_submission,
    validate_submission,
)


NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def round_data(round_id, target_type, *, board_metadata=None,
               lock_at="2026-09-01T00:00:00Z", status="open"):
    body = {
        "round_id": round_id,
        "question": f"Complete question for {round_id}?",
        "unit": "points",
        "release_at": "2026-09-03T00:00:00Z",
        "lock_at": lock_at,
        "resolve": f"Declared resolution rule for {round_id}",
        "target_type": target_type,
        "status": status,
        "baselines": {"persistence": {"mean": 42, "sd": 2}},
    }
    body.update(board_metadata or {})
    return body


def sample_data():
    return {"rounds": [
        round_data("continuous-round", "continuous_normal"),
        round_data("binary-round", "binary_probability"),
        round_data("choice-round", "multiple_choice",
                   board_metadata={"options": ["A", "B"]}),
        round_data("short-round", "short_answer"),
        round_data("ranking-round", "ranking_list",
                   board_metadata={"ranking": {
                       "length": 3, "kind": "basket", "loss": "kendall",
                       "items": ["A", "B", "C"],
                       "week_start": "2026-08-17",
                       "week_end": "2026-08-23",
                   }}),
        round_data("profile-round", "profile_energy",
                   board_metadata={
                       "cells": ["cell_one", "cell_two"],
                       "profile": {"cells": ["cell_one", "cell_two"],
                                   "labels": {"cell_one": "One",
                                              "cell_two": "Two"}},
                   }),
        round_data("locked-round", "continuous_normal",
                   lock_at="2026-08-19T00:00:00Z", status="locked"),
    ]}


def agent_answer(round_id, target_type):
    responses = {
        "continuous_normal": {"mean": 40.5, "sd": 2.1},
        "binary_probability": {"probability": 0.7},
        "multiple_choice": {"choice": "A"},
        "short_answer": {"text": "Bounded answer"},
        "ranking_list": {"ranking": ["A", "B", "C"]},
        "profile_energy": {"profile": {
            "cell_one": {"mean": 1, "sd": 2},
            "cell_two": {"mean": 3, "sd": 2},
        }},
    }
    return {"round_id": round_id, "target_type": target_type,
            "response": responses[target_type]}


def human_answer(round_id, target_type):
    responses = {
        "continuous_normal": {"value": 40.5},
        "binary_probability": {"choice": "Yes"},
        "multiple_choice": {"choice": "A"},
        "short_answer": {"text": "Bounded answer"},
        "ranking_list": {"ranking": ["A", "B", "C"]},
        "profile_energy": {"profile": {"cell_one": 1, "cell_two": 3}},
    }
    return {"round_id": round_id, "target_type": target_type,
            "response": responses[target_type]}


def agent_submission(data):
    open_items = data["rounds"][:6]
    return {"track": "agent", "submission": {
        "participant_type": "startup",
        "organization_name": "Acme Labs",
        "product_name": "Acme Agent",
        "contact": {"name": "Ada Researcher", "email": "ada@example.com"},
        "publication_consent": {
            "accepted": True,
            "fields": ["organization_name", "product_name"],
            "terms_version": "ssa-publication-v1",
        },
        "delivery": {
            "method": "questionnaire_commitment",
            "answers": [agent_answer(item["round_id"], item["target_type"])
                        for item in open_items],
            "commitment": {"accepted": True,
                           "terms_version": "ssa-participant-v1"},
        },
    }}


def human_submission(data, board="topline"):
    board_types = {
        "topline": {"continuous_normal", "binary_probability",
                    "multiple_choice", "short_answer"},
        "profile": {"profile_energy"},
        "ranking": {"ranking_list"},
    }
    items = [item for item in data["rounds"][:6]
             if item["target_type"] in board_types[board]]
    manifest = [item["round_id"] for item in items]
    return {"track": "human", "submission": {
        "submission_version": 1,
        "board_id": board,
        "round_manifest": manifest,
        "username": "forecast-fan",
        "contact_email": "human@example.com",
        "answers": [human_answer(item["round_id"], item["target_type"])
                    for item in items],
        "commitment": {"accepted": True,
                       "terms_version": "ssa-participant-v1"},
        "publication_consent": {
            "accepted": True,
            "field": "username",
            "terms_version": "ssa-publication-v1",
        },
    }}


class QuestionnaireApiContracts(unittest.TestCase):
    def setUp(self):
        self.data = sample_data()

    def test_manifest_contains_complete_questions_and_both_answer_formats(self):
        manifest = build_manifest(self.data, NOW)
        self.assertEqual("ssa-questionnaire-manifest-v1",
                         manifest["schema_version"])
        self.assertEqual(6, len(manifest["questions"]))
        first = next(item for item in manifest["questions"]
                     if item["round_id"] == "continuous-round")
        self.assertEqual("Complete question for continuous-round?",
                         first["question"])
        self.assertEqual("Declared resolution rule for continuous-round",
                         first["resolution_rule"])
        self.assertIsNone(first["resolution_source_url"])
        self.assertEqual(42, first["latest_public_reference"]["mean"])
        self.assertEqual("continuous_normal",
                         first["answer_schema"]["agent"]["properties"]
                         ["target_type"]["const"])
        self.assertIn("value", first["answer_schema"]["human"]
                      ["properties"]["response"]["properties"])
        ranking = next(item for item in manifest["questions"]
                       if item["round_id"] == "ranking-round")
        self.assertEqual(["A", "B", "C"], ranking["ranking"]["items"])

    def test_vercel_bundles_python_functions_and_runtime_data(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual("3.12", (root / ".python-version").read_text().strip())
        config = json.loads((root / "vercel.json").read_text())
        python_build = next(build for build in config["builds"]
                            if build["use"] == "@vercel/python")
        self.assertEqual("api/*.py", python_build["src"])
        included = python_build["config"]["includeFiles"]
        self.assertIn("site/data.json", included)
        self.assertIn("schema/*.schema.json", included)
        self.assertTrue(any(build["use"] == "@vercel/static"
                            and build["src"] == "site/**/*"
                            for build in config["builds"]))
        workflow = (root / ".github/workflows/preview.yml").read_text()
        self.assertIn("vercel deploy --force --yes", workflow)

    def test_valid_agent_and_each_human_board_are_accepted(self):
        validate_submission(agent_submission(self.data), self.data, NOW)
        for board in ("topline", "profile", "ranking"):
            validate_submission(human_submission(self.data, board),
                                self.data, NOW)

    def test_agent_api_registration_cannot_use_questionnaire_endpoint(self):
        envelope = agent_submission(self.data)
        envelope["submission"]["delivery"] = {
            "method": "openai_compatible_api",
            "endpoint": "https://agent.example/v1",
            "credential_supplied": False,
            "contract_version": "ssa-agent-api-v1",
            "probe_status": "passed",
        }
        with self.assertRaisesRegex(SubmissionError,
                                    "questionnaire_commitment"):
            validate_submission(envelope, self.data, NOW)

    def test_human_commitment_is_required(self):
        envelope = human_submission(self.data)
        del envelope["submission"]["commitment"]
        with self.assertRaises(SubmissionError) as context:
            validate_submission(envelope, self.data, NOW)
        self.assertTrue(any(detail["path"] == "$" for detail in
                            context.exception.details))

    def test_manifest_and_answers_must_match_current_open_rounds(self):
        envelope = human_submission(self.data)
        envelope["submission"]["round_manifest"].append("locked-round")
        with self.assertRaisesRegex(SubmissionError, "round_manifest"):
            validate_submission(envelope, self.data, NOW)

        envelope = agent_submission(self.data)
        envelope["submission"]["delivery"]["answers"].pop()
        with self.assertRaisesRegex(SubmissionError, "manifest exactly"):
            validate_submission(envelope, self.data, NOW)

    def test_target_type_is_checked_against_the_live_round(self):
        envelope = agent_submission(self.data)
        answer = envelope["submission"]["delivery"]["answers"][0]
        answer["target_type"] = "binary_probability"
        answer["response"] = {"probability": 0.5}
        with self.assertRaisesRegex(SubmissionError, "must be continuous_normal"):
            validate_submission(envelope, self.data, NOW)

    def test_declared_options_ranking_items_and_profile_cells_are_enforced(self):
        cases = [
            (2, {"choice": "Not declared"}, "declared option"),
            (4, {"ranking": ["A", "B"]}, "exactly 3"),
            (5, {"profile": {"cell_one": {"mean": 1, "sd": 2},
                              "wrong_cell": {"mean": 3, "sd": 2}}},
             "every declared profile cell"),
        ]
        for answer_index, response, message in cases:
            with self.subTest(message=message):
                envelope = agent_submission(self.data)
                envelope["submission"]["delivery"]["answers"][answer_index][
                    "response"] = response
                with self.assertRaisesRegex(SubmissionError, message):
                    validate_submission(envelope, self.data, NOW)

    def test_local_private_store_is_idempotent_and_detects_key_reuse(self):
        validated = validate_submission(agent_submission(self.data),
                                        self.data, NOW)
        with tempfile.TemporaryDirectory() as directory, patch.dict(
                os.environ, {"SUBMISSION_STORAGE_DIR": directory}, clear=False):
            first = store_submission(validated, "test-key-123", NOW)
            replay = store_submission(validated, "test-key-123", NOW)
            self.assertFalse(first["idempotent_replay"])
            self.assertTrue(replay["idempotent_replay"])
            self.assertEqual(first["submission_id"], replay["submission_id"])
            files = list(Path(directory).rglob("*.json"))
            self.assertEqual(1, len(files))
            stored_text = files[0].read_text()
            stored = json.loads(stored_text)
            self.assertIn("ada@example.com", stored_text)
            self.assertNotIn("test-key-123", stored_text)
            self.assertEqual("pending_review", stored["status"])

            changed = json.loads(json.dumps(validated))
            changed["submission"]["product_name"] = "Different Agent"
            with self.assertRaises(IdempotencyConflict):
                store_submission(changed, "test-key-123", NOW)


if __name__ == "__main__":
    unittest.main()
