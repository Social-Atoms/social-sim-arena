"""Route A: the contract test, run against the example server on loopback.

Run: PYTHONPATH=. python tests/test_agent_probe.py

No provider is called and no network leaves this machine -- the fixture server
from `examples/agent-api/server.py` is started in process on 127.0.0.1 and torn
down again. That is deliberate: a contract test that needs a real endpoint is a
contract test nobody runs, and the properties worth pinning here are all about
what the probe *refuses*, which a live endpoint cannot be asked to demonstrate.

The four that matter:

- the probe signs with the published test key the way the arena signs live
  requests, so an endpoint that verifies is exercised as it will be called,
  and a bad signature is required to be refused with 401/403;
- an endpoint tested only on a scalar fixture fails on the first profile round
  of the season, after the deadline, so every shape is a separate verdict;
- a 4xx is an answer and must not be retried, or a misconfigured request
  becomes five misconfigured requests;
- a revoked entrant is not called at all, because a revocation the arena does
  not honour is a revocation in name only.
"""
import importlib.util
import json
import math
import os
import socket
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import tools.probe_agent_api as probe_tool                    # noqa: E402


def load_example_server():
    spec = importlib.util.spec_from_file_location(
        "_example_agent_server",
        os.path.join(ROOT, "examples", "agent-api", "server.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SERVER = load_example_server()


class Fixture:
    """The example server on a free loopback port, for the life of a `with`."""

    def __init__(self, verify=True, delay=0.0):
        self.verify = verify
        self.delay = delay

    def __enter__(self):
        with socket.socket() as probe_socket:
            probe_socket.bind(("127.0.0.1", 0))
            port = probe_socket.getsockname()[1]
        self.server = SERVER.serve("127.0.0.1", port, self.verify, self.delay)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{port}/forecast"
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def verdicts(rows):
    return {name: (ok, detail) for name, ok, detail in rows}


SIGNER = probe_tool.make_signer(probe_tool.TEST_PRIVATE_KEY, probe_tool.TEST_KEY_ID)


def test_the_example_server_answers_all_three_round_shapes():
    """A real batch mixes them; an endpoint proved on one is proved on one."""
    with Fixture() as fixture:
        rows = verdicts(probe_tool.probe(fixture.url, SIGNER, timeout=5))
    for shape in ("scalar", "profile", "ranking"):
        ok, detail = rows[f"round/{shape}"]
        assert ok, (shape, detail)
    assert rows["idempotency"][0], rows["idempotency"]
    assert rows["transport"][0], rows["transport"]


def test_a_verifying_endpoint_refuses_a_bad_signature_and_an_unsigned_request():
    """The signature check sends a corrupted signature and requires 401/403;
    an unsigned request must be refused too, or verification is decorative."""
    with Fixture(verify=True) as fixture:
        rows = verdicts(probe_tool.probe(fixture.url, SIGNER, timeout=5))
        assert rows["round/scalar"][0], rows["round/scalar"]
        assert rows["signature"][0] and "verifies" in rows["signature"][1], rows["signature"]
        try:
            probe_tool.call(fixture.url, probe_tool.envelope("scalar", "unsigned"),
                            None, timeout=5, retries=0)
            raise AssertionError("an unsigned request was answered by a verifying server")
        except probe_tool.ProbeFailure as err:
            assert "401" in str(err), err


def test_an_open_endpoint_is_reported_as_open_not_failed():
    """Verifying is the participant's choice. An endpoint that answers a bad
    signature is not a contract failure; the probe says what it saw."""
    with Fixture(verify=False) as fixture:
        rows = verdicts(probe_tool.probe(fixture.url, SIGNER, timeout=5))
    assert rows["signature"][0], rows["signature"]
    assert "does not verify" in rows["signature"][1], rows["signature"]
    with Fixture(verify=False) as fixture:
        rows = verdicts(probe_tool.probe(fixture.url, None, timeout=5))
    assert rows["round/scalar"][0] and "unsigned" in rows["signature"][1], rows


def test_a_4xx_is_an_answer_and_is_never_retried():
    """Retrying a rejected request is a way to be told no four more times, and
    against a metered endpoint it is a way to be billed for it."""
    attempts = []

    class Counting(SERVER.Handler):
        verify = True
        keys = SERVER.PUBLIC_KEYS
        answered = {}
        delay = 0.0

        def do_POST(self):
            attempts.append(1)
            super().do_POST()

    from http.server import ThreadingHTTPServer
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = ThreadingHTTPServer(("127.0.0.1", port), Counting)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        try:
            probe_tool.call(f"http://127.0.0.1:{port}/forecast",
                            probe_tool.envelope("scalar", "id"),
                            SIGNER, timeout=5, retries=3, corrupt=True)
            assert False, "a 401 was accepted"
        except probe_tool.ProbeFailure as err:
            assert "401" in str(err), err
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert len(attempts) == 1, f"a 4xx was retried {len(attempts)} times"


def test_a_slow_endpoint_times_out_rather_than_hanging_the_run():
    """An endpoint that answers in eleven minutes looks exactly like one that
    works, right up until the read timeout closes the round."""
    with Fixture(delay=3.0) as fixture:
        rows = verdicts(probe_tool.probe(fixture.url, SIGNER, timeout=0.5,
                                         retries=0, shapes=("scalar",)))
    ok, detail = rows["round/scalar"]
    assert not ok, detail
    assert "no reply" in detail, detail


def test_http_is_refused_for_anything_that_is_not_loopback():
    """A bearer token over http is a token you have published."""
    rows = verdicts(probe_tool.probe("http://example.com/forecast", timeout=1))
    assert not rows["transport"][0], rows["transport"]
    assert len(rows) == 1, "the probe kept going after refusing the transport"


def test_a_wrong_shaped_reply_fails_the_shape_it_answered():
    """A valid profile is still a wrong answer to a scalar round: it satisfies
    the versioned response schema and answers a different question."""
    content = {"schema_version": "ssa-agent-api-v2",
               "forecast": {"profile": {"probe_cell_a": {"mean": 1, "sd": 1},
                                        "probe_cell_b": {"mean": 1, "sd": 1}}}}
    try:
        probe_tool.check_content(content, "scalar")
        assert False, "a profile passed as a scalar answer"
    except probe_tool.ProbeFailure as err:
        assert "mean" in str(err), err

    partial = {"schema_version": "ssa-agent-api-v2",
               "forecast": {"profile": {c: {"mean": 1.0, "sd": 1.0}
                                        for c in ["probe_cell_a",
                                                  "probe_cell_b"]}}}
    try:
        probe_tool.check_content(partial, "profile")
        assert False, "a profile missing half its cells passed"
    except probe_tool.ProbeFailure as err:
        assert "missing" in str(err), err


def test_a_revoked_entrant_is_not_probed():
    """Revocation that only lives in a private console is revocation in name
    only: the thing that has to stop is the call."""
    saved = probe_tool.ROOT
    root = tempfile.mkdtemp(prefix="ssa-probe-")
    try:
        os.makedirs(os.path.join(root, "entrants"))
        path = os.path.join(root, "entrants", "probe_demo.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"entrant_id": "probe_demo", "name": "Demo",
                       "type": "llm", "method": "example",
                       "status": "revoked"}, fh)
        probe_tool.ROOT = root
        assert probe_tool.main(["--url", "https://example.com/forecast",
                                "--entrant", "probe_demo"]) == 1
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"entrant_id": "probe_demo", "name": "Demo",
                       "type": "llm", "method": "example"}, fh)
        entrant = probe_tool.load_entrant("probe_demo")
        assert entrant.get("status") != "revoked"
    finally:
        probe_tool.ROOT = saved
        import shutil
        shutil.rmtree(root)


def test_the_registered_entrants_all_satisfy_the_schema_with_status_added():
    """`status` is optional, so adding it must not invalidate a single one of
    the registrations already in the repository."""
    import jsonschema
    with open(os.path.join(ROOT, "schema", "entrant.schema.json"),
              encoding="utf-8") as fh:
        schema = json.load(fh)
    directory = os.path.join(ROOT, "entrants")
    names = sorted(n for n in os.listdir(directory) if n.endswith(".json"))
    assert names, "no registrations to check"
    for name in names:
        with open(os.path.join(directory, name), encoding="utf-8") as fh:
            jsonschema.validate(json.load(fh), schema)


def test_the_public_result_contains_only_the_checked_reply_and_upserts():
    """The shareable proof must show the answer without publishing its URL.

    Free-form reasoning is participant-controlled and may also contain a
    provider response id, so neither it nor registration/contact fields are
    copied into the static dev feed.
    """
    entrant = {
        "entrant_id": "probe_demo",
        "name": "Probe demo",
        "type": "participant",
        "contact": "secret@example.test",
        "route": {"kind": "agent_api",
                  "url": "https://secret.example.test/forecast"},
    }
    reply = {
        "schema_version": "ssa-agent-api-v2",
        "forecast": {"mean": 50, "sd": 1},
        "reasoning_trace": "provider secret and response id",
    }
    first = probe_tool.public_probe_result(
        entrant, reply, "github-action-1", "2026-09-08T17:00:00Z",
        "https://github.com/Social-Atoms/social-sim-arena/actions/runs/1")
    assert first["response"]["forecast"] == {"mean": 50, "sd": 1}
    assert first["round"]["round_id"] == "ssa-contract-test"

    root = tempfile.mkdtemp(prefix="ssa-public-probe-")
    try:
        path = os.path.join(root, "agent-probes.json")
        probe_tool.record_public_result(path, first)
        second = probe_tool.public_probe_result(
            entrant, {**reply, "forecast": {"mean": 51.25, "sd": 2}},
            "github-action-2", "2026-09-08T17:05:00Z")
        feed = probe_tool.record_public_result(path, second)
        assert len(feed["probes"]) == 1
        assert feed["probes"][0]["request_id"] == "github-action-2"
        assert feed["probes"][0]["response"]["forecast"]["mean"] == 51.25
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
        for secret in ("secret.example", "secret@example", "reasoning_trace",
                       "provider secret", "response id"):
            assert secret not in raw, secret
    finally:
        import shutil
        shutil.rmtree(root)


def test_a_historical_demo_uses_the_real_lock_and_production_score():
    """Immediate scoring is retrospective, but its inputs and metric are real."""
    case = probe_tool.historical_demo_case(
        ROOT, "probe_demo", "yougov-2026-w34-approval")
    request = case["request"]
    assert request["request_id"] == "probe_demo:yougov-2026-w34-approval"
    assert request["round"]["question"] == (
        "Economist/YouGov wave publishing ~Aug 18, Trump % approve among US "
        "adult citizens")
    assert request["round"]["context"]["persistence"] == 33.0
    history = request["round"]["context"]["history"]
    assert len(history) == 24 and history[-1] == {
        "date": "2026-08-08", "value": 33.0}
    assert "resolution" not in request["round"]

    reply = {"schema_version": "ssa-agent-api-v2",
             "forecast": {"mean": 34.5, "sd": 1.0},
             "reasoning_trace": "not public"}
    result = probe_tool.public_historical_demo_result(
        {"entrant_id": "probe_demo", "name": "Probe demo",
         "type": "participant"},
        reply, case, tested_at="2026-09-08T22:00:00Z")
    score = result["evaluation"]
    assert result["kind"] == "historical_demo" and score["retrospective"] is True
    assert score["outcome"] == 35.0 and score["rounds"] == 1
    assert math.isclose(score["loss"], 0.331403531254856, abs_tol=1e-12)
    assert math.isclose(score["persistence_loss"], 1.280900969802875,
                        abs_tol=1e-12)
    assert math.isclose(score["arena_score"], 74.1273104582115,
                        abs_tol=1e-10)
    assert "context" not in result["round"] and "reasoning_trace" not in result


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
