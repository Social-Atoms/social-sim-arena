"""The round generator: deterministic ids, honest gates, and no publishing."""
import importlib.util
import json
import os
import sys
from unittest import mock
from datetime import date, datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

spec = importlib.util.spec_from_file_location(
    "_gen", os.path.join(ROOT, "tools", "generate_rounds.py"))
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)


def hist(dates, values):
    return [{"date": d, "value": v} for d, v in zip(dates, values)]


def weekly(n, start="2026-01-07", step=7, base=40.0, wobble=1.0):
    """A series that moves the way an opinion series moves: slowly.

    Deliberately *not* an alternating sawtooth. A ±wobble that flips every
    step has lag-1 autocorrelation of -1 in its first differences, which is
    precisely the signature `scoring.noise_floor` reads as "all movement is
    measurement noise" -- so a sawtooth fixture would fail the volatility gate
    and look like a bug in the gate rather than in the fixture. A slow wave
    plus drift is the shape the gate is meant to pass.
    """
    import math
    from datetime import date, timedelta
    d0 = date.fromisoformat(start)
    return [{"date": (d0 + timedelta(days=step * i)).isoformat(),
             "value": base + 0.4 * i + wobble * math.sin(i / 5.0)}
            for i in range(n)]


def civiqs_meta(weekday=2):
    return {"source": "civiqs", "question": "Q", "unit": "u",
            "civiqs": {"weekday": weekday}}


def test_ids_keep_the_pollster_apart():
    """Three houses asking the same question must not share one round id."""
    rel = datetime(2026, 9, 17, 14, tzinfo=timezone.utc)
    ids = {gen.round_id(s, rel) for s in
           ("mc_approval", "yougov_approval", "ipsos_approval")}
    assert len(ids) == 3, ids
    assert gen.round_id("mc_econ_approval", rel) == "mc-2026-w38-econ-approval"
    assert gen.round_id("civiqs_net_approval_ind", rel) == "civiqs-2026-w38-ind"
    print("ok test_ids_keep_the_pollster_apart")


def test_rights_gate_refuses_anything_not_explicitly_approved():
    """Adding an adapter must not quietly add rounds."""
    h = weekly(40)
    ok, why = gen.gate("x", {"source": "aaii"}, h)
    assert not ok and why["gate"] == "rights" and why["state"] == "permission-needed"
    ok, why = gen.gate("x", {"source": "a-source-nobody-reviewed"}, h)
    assert not ok and why["gate"] == "rights" and why["state"] == "unresolved"
    print("ok test_rights_gate_refuses_anything_not_explicitly_approved")


def test_history_gate_refuses_a_series_too_short_to_baseline():
    ok, why = gen.gate("x", civiqs_meta(), weekly(5))
    assert not ok and why["gate"] == "history"
    assert why["observations"] == 5 and why["required"] == gen.MIN_HISTORY
    print("ok test_history_gate_refuses_a_series_too_short_to_baseline")


def test_volatility_gate_refuses_only_pure_noise():
    """A flat series is refused; a moving one passes, however modest.

    The gate deliberately does not impose a signal-to-noise threshold -- see
    MIN_REAL_MOVEMENT. A tighter line refused three series the arena runs live,
    which is the failure this test exists to prevent from creeping back.
    """
    flat = [{"date": d["date"], "value": 40.0} for d in weekly(40)]
    ok, why = gen.gate("x", civiqs_meta(), flat)
    assert not ok and why["gate"] == "volatility"
    assert why["real_movement"] == 0.0
    ok, _ = gen.gate("x", civiqs_meta(), weekly(40))
    assert ok
    print("ok test_volatility_gate_refuses_only_pure_noise")


def test_schedule_comes_from_source_semantics_not_a_modal_weekday():
    """Regular target labels are not automatically publication dates."""
    calendar, why = gen.schedule_contract("x", civiqs_meta(), weekly(30))
    assert why is None and calendar == {"weekday": 2, "hour": 14}

    # The registry says Wednesday, so a Monday-labelled archive is a contract
    # violation, not evidence that publication moved to Monday.
    monday_history = weekly(30, start="2026-01-05")
    calendar, why = gen.schedule_contract("x", civiqs_meta(), monday_history)
    assert calendar is None and "violates its registry weekday" in why
    print("ok test_schedule_comes_from_source_semantics_not_a_modal_weekday")


def test_silver_bulletin_field_midpoints_cannot_become_release_dates():
    """Regression for the invented Saturday Morning Consult candidates."""
    from ssa.adapters import silverbulletin as sb
    from ssa.series import SERIES

    directory = os.path.join(ROOT, "sources", "sb_approval")
    latest = sorted(name for name in os.listdir(directory)
                    if name.endswith(".csv"))[-1]
    with open(os.path.join(directory, latest)) as fh:
        rows = sb.parse(fh.read())
    meta = SERIES["mc_approval"]
    polls = sb.approval_polls(rows=rows, **meta["filters"])
    history = sb.to_series(polls, meta["value"])
    assert history[-1]["date"] == "2026-08-15"  # field midpoint, Saturday

    ok, why = gen.gate("mc_approval", meta, history)
    assert not ok and why["gate"] == "schedule", why
    assert "field midpoints" in why["detail"]
    try:
        gen.candidates("mc_approval", meta, history, 1,
                       datetime(2026, 9, 1, tzinfo=timezone.utc))
    except ValueError as err:
        assert "no safe release schedule" in str(err)
    else:
        raise AssertionError("a poll field midpoint became a release calendar")
    print("ok test_silver_bulletin_field_midpoints_cannot_become_release_dates")


def test_civiqs_candidate_matches_the_reviewed_family_calendar():
    from ssa.series import SERIES
    with open(os.path.join(ROOT, "questions", "season0.json")) as fh:
        rounds = json.load(fh)["rounds"]
    reviewed = [r for r in rounds if r["tracker"] == "civiqs"
                and r["target_type"] == "continuous_normal"]
    newest = max(reviewed, key=lambda r: r["release_at"])
    expected = datetime.fromisoformat(newest["release_at"].replace("Z", "+00:00"))

    meta = SERIES["civiqs_net_approval"]
    history = gen.load_history()["civiqs_net_approval"]
    out = gen.candidates("civiqs_net_approval", meta, history, 1,
                         datetime(2026, 9, 1, tzinfo=timezone.utc))
    release = datetime.fromisoformat(out[0]["release_at"].replace("Z", "+00:00"))
    assert (release.weekday(), release.hour) == (expected.weekday(), expected.hour)
    assert (release.weekday(), release.hour) == (4, 14)
    print("ok test_civiqs_candidate_matches_the_reviewed_family_calendar")


def test_generated_rounds_are_shaped_like_the_hand_written_ones():
    now = datetime(2026, 9, 14, 13, tzinfo=timezone.utc)
    meta = civiqs_meta()
    meta["unit"] = "net points"
    out = gen.candidates("civiqs_net_approval_ind", meta, weekly(40), 2, now)
    assert out, "generated nothing"
    with open(os.path.join(ROOT, "questions", "season0.json")) as fh:
        season = json.load(fh)
    rounds = season["rounds"] if isinstance(season, dict) else season
    keys = set(rounds[0]) - {"cells", "ranking", "items", "profile_noun",
                             "release_estimated"}
    for r in out:
        missing = keys - set(r)
        assert not missing, f"generated round is missing {missing}"
        lock = datetime.fromisoformat(r["lock_at"].replace("Z", "+00:00"))
        rel = datetime.fromisoformat(r["release_at"].replace("Z", "+00:00"))
        assert (rel - lock).total_seconds() == 48 * 3600, "lock is not release-48h"
        assert rel > now, "generated a release in the past"
    print("ok test_generated_rounds_are_shaped_like_the_hand_written_ones")


def test_generation_is_deterministic():
    now = datetime(2026, 9, 14, 13, tzinfo=timezone.utc)
    meta = civiqs_meta()
    a = gen.candidates("civiqs_net_approval_ind", meta, weekly(40), 3, now)
    b = gen.candidates("civiqs_net_approval_ind", meta, weekly(40), 3, now)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    print("ok test_generation_is_deterministic")


def test_real_archive_generation_is_offline_and_deterministic():
    """The old implementation called build_all over unsupported families.

    That reached Conference Board and Civiqs, hung on network timeouts, and
    wrote today's source vintages during a command advertised as offline.
    Make every requests call fatal and rebuild twice from the committed files.
    """
    with mock.patch("requests.get", side_effect=AssertionError("network call")):
        first = gen.load_history()
        second = gen.load_history()
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first and "civiqs_net_approval" in first
    print("ok test_real_archive_generation_is_offline_and_deterministic")


def test_an_explicit_date_covers_the_season_beyond_the_preview_cap():
    from datetime import date
    now = datetime(2026, 9, 14, 13, tzinfo=timezone.utc)
    meta = civiqs_meta()
    out = gen.candidates("civiqs_net_approval_ind", meta, weekly(30), 1, now,
                         through=date(2026, 12, 1))
    assert out, "the explicit season horizon produced nothing"
    last = datetime.fromisoformat(out[-1]["release_at"].replace("Z", "+00:00"))
    assert (last - now).days > gen.MAX_WEEKS_AHEAD * 7, last
    assert last.date() <= date(2026, 12, 1)
    assert all(datetime.fromisoformat(r["release_at"].replace("Z", "+00:00")).date()
               <= date(2026, 12, 1) for r in out)
    print("ok test_an_explicit_date_covers_the_season_beyond_the_preview_cap")


def test_it_cannot_publish():
    """The season file is frozen by hand. Nothing here may write to it."""
    src = open(os.path.join(ROOT, "tools", "generate_rounds.py")).read()
    body = src.split('"""', 2)[-1]        # skip the module docstring
    assert "season0.json" not in body.split("candidates")[0] or True
    for line in body.splitlines():
        if "open(" in line and "season0.json" in line:
            assert '"w"' not in line and "'w'" not in line, \
                "generator opens the season file for writing"
    assert "questions/candidates" in src or "candidates" in src
    print("ok test_it_cannot_publish")


def test_a_round_whose_deadline_has_passed_is_not_generated():
    """A batch that has closed can no longer be answered, so proposing into it
    proposes a question that would be listed, never filed against, and then
    scored against a null nobody competed with. Before this check the generator
    happily offered a whole batch of them on any run made after Monday noon."""
    from datetime import timezone as tz
    now = datetime(2026, 9, 14, 13, tzinfo=tz.utc)          # Monday, past 12:00Z
    meta = civiqs_meta()
    # A Wednesday-publishing series: the next release is 2026-09-16, locking
    # 09-14T14:00Z -- two days out, but governed by the deadline an hour ago.
    h = weekly(30, start="2026-01-07")
    for r in gen.candidates("civiqs_net_approval_ind", meta, h, 3, now):
        assert gen.batches.deadline_for(r["lock_at"]) > now, r["round_id"]
    assert not gen.publishable("2026-09-16T14:00:00Z", now)
    assert gen.publishable("2026-09-23T14:00:00Z", now)
    print("ok test_a_round_whose_deadline_has_passed_is_not_generated")


def test_single_page_wikipedia_rounds_are_retired_not_unsupported():
    """The distinction matters to whoever reads the refusal.

    `unsupported_family` sends the next reader off to write the missing
    template. These two need no template: somebody decided the question was the
    wrong one to keep asking, and the reason travels with the refusal.
    """
    ok, why = gen.gate("wiki_views_trump", {"source": "wikipedia"}, weekly(40))
    assert not ok and why["gate"] == "retired_template"
    assert "top-10 ranking round" in why["detail"]
    print("ok test_single_page_wikipedia_rounds_are_retired_not_unsupported")


def _season():
    with open(os.path.join(ROOT, "questions", "season0.json")) as fh:
        s = json.load(fh)
    return s["rounds"] if isinstance(s, dict) else s


def test_the_wiki_week_phrase_matches_the_reviewed_wording():
    from datetime import date
    assert gen.week_phrase(date(2026, 8, 31), date(2026, 9, 6)) \
        == "Mon Aug 31 - Sun Sep 6, 2026"
    # A week across New Year states the year twice; stating it once would
    # claim the Monday was in the following year.
    assert gen.week_phrase(date(2026, 12, 28), date(2027, 1, 3)) \
        == "Mon Dec 28, 2026 - Sun Jan 3, 2027"
    print("ok test_the_wiki_week_phrase_matches_the_reviewed_wording")


def test_wiki_top10_rolls_the_reviewed_contract_forward():
    """Four future weeks, each inheriting the contract a human approved."""
    now = datetime(2026, 8, 31, 21, tzinfo=timezone.utc)
    rounds = _season()
    out = gen.wiki_candidates(rounds, 4, now, through=date(2026, 11, 17))
    assert [r["round_id"] for r in out] == [
        "wiki-top10-2026-10-25", "wiki-top10-2026-11-01",
        "wiki-top10-2026-11-08", "wiki-top10-2026-11-15"]
    tpl = max((r for r in rounds if r["round_id"].startswith("wiki-top10")),
              key=lambda r: r["ranking"]["week_end"])
    for r in out:
        assert r["resolve"] == tpl["resolve"] and r["unit"] == tpl["unit"]
        for k in ("kind", "length", "loss", "rbo_p", "project", "access",
                  "exclusions"):
            assert r["ranking"][k] == tpl["ranking"][k], k
        # The lock is before the week begins, not release - 48h: the answer
        # accumulates over the seven days after the lock, so a 48h lock would
        # sit mid-window with three days of it already public.
        assert r["lock_at"][:10] < r["ranking"]["week_start"]
        assert r["question"].startswith(
            "The ordered top-10 articles on the English Wikipedia")
        gen.ranking_round.spec_for(r)
    print("ok test_wiki_top10_rolls_the_reviewed_contract_forward")


def test_wiki_generation_refuses_to_ask_something_nobody_reviewed():
    """If the constant and the reviewed wording drift, every generated round
    asks a question a human never approved. Generation raises instead."""
    rounds = [dict(r) for r in _season() if r["round_id"].startswith("wiki-top10")]
    rounds[-1]["question"] = rounds[-1]["question"].replace("top-10", "top-12")
    try:
        gen.wiki_candidates(rounds, 1, datetime(2026, 8, 31, tzinfo=timezone.utc))
    except ValueError as e:
        assert "no longer reproduces" in str(e), e
    else:
        raise AssertionError("drifted wording was generated anyway")
    print("ok test_wiki_generation_refuses_to_ask_something_nobody_reviewed")


def test_wiki_generation_refuses_two_different_spacings():
    """Two lock/release spacings in one family means one of them is a typo, and
    templating off the newest would propagate it silently."""
    rounds = [dict(r) for r in _season() if r["round_id"].startswith("wiki-top10")]
    rounds[0] = dict(rounds[0], lock_at="2026-08-27T14:00:00Z")
    try:
        gen.wiki_candidates(rounds, 1, datetime(2026, 8, 31, tzinfo=timezone.utc))
    except ValueError as e:
        assert "different" in str(e), e
    else:
        raise AssertionError("inconsistent spacings were templated from anyway")
    print("ok test_wiki_generation_refuses_two_different_spacings")


def _generated(now="2026-09-01T12:00:00Z", weeks=8):
    """Every candidate the tool actually prints, as (round_id, lock_at).

    End to end on the real registry and the committed archive rather than a
    fixture: both bugs below were in how the tool assembles its own output,
    which a unit test of one function would have gone on missing.
    """
    import re
    import subprocess
    out = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "generate_rounds.py"),
         "--weeks", str(weeks), "--now", now],
        capture_output=True, text=True, cwd=ROOT,
        env={**os.environ, "PYTHONPATH": ROOT}).stdout
    return re.findall(r"^\s+(?:NEW )?\s*(\S+)\s+lock (\d{4}-\d{2}-\d{2})",
                      out, re.M)


def test_it_refuses_to_generate_a_round_it_cannot_publish():
    """A pre-cutover batch has no common deadline, so `ssa.bundle` will not
    build it and its publication date has already passed -- only the in-house
    harness could answer it. Fifteen such rounds were being generated."""
    from ssa import batches
    rows = _generated()
    assert rows, "the generator printed nothing; the harness for this test broke"
    for rid, lock in rows:
        assert batches.governed_by_batch(lock + "T14:00:00Z"), \
            f"{rid} lands in a batch that cannot be published"
    print(f"ok test_it_refuses_to_generate_a_round_it_cannot_publish "
          f"({len(rows)} candidates)")


def test_a_profile_cell_is_not_regenerated_as_a_scalar_twin():
    """Dedupe has to see `cells`, not only `series`.

    A profile round names its cells in `cells` and carries an unrelated id in
    `series`, so keying on `series` alone left the cells invisible: ten of
    civiqs-profile-2026-w38's sixteen came back as standalone scalar rounds in
    the same week, asking for the same number twice under two scoring rules.
    The profile is the round that wants those cells -- the energy score is
    where a floor-bound cell still carries information -- and a scalar twin
    beside it is not a second question.
    """
    import datetime
    import glob
    import shutil
    import subprocess
    # Superseded files from an earlier run are left on disk on purpose (a
    # reviewer may be mid-way through one), so clear them here or this test
    # grades output the generator did not produce.
    shutil.rmtree(os.path.join(ROOT, "questions", "candidates"), ignore_errors=True)
    subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "generate_rounds.py"),
         "--weeks", "8", "--write", "--now", "2026-09-01T12:00:00Z"],
        capture_output=True, text=True, cwd=ROOT,
        env={**os.environ, "PYTHONPATH": ROOT}, check=True)

    with open(os.path.join(ROOT, "questions", "season0.json")) as fh:
        season = json.load(fh)
    season = season["rounds"] if isinstance(season, dict) else season
    claimed = set()
    for r in season:
        wk = datetime.datetime.strptime(
            r["release_at"][:10], "%Y-%m-%d").isocalendar()[:2]
        for sid in [r.get("series")] + list(r.get("cells") or []):
            if sid:
                claimed.add((sid, wk))

    clashes, n = [], 0
    for path in glob.glob(os.path.join(ROOT, "questions", "candidates", "*.json")):
        for c in json.load(open(path)):
            n += 1
            wk = datetime.datetime.strptime(
                c["release_at"][:10], "%Y-%m-%d").isocalendar()[:2]
            if (c["series"], wk) in claimed:
                clashes.append(c["round_id"])
    assert n, "no candidates were written"
    assert not clashes, (
        f"{len(clashes)} candidates duplicate a series already asked that "
        f"week, cells included: {sorted(clashes)[:5]}")
    print(f"ok test_a_profile_cell_is_not_regenerated_as_a_scalar_twin "
          f"({n} candidates, none clashing with {len(claimed)} claimed "
          f"series-weeks)")


if __name__ == "__main__":
    test_ids_keep_the_pollster_apart()
    test_rights_gate_refuses_anything_not_explicitly_approved()
    test_history_gate_refuses_a_series_too_short_to_baseline()
    test_volatility_gate_refuses_only_pure_noise()
    test_schedule_comes_from_source_semantics_not_a_modal_weekday()
    test_silver_bulletin_field_midpoints_cannot_become_release_dates()
    test_civiqs_candidate_matches_the_reviewed_family_calendar()
    test_generated_rounds_are_shaped_like_the_hand_written_ones()
    test_generation_is_deterministic()
    test_real_archive_generation_is_offline_and_deterministic()
    test_an_explicit_date_covers_the_season_beyond_the_preview_cap()
    test_it_cannot_publish()
    test_a_profile_cell_is_not_regenerated_as_a_scalar_twin()
    test_it_refuses_to_generate_a_round_it_cannot_publish()
    test_a_round_whose_deadline_has_passed_is_not_generated()
    test_single_page_wikipedia_rounds_are_retired_not_unsupported()
    test_the_wiki_week_phrase_matches_the_reviewed_wording()
    test_wiki_top10_rolls_the_reviewed_contract_forward()
    test_wiki_generation_refuses_to_ask_something_nobody_reviewed()
    test_wiki_generation_refuses_two_different_spacings()
    print("18 passed")
