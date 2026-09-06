"""The three participant-facing scoring examples, against committed truth.

Run: PYTHONPATH=. python tests/test_scoring_examples.py

The page uses fictional answers so a participant can see what scores well and
what does not.  The questions and outcomes are historical, and this suite reads
the same committed resolution/source archives as the production scorers.  A
number in the prose therefore cannot drift away from the code that grades it.
Everything is offline.
"""
import html
import json
import os
import re
import sys
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import agent_api, profile_round, ranking_round, scoring      # noqa: E402
from ssa import series as registry                                    # noqa: E402
from ssa.adapters import wikipedia, yougov_xtab                       # noqa: E402


SITE = Path(ROOT, "site", "docs.html")
AGENT_DOC = Path(ROOT, "docs", "agent-api.md")

TOPLINE_QUESTION = (
    "What percentage of US adult citizens will approve of Donald Trump's job "
    "performance in the Economist/YouGov wave publishing around August 18, "
    "2026?"
)
PROFILE_QUESTION = """For the Economist/YouGov wave dated August 31, 2026, what percentage of US registered voters in each of the following 16 subgroups will approve of Donald Trump's job performance?

Party: Democrat, Independent, Republican.
Age: under 30, 30–44, 45–64, 65+.
Race: White, Black, Hispanic.
Gender: Male, Female.
Education: high school or less, some college, college graduate, postgraduate.

Forecast every named subgroup. The unit is percent approving."""
RANKING_QUESTION = """What will be the ordered top 10 English Wikipedia articles by pageviews for the week Monday August 10 through Sunday August 16, 2026?

Sum Wikimedia's seven daily top-1000 lists per article. Exclude Main_Page and all non-article namespaces: Special:, Wikipedia:, Portal:, Help:, File:, Template:, Category:, Draft:, User:, Talk:, and their talk variants. Return ten unique canonical Wikipedia titles in underscore form, rank 1 first."""


def close(got, want, tol=1e-12):
    assert abs(got - want) <= tol, (got, want)


def section(summary):
    body = SITE.read_text()
    match = re.search(
        rf'<details class="worked"><summary>{re.escape(summary)}.*?</details>',
        body,
        re.DOTALL,
    )
    assert match, summary
    return match.group(0)


def json_block(body, heading):
    match = re.search(
        rf"<h3>{re.escape(heading)}</h3>\s*<pre><code>(.*?)</code></pre>",
        body,
        re.DOTALL,
    )
    assert match, heading
    return json.loads(html.unescape(match.group(1)))


def question_block(body):
    match = re.search(r"<h3>Question</h3>\s*<pre><code>(.*?)</code></pre>",
                      body, re.DOTALL)
    assert match
    return html.unescape(match.group(1))


def request_block(shape):
    match = re.search(
        rf"<!-- worked-request:{re.escape(shape)} -->.*?```json\n(.*?)\n```",
        AGENT_DOC.read_text(),
        re.DOTALL,
    )
    assert match, shape
    return json.loads(match.group(1))


def test_topline_example_uses_the_real_resolution_and_scalar_scorer():
    body = section("Topline worked example · Economist/YouGov approval")
    answer1 = json_block(body, "Answer 1")
    answer2 = json_block(body, "Answer 2")
    shown_truth = json_block(body, "Ground truth")

    resolutions = json.loads(Path(ROOT, "resolutions", "resolved.json").read_text())
    truth = resolutions["yougov-2026-w34-approval"]
    assert shown_truth["observed_date"] == truth["observed_date"]
    assert shown_truth["value"] == truth["value"] == 35.0
    assert shown_truth["source"] == "resolutions/resolved.json"

    persistence = json.loads(Path(
        ROOT, "forecasts", "yougov-2026-w34-approval", "persistence.json"
    ).read_text())["topline"]
    p_loss = scoring.crps_forecast(persistence, truth["value"])
    a_loss = scoring.crps_forecast(answer1, truth["value"])
    b_loss = scoring.crps_forecast(answer2, truth["value"])

    close(p_loss, 1.2809009698028746)
    close(a_loss, 0.3314035312548559)
    close(b_loss, 3.8796373816210004)
    close(scoring.skill(a_loss, p_loss), 0.7412731045821148)
    close(scoring.skill(b_loss, p_loss), -2.0288347601283028)
    assert "0.331403531255" in body and "3.879637381621" in body
    print("ok test_topline_example_uses_the_real_resolution_and_scalar_scorer")


def _workbook_profile(path, day):
    waves = yougov_xtab.parse(path.read_bytes())
    wave = next(w for w in waves if w["date"] == day)
    cells = list(registry.YOUGOV_XTAB_CELLS)
    labels = [registry.SERIES[c]["yougov_xtab"]["cell"] for c in cells]
    return cells, {c: wave["cells"][label]["approve"]
                   for c, label in zip(cells, labels)}


def test_profile_example_scores_all_sixteen_real_cells_together():
    body = section("Population profile worked example · 16 YouGov subgroups")
    answer1 = json_block(body, "Answer 1")
    answer2 = json_block(body, "Answer 2")
    shown_truth = json_block(body, "Ground truth")["profile"]

    cells, previous = _workbook_profile(
        Path(ROOT, "sources", "yougov_xtab", "2026-08-27.xlsx"),
        "2026-08-24",
    )
    truth_cells, truth = _workbook_profile(
        Path(ROOT, "sources", "yougov_xtab", "2026-09-05.xlsx"),
        "2026-08-31",
    )
    assert truth_cells == cells
    assert shown_truth == truth
    assert json_block(body, "Ground truth")["source"] == \
        "sources/yougov_xtab/2026-09-05.xlsx"
    assert list(answer1["profile"]) == cells
    assert list(answer2["profile"]) == cells

    outcome = [truth[c] for c in cells]
    persistence = {"profile": {
        c: {"mean": previous[c], "sd": 1.5} for c in cells
    }}
    p_energy = profile_round.score_submission(
        persistence, outcome, cells
    )["energy"]
    a_energy = profile_round.score_submission(answer1, outcome, cells)["energy"]
    b_energy = profile_round.score_submission(answer2, outcome, cells)["energy"]

    close(p_energy, 8.595569671153477)
    close(a_energy, 5.582756883815237)
    close(b_energy, 59.493892047957836)
    close(scoring.profile_skill(a_energy, p_energy), 0.3505076338859967)
    close(scoring.profile_skill(b_energy, p_energy), -5.921460045588123)
    assert "5.582756883815" in body and "59.493892047958" in body
    print("ok test_profile_example_scores_all_sixteen_real_cells_together")


def test_ranking_example_uses_the_real_archive_and_rbo_scorer():
    body = section("Ranking worked example · English Wikipedia top 10")
    answer1 = json_block(body, "Answer 1")
    answer2 = json_block(body, "Answer 2")
    shown = json_block(body, "Ground truth")
    shown_truth = shown["ranking"]

    truth, _ = wikipedia.weekly_top("2026-08-16", 10, fetch=False)
    persistence, _ = wikipedia.weekly_top("2026-08-02", 10, fetch=False)
    assert shown_truth == truth
    assert answer2["ranking"] == persistence
    assert shown["source_files"] == [
        f"wikitop/en.wikipedia.all-access/2026-08-{day:02d}.json"
        for day in range(10, 17)
    ]
    assert all(Path(ROOT, path).is_file() for path in shown["source_files"])

    spec = {
        "kind": "wiki_top10",
        "length": 10,
        "loss": "rbo",
        "rbo_p": 0.9,
        "exclusions": wikipedia.EXCLUSION_RULE_ID,
    }
    p_loss = ranking_round.score_submission(
        answer2, truth, spec
    )["loss"]
    a_loss = ranking_round.score_submission(
        answer1, truth, spec
    )["loss"]

    close(p_loss, 0.7917959312259693)
    close(a_loss, 0.12507579633005872)
    close(ranking_round.ranking_skill(a_loss, p_loss), 0.8420353131438818)
    assert "RBO similarity = Σ(weight(d) × agreement(d)) / Σ weight(d)" in body
    assert "0.569856997129 / 0.651321559900" in body
    assert "0.125075796330" in body and "0.791795931226" in body
    print("ok test_ranking_example_uses_the_real_archive_and_rbo_scorer")


def test_examples_separate_questions_from_the_http_payload():
    body = SITE.read_text()
    assert '<h2 id="example-questions">Example Questions</h2>' in body
    assert body.count('href="#example-questions">Example Questions</a>') == 2
    assert "Question</b> is the complete human-readable task" in body
    assert "The Question and Answer blocks above are readable extracts" in body
    assert "The Arena does not add a hidden natural-language prompt" in body
    assert body.index("Topline worked example") < body.index(
        '<h2 id="request-payload">Request payload</h2>'
    )
    print("ok test_examples_separate_questions_from_the_http_payload")


def test_complete_request_payloads_are_built_by_the_production_contract():
    top_history = [
        {"date": day, "value": value}
        for day, value in [
            ("2026-06-18", 38.0), ("2026-06-20", 34.0),
            ("2026-06-27", 38.0), ("2026-07-04", 35.0),
            ("2026-07-11", 37.0), ("2026-07-18", 36.0),
            ("2026-07-23", 39.0), ("2026-07-26", 34.0),
            ("2026-08-01", 36.0), ("2026-08-08", 33.0),
        ]
    ]
    top_round = {
        "round_id": "yougov-2026-w34-approval",
        "target_type": "continuous_normal",
        "question": TOPLINE_QUESTION,
        "unit": "% approve",
        "lock_at": "2026-08-16T14:00:00Z",
        "baselines": {"persistence": {"mean": 33.0}},
    }
    expected_top = agent_api.build_envelope(
        "example-agent", top_round, history=top_history
    )

    cells, previous = _workbook_profile(
        Path(ROOT, "sources", "yougov_xtab", "2026-08-27.xlsx"),
        "2026-08-24",
    )
    profile_round_def = {
        "round_id": "example-yougov-xtab-2026-08-31",
        "target_type": "profile_energy",
        "question": PROFILE_QUESTION,
        "unit": "percent approving, per subgroup",
        "lock_at": "2026-08-28T14:00:00Z",
        "cells": cells,
    }
    profile_history = {
        cell: [{"date": "2026-08-24", "value": previous[cell]}]
        for cell in cells
    }
    expected_profile = agent_api.build_envelope(
        "example-agent", profile_round_def, profile_history=profile_history
    )

    persistence, _ = wikipedia.weekly_top("2026-08-02", 10, fetch=False)
    ranking_round_def = {
        "round_id": "example-wiki-top10-2026-08-16",
        "target_type": "ranking_list",
        "question": RANKING_QUESTION,
        "unit": "ordered list of 10 en.wikipedia article titles",
        "lock_at": "2026-08-07T14:00:00Z",
        "ranking": {
            "kind": "wiki_top10",
            "length": 10,
            "loss": "rbo",
            "rbo_p": 0.9,
            "week_start": "2026-08-10",
            "week_end": "2026-08-16",
            "project": "en.wikipedia",
            "access": "all-access",
            "exclusions": wikipedia.EXCLUSION_RULE_ID,
        },
    }
    expected_ranking = agent_api.build_envelope(
        "example-agent",
        ranking_round_def,
        ranking_history=[{"week_end": "2026-08-02",
                          "ranking": persistence}],
    )

    assert request_block("topline") == expected_top
    assert request_block("profile") == expected_profile
    assert request_block("ranking") == expected_ranking
    assert question_block(section(
        "Topline worked example · Economist/YouGov approval"
    )) == expected_top["round"]["question"]
    assert question_block(section(
        "Population profile worked example · 16 YouGov subgroups"
    )) == expected_profile["round"]["question"]
    assert question_block(section(
        "Ranking worked example · English Wikipedia top 10"
    )) == expected_ranking["round"]["question"]
    print("ok test_complete_request_payloads_are_built_by_the_production_contract")


if __name__ == "__main__":
    test_topline_example_uses_the_real_resolution_and_scalar_scorer()
    test_profile_example_scores_all_sixteen_real_cells_together()
    test_ranking_example_uses_the_real_archive_and_rbo_scorer()
    test_examples_separate_questions_from_the_http_payload()
    test_complete_request_payloads_are_built_by_the_production_contract()
    print("all scoring example tests pass")
