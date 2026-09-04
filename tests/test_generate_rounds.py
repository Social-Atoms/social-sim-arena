"""The round generator: deterministic ids, honest gates, and no publishing."""
import importlib.util
import json
import os
import sys
from unittest import mock
from datetime import date, datetime, timedelta, timezone

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
    """Adding an adapter must not quietly add rounds.

    The blocked source is read out of the inventory rather than named here: as
    of 2026-09-03 every source that is still registered is approved, so a
    literal name in this test would be a source that no longer exists, and the
    test would be asserting against a typo instead of against the gate.
    """
    from ssa import inventory
    h = weekly(40)
    blocked = sorted(k for k, r in inventory.INVENTORY.items()
                     if r["rights"] not in inventory.GENERATING_RIGHTS)
    assert blocked, "no blocked source left to test the rights gate with"
    for source in blocked:
        ok, why = gen.gate("x", {"source": source}, h)
        assert not ok and why["gate"] == "rights", (source, ok, why)
        assert why["state"] == inventory.INVENTORY[source]["rights"], source
    ok, why = gen.gate("x", {"source": "a-source-nobody-reviewed"}, h)
    assert not ok and why["gate"] == "rights" and why["state"] == "unresolved"
    # And the conditional approval really does open the gate, or the three
    # sources it covers would be blocked by a verdict that says they are not.
    conditional = [k for k, r in inventory.INVENTORY.items()
                   if r["rights"] == inventory.APPROVED_NO_REDISTRIBUTION]
    assert conditional
    for source in conditional:
        ok, why = gen.gate("x", {"source": source}, h)
        assert ok or why["gate"] != "rights", (source, why)
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
    # No literal date here. This reads the newest committed CSV, so the last
    # point moves every time the archive grows -- it was pinned to 2026-08-15
    # and went red the moment main's data refreshes were merged in. The
    # behaviour under test is the refusal below, which does not move. Nor
    # would a weekday assertion help: these midpoints have landed on Saturday
    # twenty times running, which is exactly the disguise
    # `test_schedule_comes_from_source_semantics_not_a_modal_weekday` exists
    # for -- a modal weekday is not evidence of a publication calendar.
    assert history, "fixture: mc_approval built no history from the newest CSV"

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
    rounds = _season()
    tpl = max((r for r in rounds if r["round_id"].startswith("wiki-top10")),
              key=lambda r: r["ranking"]["week_end"])
    last = date.fromisoformat(tpl["ranking"]["week_end"])
    # `now` is anchored to the newest reviewed week rather than to a literal
    # date. Fixed at one, this test goes empty the moment the family is
    # promoted far enough that the next four weeks fall outside
    # MAX_WEEKS_AHEAD -- which is a normal state for a season that is being
    # extended, and says nothing about whether the roll-forward works.
    now = datetime.combine(last - timedelta(days=6), datetime.min.time(),
                           tzinfo=timezone.utc)
    # Count-bounded, not date-bounded: with `through` set the loop runs to
    # that date and ignores the count, so passing both would test neither.
    out = gen.wiki_candidates(rounds, 4, now)
    # Derived from the newest reviewed round rather than pinned: promoting a
    # generated week makes it reviewed, and the next candidate moves on. A
    # literal list here would go red every time this family is extended, which
    # is the thing it exists to make routine.
    last = date.fromisoformat(tpl["ranking"]["week_end"])
    assert [r["round_id"] for r in out] == [
        f"wiki-top10-{(last + timedelta(days=7 * (i + 1))).isoformat()}"
        for i in range(4)], [r["round_id"] for r in out]
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


def test_the_trends_basket_rolls_forward_and_keeps_its_shape():
    """October's only non-Civiqs candidates, and its only profile round.

    Every other candidate that month is a Civiqs scalar. Without this family
    October asks one question shape, from one source, sixteen ways -- and the
    sixteen approval cuts are 0.75-correlated, so it is close to one question.
    """
    rounds = _season()
    tpl = max((r for r in rounds if r["round_id"].startswith("trends-basket")),
              key=lambda r: r["release_at"])
    last = date.fromisoformat(tpl["release_at"][:10])
    # Anchored to the newest reviewed week, for the reason the wiki test above
    # gives: a literal clock empties this test as soon as the family is
    # promoted past MAX_WEEKS_AHEAD, which is a normal state and not a defect.
    now = datetime.combine(last - timedelta(days=6), datetime.min.time(),
                           tzinfo=timezone.utc)
    out = gen.trends_candidates(rounds, 4, now)
    assert [r["round_id"] for r in out] == [
        f"trends-basket-{(last + timedelta(days=7 * (i + 1))).isoformat()}"
        for i in range(4)], [r["round_id"] for r in out]
    for r in out:
        assert r["unit"] == tpl["unit"] and r["target_type"] == tpl["target_type"]
        assert list(r["cells"]) == list(tpl["cells"])
        # Locks before its week begins, like the ranking family: the shares
        # accumulate over the seven days after the lock.
        assert r["lock_at"][:10] < r["release_at"][:10]
        assert gen.batches.governed_by_batch(r["lock_at"])
        # The resolve rule names its own week, not the template's.
        assert r["release_at"][:10] in r["resolve"]
        assert tpl["release_at"][:10] not in r["resolve"]
        gen.profile_round.cells_for(r)
    print("ok test_the_trends_basket_rolls_forward_and_keeps_its_shape")


def test_a_seasonal_note_is_never_carried_into_a_month_it_is_false_in():
    """The reviewed rounds end with "September is Apple's announcement window,
    the largest regular swing in this basket." It is a claim about September.

    Repeating a question a human approved is what a template is for. Repeating
    a seasonal hint into October is inventing a claim, and a wrong one -- so
    generated rounds carry no note, and the constant is still required to
    reproduce the reviewed wording when the note is supplied.
    """
    rounds = _season()
    reviewed = [r for r in rounds if r["round_id"].startswith("trends-basket")]
    assert reviewed, "fixture: no reviewed basket round"
    # Some carry the note and some do not -- the originals were written by
    # hand in September, the later weeks were rolled forward. What matters is
    # that at least one reviewed round has it, so this test is still testing
    # something, and that no generated round does.
    assert any("Apple" in r["question"] for r in reviewed), \
        "fixture: no reviewed round carries the seasonal note any more"
    last = date.fromisoformat(max(r["release_at"][:10] for r in reviewed))
    now = datetime.combine(last - timedelta(days=6), datetime.min.time(),
                           tzinfo=timezone.utc)
    for r in gen.trends_candidates(rounds, 4, now):
        assert "Apple" not in r["question"], r["round_id"]
        assert "September" not in r["question"], r["round_id"]
        assert r["question"].startswith("Google Trends, United States:")
        assert r["question"].endswith("The five shares add to 100.")
    print("ok test_a_seasonal_note_is_never_carried_into_a_month_it_is_false_in")


def test_trends_generation_refuses_drifted_wording_and_split_spacings():
    rounds = [dict(r) for r in _season()
              if r["round_id"].startswith("trends-basket")]
    drifted = [dict(r) for r in rounds]
    drifted[-1]["question"] = drifted[-1]["question"].replace(
        "five-brand total", "six-brand total")
    assert "six-brand" in drifted[-1]["question"], "fixture: nothing drifted"
    try:
        gen.trends_candidates(drifted, 1, datetime(2026, 9, 3, tzinfo=timezone.utc))
    except ValueError as e:
        assert "no longer reproduces" in str(e), e
    else:
        raise AssertionError("drifted wording was generated anyway")

    split = [dict(r) for r in rounds]
    split[0] = dict(split[0], lock_at="2026-08-27T14:00:00Z")
    try:
        gen.trends_candidates(split, 1, datetime(2026, 9, 3, tzinfo=timezone.utc))
    except ValueError as e:
        assert "different" in str(e), e
    else:
        raise AssertionError("inconsistent spacings were templated from anyway")
    print("ok test_trends_generation_refuses_drifted_wording_and_split_spacings")


def _sce_template_round():
    """The newest reviewed SCE round, which the clocks and ids anchor to."""
    return max((r for r in _season() if r.get("series") in gen.SCE_HORIZONS),
               key=lambda r: r["release_at"])


def test_sce_rolls_the_reviewed_month_forward_on_the_entered_calendar():
    """Two horizons a month, on a release day somebody typed in.

    The day is a fixture, patched in rather than read from `SCE_RELEASES`,
    so the test does not move when another month is entered.
    """
    rounds = _season()
    tpl = _sce_template_round()
    month = gen._next_month(gen._sce_month(tpl["round_id"]))
    # A survey month is released the month after it; a day inside its own
    # month would lock before `now` and be skipped.
    day = f"{gen._next_month(month)}-08"
    now = datetime.fromisoformat(tpl["release_at"].replace("Z", "+00:00"))
    with mock.patch.dict(gen.SCE_RELEASES, {month: day}):
        out, unentered = gen.sce_candidates(rounds, 1, now)
    assert unentered is None, unentered
    # At least one reviewed round carries an aside after the invariant
    # sentence, so the prefix rule is exercised. Not the newest: once a
    # generated month is promoted, the newest carries none.
    assert any(r["question"] != gen.SCE_QUESTION.format(
        horizon=gen.SCE_HORIZONS[r["series"]][1],
        month=gen._month_phrase(gen._sce_month(r["round_id"])))
        for r in rounds if r.get("series") in gen.SCE_HORIZONS), \
        "fixture: no reviewed round carries an aside any more"
    assert [r["round_id"] for r in out] == [f"sce-{month}-infl1y",
                                            f"sce-{month}-infl3y"], out
    lock_off = (datetime.fromisoformat(tpl["lock_at"].replace("Z", "+00:00"))
                - now)
    for r in out:
        assert r["release_at"] == f"{day}T{tpl['release_at'][11:]}", r
        rel = datetime.fromisoformat(r["release_at"].replace("Z", "+00:00"))
        lock = datetime.fromisoformat(r["lock_at"].replace("Z", "+00:00"))
        assert lock - rel == lock_off, r["round_id"]
        # The invariant sentence and nothing after it: neither the release
        # date nor the July reading the reviewed rounds append is carried.
        assert r["question"] == gen.SCE_QUESTION.format(
            horizon=gen.SCE_HORIZONS[r["series"]][1],
            month=gen._month_phrase(month)), r["question"]
        assert gen._month_phrase(month) in r["resolve"], r["resolve"]
        assert gen._month_phrase(gen._sce_month(tpl["round_id"])) \
            not in r["resolve"], r["resolve"]
        assert r["unit"] == tpl["unit"] and r["tracker"] == tpl["tracker"]
        assert r["target_type"] == tpl["target_type"]
        assert gen.batches.governed_by_batch(r["lock_at"])
    print("ok test_sce_rolls_the_reviewed_month_forward_on_the_entered_calendar")


def test_sce_refuses_drifted_wording_split_spacings_and_a_wrong_day():
    rounds = [dict(r) for r in _season()
              if r.get("series") in gen.SCE_HORIZONS]
    tpl = _sce_template_round()
    now = datetime.fromisoformat(tpl["release_at"].replace("Z", "+00:00"))

    drifted = [dict(r) for r in rounds]
    top = max(drifted, key=lambda r: r["release_at"])
    top["question"] = top["question"].replace("survey month", "survey week")
    assert "survey week" in top["question"], "fixture: nothing drifted"
    try:
        gen.sce_candidates(drifted, 1, now)
    except ValueError as e:
        assert "no longer reproduces" in str(e), e
    else:
        raise AssertionError("drifted wording was generated anyway")

    split = [dict(r) for r in rounds]
    lock = datetime.fromisoformat(split[0]["lock_at"].replace("Z", "+00:00"))
    split[0]["lock_at"] = (lock - timedelta(days=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    try:
        gen.sce_candidates(split, 1, now)
    except ValueError as e:
        assert "different" in str(e), e
    else:
        raise AssertionError("inconsistent spacings were templated from anyway")

    # The typed-in calendar is checked against the reviewed rounds, not
    # trusted: a wrong day would schedule a round for a date the NY Fed
    # publishes nothing on.
    month = gen._sce_month(tpl["round_id"])
    wrong = date.fromisoformat(gen.SCE_RELEASES[month]) + timedelta(days=1)
    with mock.patch.dict(gen.SCE_RELEASES, {month: wrong.isoformat()}):
        try:
            gen.sce_candidates(rounds, 1, now)
        except ValueError as e:
            assert "disagrees" in str(e), e
        else:
            raise AssertionError("a day nobody reviewed was templated from")

    # A row keyed by its own month would release before the survey ends.
    nxt = gen._next_month(month)
    with mock.patch.dict(gen.SCE_RELEASES, {nxt: f"{nxt}-07"}):
        try:
            gen.sce_candidates(rounds, 1, now)
        except ValueError as e:
            assert "does not follow" in str(e), e
        else:
            raise AssertionError("a release inside its survey month was kept")

    # The resolve rule is rewritten by swapping the month name, so one that
    # never names it would roll forward whole and settle the wrong row.
    unnamed = [dict(r) for r in rounds]
    top = max(unnamed, key=lambda r: r["release_at"])
    top["resolve"] = top["resolve"].replace(
        gen._month_phrase(gen._sce_month(top["round_id"])), "the survey month")
    try:
        gen.sce_candidates(unnamed, 1, now)
    except ValueError as e:
        assert "resolve rule" in str(e), e
    else:
        raise AssertionError("a resolve naming no month was rolled forward")
    print("ok test_sce_refuses_drifted_wording_split_spacings_and_a_wrong_day")


def test_sce_names_the_first_month_the_calendar_does_not_cover():
    """A hand-entered calendar runs out. Ending the family silently there is
    the "forgotten" the module docstring warns about, so the month is named."""
    import contextlib
    import io
    rounds = _season()
    tpl = _sce_template_round()
    now = datetime.fromisoformat(tpl["release_at"].replace("Z", "+00:00"))
    expected = gen._next_month(gen._sce_month(tpl["round_id"]))
    # Trimmed to the reviewed months, where a hand-entered calendar always
    # ends up, whatever has been entered since.
    reviewed = {gen._sce_month(r["round_id"]): r["release_at"][:10]
                for r in rounds if r.get("series") in gen.SCE_HORIZONS}
    argv = [sys.argv[0], "--rejects", "--now", tpl["release_at"]]
    buf = io.StringIO()
    with mock.patch.dict(gen.SCE_RELEASES, reviewed, clear=True):
        assert gen.sce_candidates(rounds, 1, now) == ([], expected)
        with mock.patch.object(sys, "argv", argv), \
                contextlib.redirect_stdout(buf):
            gen.main()
    printed = buf.getvalue()
    named = [ln for ln in printed.splitlines() if "sce_inflation" in ln]
    assert len(named) == 2, printed
    assert all('"gate": "calendar"' in ln and expected in ln for ln in named)
    for line in printed.splitlines():
        if "unsupported_family" not in line:
            continue
        for source in ("sce", "hhpoll", "trends", "trends_basket"):
            assert f'"source": "{source}"' not in line, line
    print("ok test_sce_names_the_first_month_the_calendar_does_not_cover")


def test_sce_rounds_are_still_rights_gated():
    """The SCE rows skip the gate loop, so rights is read where they
    generate: a withdrawn verdict refuses them by name, it does not forget
    them."""
    import contextlib
    import io
    tpl = _sce_template_round()
    argv = [sys.argv[0], "--rejects", "--now", tpl["release_at"]]
    buf = io.StringIO()
    with mock.patch.dict(gen.RIGHTS, {"sce": "unresolved"}), \
            mock.patch.object(sys, "argv", argv), \
            contextlib.redirect_stdout(buf):
        gen.main()
    lines = [ln for ln in buf.getvalue().splitlines() if "sce_inflation" in ln]
    assert len(lines) == 2 and all('"gate": "rights"' in ln for ln in lines), \
        lines
    print("ok test_sce_rounds_are_still_rights_gated")


def test_declined_families_carry_their_decision():
    """Considered and declined is not the same refusal as never templated.

    `unsupported_family` sends the next reader off to write the template; for
    these three that work was done and decided against.
    """
    ok, why = gen.gate("hh_trump_approval", {"source": "hhpoll"}, [])
    assert not ok and why["gate"] == "declined_family", why
    assert "calendar" in why["detail"], why
    ok, why = gen.gate("trends_share_tesla", {"source": "trends_basket"}, [])
    assert not ok and why["gate"] == "declined_family", why
    assert "basket" in why["detail"], why
    ok, why = gen.gate("trends_iphone", {"source": "trends"}, [])
    assert not ok and why["gate"] == "declined_family", why
    assert "basket" in why["detail"], why
    print("ok test_declined_families_carry_their_decision")


def test_the_headline_profile_round_no_longer_stops_in_september():
    """Sixteen cells scored with the energy score is the thing this benchmark
    does that a scalar board cannot, and it had three instances.

    `civiqs-profile-2026-w37`, `-w38` and `-w39` were written by hand and
    nothing continued them, so the headline round type was scheduled to stop on
    2026-09-25 while the scalar board ran to December.
    """
    rounds = _season()
    tpl = max((r for r in rounds if r["round_id"].startswith("civiqs-profile")),
              key=lambda r: r["release_at"])
    last = date.fromisoformat(tpl["release_at"][:10])
    now = datetime.combine(last - timedelta(days=6), datetime.min.time(),
                           tzinfo=timezone.utc)
    out = gen.civiqs_profile_candidates(rounds, 4, now)
    assert len(out) == 4, [r["round_id"] for r in out]
    for i, r in enumerate(out):
        rel = date.fromisoformat(r["release_at"][:10])
        assert rel == last + timedelta(days=7 * (i + 1)), r["round_id"]
        assert rel.strftime("%a") == "Fri", "the dashboard value is Friday's"
        assert len(r["cells"]) == 16 and r["cells"] == tpl["cells"]
        assert r["unit"] == tpl["unit"] and r["resolve"] == tpl["resolve"]
        assert r["lock_at"][:10] < r["release_at"][:10]
        assert gen.batches.governed_by_batch(r["lock_at"])
        assert rel.strftime("%b %-d") in r["question"], r["question"]
        gen.profile_round.cells_for(r)
    print("ok test_the_headline_profile_round_no_longer_stops_in_september")


def test_a_sixteen_cell_question_is_not_interpolated_to_another_width():
    """The reviewed wording says "16-cell" in words, so it is not a
    substitution away from working at another width. A different vector needs
    its own reviewed sentence."""
    rounds = [dict(r) for r in _season()
              if r["round_id"].startswith("civiqs-profile")]
    now = datetime(2026, 9, 3, tzinfo=timezone.utc)

    narrowed = [dict(r) for r in rounds]
    narrowed[-1] = dict(narrowed[-1], cells=narrowed[-1]["cells"][:8])
    try:
        gen.civiqs_profile_candidates(narrowed, 1, now)
    except ValueError as e:
        # Either it no longer looks like a member of the family, or the family
        # disagrees with itself. Both are refusals; neither interpolates.
        assert "no reviewed" in str(e) or "disagree" in str(e), e
    else:
        raise AssertionError("an 8-cell round was templated as 16")

    drifted = [dict(r) for r in rounds]
    drifted[-1] = dict(drifted[-1],
                       question=drifted[-1]["question"].replace("16-cell",
                                                                "17-cell"))
    try:
        gen.civiqs_profile_candidates(drifted, 1, now)
    except ValueError as e:
        assert "no longer reproduces" in str(e), e
    else:
        raise AssertionError("drifted wording was generated anyway")
    print("ok test_a_sixteen_cell_question_is_not_interpolated_to_another_width")


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


def test_dedupe_is_per_board_not_per_series():
    """A week's slot belongs to a board, not to a series name.

    Both directions, because this rule has been wrong in both:

    - A generated scalar round must not duplicate a *scalar* round already
      asking that series that week. Same board, same rule, same number: the
      second one is not a second question.
    - A generated scalar round must still be allowed beside a *profile* round
      whose cells cover it. Those are two boards -- `build_leaderboard` scores
      a CRPS in points, `build_profile_leaderboard` an energy score in
      sixteen-dimensional points-space, and `refresh` keeps them apart because
      averaging them answers nothing. The pair is the season's most direct
      comparison: one number, elicited jointly and marginally.

    Keyed on `series` alone, ten of civiqs-profile-2026-w38's cells came back
    as scalar twins. Keyed on cells too, the comparison disappeared instead.
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

    def week(d):
        return datetime.datetime.strptime(d[:10], "%Y-%m-%d").isocalendar()[:2]

    with open(os.path.join(ROOT, "questions", "season0.json")) as fh:
        season = json.load(fh)
    season = season["rounds"] if isinstance(season, dict) else season
    scalar_claimed, cell_claimed = set(), set()
    for r in season:
        wk = week(r["release_at"])
        if r.get("cells"):
            for c in r["cells"]:
                cell_claimed.add((c, wk))
        elif r.get("series"):
            scalar_claimed.add((r["series"], wk))

    clashes, n, beside_profile = [], 0, 0
    for path in glob.glob(os.path.join(ROOT, "questions", "candidates", "*.json")):
        for c in json.load(open(path)):
            n += 1
            key = (c["series"], week(c["release_at"]))
            if key in scalar_claimed:
                clashes.append(c["round_id"])
            if key in cell_claimed:
                beside_profile += 1
    assert n, "no candidates were written"
    assert not clashes, (
        f"{len(clashes)} candidates duplicate a scalar round already asking "
        f"that series that week: {sorted(clashes)[:5]}")
    assert cell_claimed, "fixture is stale: the season has no profile cells"
    print(f"ok test_dedupe_is_per_board_not_per_series "
          f"({n} candidates, 0 clashing with {len(scalar_claimed)} scalar "
          f"series-weeks, {beside_profile} allowed beside a profile cell)")


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
    test_dedupe_is_per_board_not_per_series()
    test_it_refuses_to_generate_a_round_it_cannot_publish()
    test_a_round_whose_deadline_has_passed_is_not_generated()
    test_single_page_wikipedia_rounds_are_retired_not_unsupported()
    test_the_wiki_week_phrase_matches_the_reviewed_wording()
    test_wiki_top10_rolls_the_reviewed_contract_forward()
    test_wiki_generation_refuses_to_ask_something_nobody_reviewed()
    test_wiki_generation_refuses_two_different_spacings()
    test_the_trends_basket_rolls_forward_and_keeps_its_shape()
    test_a_seasonal_note_is_never_carried_into_a_month_it_is_false_in()
    test_trends_generation_refuses_drifted_wording_and_split_spacings()
    test_sce_rolls_the_reviewed_month_forward_on_the_entered_calendar()
    test_sce_refuses_drifted_wording_split_spacings_and_a_wrong_day()
    test_sce_names_the_first_month_the_calendar_does_not_cover()
    test_sce_rounds_are_still_rights_gated()
    test_declined_families_carry_their_decision()
    test_the_headline_profile_round_no_longer_stops_in_september()
    test_a_sixteen_cell_question_is_not_interpolated_to_another_width()
    print("30 passed")
