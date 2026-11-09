"""Unscored pilot rounds: run in full, counted on no board.

A round with `"scored": false` and a `"pilot"` note (ssa/season.is_pilot) is
posted, locked, sealed and resolved like any other, and shown on the site
labelled "pilot, unscored". It must not move a single aggregate. These tests
take the committed published payload, add pilot rounds whose forecasts would
otherwise change every board, and hold each board to the byte.
"""
import copy
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import bundle, refresh, season                       # noqa: E402

PASSED = []


def ok(fn):
    PASSED.append(fn.__name__)
    print("ok", fn.__name__)


def dump(x):
    return json.dumps(x, sort_keys=True, separators=(",", ":"))


def _published():
    with open(os.path.join(ROOT, "site", "data.json")) as f:
        data = json.load(f)
    rounds = data["rounds"]
    resolved = {r["round_id"]: r["resolution"] for r in rounds
                if r.get("status") == "resolved"
                and isinstance(r.get("resolution"), dict)
                and "value" in r["resolution"]}
    return data, rounds, resolved


class Forecasts:
    """refresh.FORECASTS pointed at a scratch tree: the real round folders
    linked in, plus the pilot folders this test writes. Nothing under the
    repository's forecasts/ is touched."""

    def __enter__(self):
        self.tmp = tempfile.mkdtemp(prefix="ssa-pilot-")
        for name in os.listdir(refresh.FORECASTS):
            src = os.path.join(refresh.FORECASTS, name)
            if os.path.isdir(src):
                os.symlink(src, os.path.join(self.tmp, name))
        self.saved = refresh.FORECASTS
        refresh.FORECASTS = self.tmp
        return self

    def copy_round(self, src_id, dst_id):
        shutil.copytree(os.path.join(self.saved, src_id),
                        os.path.join(self.tmp, dst_id))
        for fn in os.listdir(os.path.join(self.tmp, dst_id)):
            p = os.path.join(self.tmp, dst_id, fn)
            if fn.endswith(".json"):
                with open(p) as f:
                    body = json.load(f)
                body["round_id"] = dst_id
                with open(p, "w") as f:
                    json.dump(body, f)

    def __exit__(self, *exc):
        refresh.FORECASTS = self.saved
        shutil.rmtree(self.tmp)


def _pilot_of(r, rid, pilot=True):
    out = copy.deepcopy(r)
    out["round_id"] = rid
    for k in ("scores", "persistence_crps", "pilot_scores"):
        out.pop(k, None)
    if pilot:
        out["scored"] = False
        out["pilot"] = "unscored pilot (test)"
    return out


def test_the_validator_accepts_one_spelling_and_refuses_the_rest():
    with open(os.path.join(ROOT, "questions", "season0.json")) as f:
        base = json.load(f)["rounds"]
    r = copy.deepcopy(base[-1])
    r["round_id"] = "pilot-check-" + r["round_id"][-20:].strip("-")
    r["release_at"] = "2031-01-03T12:00:00Z"
    r["lock_at"] = "2031-01-01T12:00:00Z"

    def problems(**fields):
        x = dict(r, **fields)
        for k, v in list(x.items()):
            if v is None:
                del x[k]
        return [p for p in season.validate_document({"rounds": [x]})
                if "pilot" in p or "scored" in p]
    assert problems() == []
    assert problems(scored=False, pilot="go/no-go pilot") == []
    assert season.is_pilot(dict(r, scored=False, pilot="x"))
    assert not season.is_pilot(r)
    assert problems(scored=True), "scored: true is not a second spelling"
    assert problems(scored="false")
    assert problems(scored=False), "a pilot names why it is one"
    assert problems(scored=False, pilot="  ")
    assert problems(scored=False, pilot="x" * 501)
    assert problems(pilot="orphan note")
    ok(test_the_validator_accepts_one_spelling_and_refuses_the_rest)


def test_a_pilot_number_round_leaves_every_board_byte_identical():
    data, rounds, resolved = _published()
    # Clone a resolved number round whose forecasts would move the board.
    src = next(r for r in rounds
               if r.get("target_type") == "continuous_normal"
               and r.get("baselines") and r["round_id"] in resolved
               and len(r.get("scores") or {}) >= 3)
    with Forecasts() as fx:
        fx.copy_round(src["round_id"], "t-pilot-number")
        fx.copy_round(src["round_id"], "t-control-number")
        res = dict(resolved, **{"t-pilot-number": resolved[src["round_id"]],
                                "t-control-number": resolved[src["round_id"]]})
        before = copy.deepcopy(rounds)
        board_before = refresh.build_leaderboard(before, resolved)
        main_before = refresh.build_main_board(before)
        block_before = refresh.leaderboard_block(before, board_before)

        pilot = _pilot_of(src, "t-pilot-number")
        pilot["lock_at"] = max(pilot["lock_at"], refresh.SEASON0_MAIN_START)
        after = copy.deepcopy(rounds) + [pilot]
        board_after = refresh.build_leaderboard(after, res)
        main_after = refresh.build_main_board(after)
        block_after = refresh.leaderboard_block(after, board_after)

        assert dump(board_after) == dump(board_before)
        assert dump(main_after) == dump(main_before)
        assert dump(after[:-1]) == dump(before), "an existing round's payload moved"
        assert block_after["resolved_rounds"] == block_before["resolved_rounds"]
        assert block_after["pilot_resolved"] == 1
        assert "pilot_resolved" not in block_before
        got = after[-1]
        assert "scores" not in got and len(got["pilot_scores"]) >= 3
        assert got["pilot_scores"] == src["scores"], "scored the same way"

        # Control: the same round without the flag does move the board, so
        # the identity above is not vacuous.
        control = _pilot_of(src, "t-control-number", pilot=False)
        control["lock_at"] = pilot["lock_at"]
        moved = copy.deepcopy(rounds) + [control]
        assert dump(refresh.build_leaderboard(moved, res)) != dump(board_before)
        assert dump(refresh.build_main_board(moved)) != dump(main_before)
    ok(test_a_pilot_number_round_leaves_every_board_byte_identical)


def test_a_pilot_profile_round_leaves_the_profile_board_byte_identical():
    data, rounds, _ = _published()
    published = {pr["round_id"]: pr for pr in data["profile"]["rounds"]}
    src = next(r for r in rounds if r["round_id"] in published)
    # One published profile round, resolved from its own published outcome,
    # so the board is rebuilt from the payload alone (the energy score is
    # slow enough that the whole season would cost a minute a pass).
    rounds = [src]
    resolved = {src["round_id"]: {"values": published[src["round_id"]]["outcome"]}}
    with Forecasts() as fx:
        fx.copy_round(src["round_id"], "t-pilot-profile")
        before = copy.deepcopy(rounds)
        prof_before = refresh.build_profile_leaderboard(before, resolved, {})
        pilot = _pilot_of(src, "t-pilot-profile")
        after = copy.deepcopy(rounds) + [pilot]
        res = dict(resolved, **{"t-pilot-profile": resolved[src["round_id"]]})
        prof_after = refresh.build_profile_leaderboard(after, res, {})
        listed = prof_after.pop("pilot_rounds")
        assert dump(prof_after) == dump(prof_before)
        assert [p["round_id"] for p in listed] == ["t-pilot-profile"]
        refresh.attach_round_scores(after, dict(prof_after, pilot_rounds=listed), None)
        assert after[-1]["status"] == "resolved"
        assert "scores" not in after[-1] and after[-1]["pilot_scores"]
        assert dump(refresh.build_main_board(after)) == \
            dump(refresh.build_main_board(before))
    ok(test_a_pilot_profile_round_leaves_the_profile_board_byte_identical)


def test_the_payload_carries_the_label_and_the_bundle_says_so():
    with open(os.path.join(ROOT, "questions", "season0.json")) as f:
        base = json.load(f)["rounds"]
    r = next(x for x in base if x.get("target_type", "continuous_normal")
             == "continuous_normal" and "consumer" not in x)
    q = bundle.question_from_round(r)
    assert "scored" not in q and "pilot" not in q, "ordinary bundle unchanged"
    p = dict(r, scored=False, pilot="unscored pilot (test)")
    q = bundle.question_from_round(p)
    assert q["scored"] is False and q["pilot"] == "unscored pilot (test)"
    # The closed bundle schema admits the two keys, and only in this shape.
    shell = {"schema_version": "x", "batch_id": "batch-2031-01-01",
             "deadline": "2031-01-01T12:00:00Z",
             "published_at": "2030-12-29T00:00:00Z"}
    def errors(question):
        return [e for e in bundle.schema_errors(
                    dict(shell, questions=[question]), "bundle.schema.json")
                if e.startswith("questions/0")]
    assert errors(q) == [], errors(q)
    assert errors(dict(q, scored=True))
    assert errors(dict(q, pilot=""))
    src = open(os.path.join(ROOT, "site", "index.html")).read()
    assert "pilot, unscored" in src and "r.scored===false" in src
    ok(test_the_payload_carries_the_label_and_the_bundle_says_so)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"{len(PASSED)} passed")
