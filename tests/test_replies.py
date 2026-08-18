"""The reply log: paid replies survive, and runs resume. No network.

Run: PYTHONPATH=. python tests/test_replies.py

Every property here is about money already spent. A live call is billed the
moment the provider answers, and before this log the only thing kept was the
*parsed* forecast -- so a reply with prose around its JSON, or a run cancelled
between the call and the commit, left the tokens gone and the text with them.
The tests below hold the log to the two claims that makes it worth committing:
a reply is on disk before anything tries to parse it, and a reply on disk is
never bought twice.

The provider is faked throughout and counts its calls, because "zero calls" is
the assertion that matters in half of these.
"""
import glob
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa import harness, personas, replies, series as series_registry

ROUND = {"round_id": "r1", "series": "yougov_approval", "unit": "%",
         "question": "q", "release_at": "2026-08-20T14:00:00Z",
         "baselines": {"persistence": {"mean": 41.0, "sd": 1.5}}}
HIST = [{"date": f"2026-08-{i+1:02d}", "value": 40.0 + i} for i in range(12)]
DISABLED = ('claude-opus-4-8 @ https://api.anthropic.com/v1 HTTP 400: '
            '{"error":{"message":"This organization has been disabled."}}')
GOOD = '{"mean": 42.5, "sd": 1.8}'
USAGE = {"input_tokens": 900, "output_tokens": 40, "thinking_tokens": 300}


def prompt_for(hist=HIST):
    return harness.build_prompt(ROUND, hist, "recent10", "direct")


class Sandbox:
    """A temp reply log, and no mock fallback, for the duration.

    Two things a test in this file must never do: write into the repository's
    own `replies/`, and let SSA_ALLOW_MOCK in the environment turn a raised
    failure into a filed placeholder -- which is exactly the failure half of
    these tests are checking for.
    """

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="ssa-replies-")
        self.saved_dir = os.environ.get("SSA_REPLIES_DIR")
        os.environ["SSA_REPLIES_DIR"] = self.dir
        self.saved_mock = harness.ALLOW_MOCK
        harness.ALLOW_MOCK = False
        return self

    def __exit__(self, *a):
        harness.ALLOW_MOCK = self.saved_mock
        if self.saved_dir is None:
            os.environ.pop("SSA_REPLIES_DIR", None)
        else:
            os.environ["SSA_REPLIES_DIR"] = self.saved_dir
        shutil.rmtree(self.dir, ignore_errors=True)

    def files(self, pattern="*/*.json"):
        return sorted(glob.glob(os.path.join(self.dir, pattern)))

    def leftovers(self):
        return sorted(glob.glob(os.path.join(self.dir, "**", "*.tmp"),
                                recursive=True))


class Keys:
    """Exactly the named keys present, everything else cleared."""

    NAMES = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPEN_ROUTER")

    def __init__(self, **present):
        self.present = present

    def __enter__(self):
        self.saved = {k: os.environ.get(k) for k in self.NAMES}
        for k in self.NAMES:
            os.environ.pop(k, None)
        os.environ.update(self.present)
        return self

    def __exit__(self, *a):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class Routed:
    """SSA_OPENROUTER set for the duration, restored after."""

    def __init__(self, value):
        self.value = value

    def __enter__(self):
        self.saved = os.environ.get("SSA_OPENROUTER")
        if self.value is None:
            os.environ.pop("SSA_OPENROUTER", None)
        else:
            os.environ["SSA_OPENROUTER"] = self.value
        return self

    def __exit__(self, *a):
        if self.saved is None:
            os.environ.pop("SSA_OPENROUTER", None)
        else:
            os.environ["SSA_OPENROUTER"] = self.saved


class Provider:
    """Stands in for call_provider, counting calls and recording the route.

    `reply` may be a string or a function of the prompt, which is how the
    persona panel gives different respondents different answers.
    """

    def __init__(self, reply=GOOD, fail_direct=None, fail_standby=None):
        self.reply, self.fail_direct = reply, fail_direct
        self.fail_standby = fail_standby
        self.calls = []

    def __call__(self, entrant, prompt, with_usage=False, context=None, via=None):
        self.calls.append(via or harness.route(entrant)["via"])
        bad = self.fail_standby if self.calls[-1] == "openrouter" else self.fail_direct
        if bad:
            raise RuntimeError(bad)
        text = self.reply(prompt) if callable(self.reply) else self.reply
        return (text, dict(USAGE)) if with_usage else text

    def __enter__(self):
        harness.forget_dead_routes()
        self.saved = harness.call_provider
        harness.call_provider = self
        return self

    def __exit__(self, *a):
        harness.call_provider = self.saved
        harness.forget_dead_routes()


def test_a_reply_is_written_before_anything_tries_to_parse_it():
    """The whole point. A reply that fails to parse has still been paid for, and
    used to leave nothing behind at all -- the run raised, the runner was thrown
    away, and the only way to see what the model actually said was to buy it
    again."""
    with Sandbox() as box, Keys(ANTHROPIC_API_KEY="k"), Routed(None):
        with Provider("I am not going to give you a number.") as p:
            try:
                harness.forecast("claude-opus", ROUND, HIST)
                assert False, "an unparseable reply filed a forecast"
            except RuntimeError as e:
                assert "no JSON object" in str(e), e
        assert p.calls == ["direct"], p.calls

        ih = harness.prompt_hash("claude-opus", prompt_for())
        rec = replies.lookup("r1", "claude-opus", ih)
        assert rec, "the reply was billed and lost"
        assert rec["reply"] == "I am not going to give you a number."
        # everything needed to say which call this was, without the prompt
        assert rec["round_id"] == "r1" and rec["entrant"] == "claude-opus"
        assert rec["input_hash"] == ih
        assert rec["model"] == harness.model_id("claude-opus")
        assert rec["via"] == "direct"
        assert rec["prompt_sha256"] == replies.prompt_sha256(prompt_for())
        assert rec["usage"] == USAGE, "the token report is the run's cost record"
        assert rec["logged_at"].endswith("Z")
        # one call, one file, in the round's own directory
        assert box.files() == [replies.path("r1", "claude-opus", ih)], box.files()


def test_a_logged_reply_is_filed_without_paying_for_it_again():
    """Resume. The forecast file is gone -- a cancelled workflow, a crash after
    the call -- but the reply is not, so the next run files it for nothing."""
    prompt = prompt_for()
    ih = harness.prompt_hash("claude-opus", prompt)
    with Sandbox(), Keys(ANTHROPIC_API_KEY="k"), Routed(None):
        replies.log("r1", "claude-opus", ih, {
            "model": harness.model_id("claude-opus"), "via": "direct",
            "prompt_sha256": replies.prompt_sha256(prompt),
            "reply": GOOD, "usage": dict(USAGE)})

        with Provider() as p:
            f = harness.forecast("claude-opus", ROUND, HIST)
        assert p.calls == [], "the run bought a reply it already had"
        assert f["topline"] == {"mean": 42.5, "sd": 1.8}, f
        # The `in=` convention is untouched -- it is what the next run matches
        # on and what the leaderboard cites -- and the note says where this came
        # from, because a forecast has to say whether it was bought or recovered.
        assert f"in={ih}" in f["notes"], f["notes"]
        assert "replayed" in f["notes"], f["notes"]
        assert "via=direct" in f["notes"], f["notes"]

        # and the layer above still short-circuits on the filed forecast, so a
        # replayed forecast is not re-read from the log every six hours either
        with Provider() as p2:
            again = harness.forecast("claude-opus", ROUND, HIST, previous=f)
        assert again == f and p2.calls == [], p2.calls


def test_a_miss_pays_once_and_writes_what_it_bought():
    with Sandbox() as box, Keys(ANTHROPIC_API_KEY="k"), Routed(None):
        ih = harness.prompt_hash("claude-opus", prompt_for())
        assert replies.lookup("r1", "claude-opus", ih) is None, "log not empty"
        with Provider() as p:
            f = harness.forecast("claude-opus", ROUND, HIST)
        assert p.calls == ["direct"], p.calls
        assert "replayed" not in f["notes"], "a bought reply claimed to be a replay"
        rec = replies.lookup("r1", "claude-opus", ih)
        assert rec["reply"] == GOOD and rec["usage"] == USAGE
        assert len(box.files()) == 1, box.files()


def test_a_reply_to_a_different_prompt_is_not_reused():
    """The log is keyed on (call identity, prompt), like everything else here.
    A new observation is a new question, and answering it with last week's reply
    would file a forecast the model never made."""
    stale = prompt_for()
    stale_ih = harness.prompt_hash("claude-opus", stale)
    fresh_hist = HIST + [{"date": "2026-08-13", "value": 52.0}]
    fresh_ih = harness.prompt_hash("claude-opus", prompt_for(fresh_hist))
    assert stale_ih != fresh_ih

    with Sandbox(), Keys(ANTHROPIC_API_KEY="k"), Routed(None):
        replies.log("r1", "claude-opus", stale_ih, {
            "model": harness.model_id("claude-opus"), "via": "direct",
            "prompt_sha256": replies.prompt_sha256(stale),
            "reply": GOOD, "usage": None})
        with Provider('{"mean": 51.0, "sd": 2.0}') as p:
            f = harness.forecast("claude-opus", ROUND, fresh_hist)
        assert p.calls == ["direct"], "the stale reply was reused"
        assert f["topline"] == {"mean": 51.0, "sd": 2.0}, f
        assert f"in={fresh_ih}" in f["notes"], f["notes"]
        # both are on disk: the old one is evidence, not garbage
        assert replies.lookup("r1", "claude-opus", stale_ih)["reply"] == GOOD
        assert replies.lookup("r1", "claude-opus", fresh_ih)["reply"] \
            == '{"mean": 51.0, "sd": 2.0}'


def test_an_unparseable_log_entry_is_replaced_rather_than_believed():
    """The reply that motivated the log is one that does not parse. If a stored
    reply like that could block the call, the log would turn a one-run failure
    into a permanent one."""
    prompt = prompt_for()
    ih = harness.prompt_hash("claude-opus", prompt)
    with Sandbox(), Keys(ANTHROPIC_API_KEY="k"), Routed(None):
        replies.log("r1", "claude-opus", ih, {
            "model": harness.model_id("claude-opus"), "via": "direct",
            "prompt_sha256": replies.prompt_sha256(prompt),
            "reply": "sorry, no.", "usage": None})
        with Provider() as p:
            f = harness.forecast("claude-opus", ROUND, HIST)
        assert p.calls == ["direct"], "an unparseable entry blocked the call"
        assert f["topline"] == {"mean": 42.5, "sd": 1.8}, f
        assert replies.lookup("r1", "claude-opus", ih)["reply"] == GOOD, \
            "the good reply must overwrite the one that could not be used"


def test_the_write_is_atomic_and_a_failed_one_never_raises():
    """A half-written file would be read back as a reply nobody gave. And the
    write must not be able to cost a forecast: the caller is holding something
    it has already paid for, so an unwritable log reports itself and gets out of
    the way."""
    with Sandbox() as box:
        for i in range(3):
            assert replies.log("r1", "claude-opus", f"aaaaaaaaaaa{i}",
                               {"reply": GOOD, "usage": None})
        assert len(box.files()) == 3, box.files()
        assert box.leftovers() == [], box.leftovers()

    # a root that cannot hold a directory: reported, not raised
    with tempfile.NamedTemporaryFile() as blocker:
        saved = os.environ.get("SSA_REPLIES_DIR")
        os.environ["SSA_REPLIES_DIR"] = blocker.name
        try:
            assert replies.log("r1", "e", "abc123abc123",
                               {"reply": GOOD, "usage": None}) is None
            assert replies.lookup("r1", "e", "abc123abc123") is None
        finally:
            if saved is None:
                os.environ.pop("SSA_REPLIES_DIR", None)
            else:
                os.environ["SSA_REPLIES_DIR"] = saved


def test_a_standby_reply_is_logged_under_the_standbys_own_hash():
    """The route is in the key and in the record. A reply bought from the
    standby must not be filed as the vendor's -- and this is the run most likely
    to have died halfway, since the standby only exists on days when a vendor
    account has stopped serving."""
    prompt = prompt_for()
    direct_ih = harness.prompt_hash("claude-opus", prompt)
    fb_ih = harness.prompt_hash("claude-opus", prompt, via="openrouter")
    with Sandbox(), Keys(ANTHROPIC_API_KEY="k", OPEN_ROUTER="sk-or-test"), \
            Routed(None):
        with Provider(fail_direct=DISABLED) as p:
            f = harness.forecast("claude-opus", ROUND, HIST)
        assert p.calls == ["direct", "openrouter"], p.calls
        assert "via=openrouter" in f["notes"], f["notes"]

        rec = replies.lookup("r1", "claude-opus", fb_ih)
        assert rec and rec["via"] == "openrouter", rec
        assert rec["model"] == harness.OPENROUTER_MODELS["claude-opus"], rec
        assert replies.lookup("r1", "claude-opus", direct_ih) is None, \
            "a standby reply was filed under a hash the vendor never answered"

        # The forecast file is lost and the vendor is still down: the standby is
        # not billed a second time for a prompt it has already answered.
        with Provider(fail_direct=DISABLED) as p2:
            again = harness.forecast("claude-opus", ROUND, HIST)
        assert p2.calls == ["direct"], p2.calls
        assert "replayed" in again["notes"] and "via=openrouter" in again["notes"]
        assert again["topline"] == f["topline"]


def test_two_personas_get_two_files_and_resume_one_at_a_time():
    """A panel is 192 calls under one input hash, so the persona id is in the
    filename. Without that the panel would share a single entry, and an
    interrupted panel would have to re-buy all 192 answers to recover 37."""
    entrant = "claude-opus-zeroshot-persona"
    spec = series_registry.survey("yougov_approval")
    panel = personas.panel()
    weights = personas.weights_for(spec.get("population"))
    prompts = {p["id"]: harness.build_persona_prompt(p, spec) for p in panel}
    ih = harness.prompt_hash(entrant, "\n\n".join(prompts[p["id"]]
                                                  for p in panel))
    seeded = [panel[0]["id"], panel[1]["id"]]

    with Sandbox() as box, Keys(ANTHROPIC_API_KEY="k"), Routed(None):
        for pid in seeded:
            replies.log("r1", entrant, ih, {
                "model": harness.model_id(entrant), "via": "direct",
                "prompt_sha256": replies.prompt_sha256(prompts[pid]),
                "reply": '{"approval": "approve"}', "usage": None,
                "persona": pid})
        assert len(box.files()) == 2, "two respondents, two files"

        with Provider('{"approval": "disapprove"}') as p:
            out = harness.forecast_persona(entrant, ROUND, HIST)
        assert len(p.calls) == len(panel) - len(seeded), \
            f"{len(p.calls)} calls for a panel of {len(panel)} with 2 on disk"

        # Only the two replayed respondents approve, so the topline is exactly
        # their share of the panel -- which is only true if each file was read
        # back for its own persona and not for the other's.
        expected = 100 * sum(weights[pid] for pid in seeded)
        assert abs(out["topline"]["mean"] - expected) < 0.05, (out, expected)
        assert "2 replayed" in out["notes"], out["notes"]
        assert f"in={ih}" in out["notes"], out["notes"]
        # every respondent is now on disk, one file each, and the panel-level
        # name is not among them
        assert len(box.files()) == len(panel), len(box.files())
        assert replies.lookup("r1", entrant, ih) is None, \
            "a panel wrote a file under the bare panel hash"
        for pid in seeded:
            assert replies.lookup("r1", entrant, ih, persona=pid)["persona"] == pid


def test_a_second_run_of_a_finished_panel_buys_nothing():
    """192 calls is the most expensive entrant in the season by two orders of
    magnitude, so a panel that has already answered must never be re-run by a
    refresh that lost its forecast file."""
    entrant = "claude-opus-zeroshot-persona"
    with Sandbox(), Keys(ANTHROPIC_API_KEY="k"), Routed(None):
        with Provider(lambda pr: '{"approval": "approve"}'
                      if "Republican" in pr else '{"approval": "disapprove"}') as p:
            first = harness.forecast_persona(entrant, ROUND, HIST)
        assert len(p.calls) == len(personas.panel()), len(p.calls)

        with Provider('{"approval": "approve"}') as p2:
            second = harness.forecast_persona(entrant, ROUND, HIST)
        assert p2.calls == [], f"{len(p2.calls)} respondents bought twice"
        # every answer came back the same way, so the topline is unchanged even
        # though the fake would now answer differently
        assert second["topline"] == first["topline"], (first, second)


def test_the_log_is_not_written_into_the_repository_by_a_test():
    """A guard on this file, not on the code: every test above redirects the log
    with `Sandbox`, and one that forgot would quietly start committing replies
    for a round that does not exist."""
    saved = os.environ.get("SSA_REPLIES_DIR")
    os.environ.pop("SSA_REPLIES_DIR", None)
    try:
        assert replies.root() == os.path.join(replies.ROOT, "replies")
        assert not glob.glob(os.path.join(replies.root(), "r1", "*.json")), \
            "a test wrote into the repository's own reply log"
    finally:
        if saved is not None:
            os.environ["SSA_REPLIES_DIR"] = saved


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
