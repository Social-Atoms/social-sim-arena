"""Route A: the contract test, run against the example server on loopback.

Run: PYTHONPATH=. python tests/test_agent_probe.py

No provider is called and no network leaves this machine -- the fixture server
from `examples/agent-api/server.py` is started in process on 127.0.0.1 and torn
down again. That is deliberate: a contract test that needs a real endpoint is a
contract test nobody runs, and the properties worth pinning here are all about
what the probe *refuses*, which a live endpoint cannot be asked to demonstrate.

The four that matter:

- an endpoint that answers a wrong bearer token is one anyone can file
  forecasts through, so the probe fails it rather than reporting auth as
  untested;
- an endpoint tested only on a scalar fixture fails on the first profile round
  of the season, after the deadline, so every shape is a separate verdict;
- a 4xx is an answer and must not be retried, or a misconfigured request
  becomes five misconfigured requests;
- a revoked entrant is not called at all, because a revocation the arena does
  not honour is a revocation in name only.
"""
import importlib.util
import json
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

    def __init__(self, api_key=None, delay=0.0):
        self.api_key = api_key
        self.delay = delay

    def __enter__(self):
        with socket.socket() as probe_socket:
            probe_socket.bind(("127.0.0.1", 0))
            port = probe_socket.getsockname()[1]
        self.server = SERVER.serve("127.0.0.1", port, self.api_key, self.delay)
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


def test_the_example_server_answers_all_three_round_shapes():
    """A real batch mixes them; an endpoint proved on one is proved on one."""
    with Fixture() as fixture:
        rows = verdicts(probe_tool.probe(fixture.url, timeout=5))
    for shape in ("scalar", "profile", "ranking"):
        ok, detail = rows[f"round/{shape}"]
        assert ok, (shape, detail)
    assert rows["idempotency"][0], rows["idempotency"]
    assert rows["transport"][0], rows["transport"]


def test_a_correct_key_passes_and_a_wrong_one_is_required_to_be_refused():
    """Configuring a key and never testing that it is enforced is the common
    version of leaving the endpoint open."""
    with Fixture(api_key="probe-secret") as fixture:
        rows = verdicts(probe_tool.probe(fixture.url, key="probe-secret",
                                         timeout=5))
    assert rows["round/scalar"][0], rows["round/scalar"]
    assert rows["auth"][0], rows["auth"]


def test_a_bad_credential_fails_the_probe_rather_than_being_reported_as_open():
    with Fixture(api_key="probe-secret") as fixture:
        rows = verdicts(probe_tool.probe(fixture.url, key="wrong-key",
                                         timeout=5, retries=0))
    ok, detail = rows["round/scalar"]
    assert not ok, detail
    assert "401" in detail, detail


def test_a_4xx_is_an_answer_and_is_never_retried():
    """Retrying a rejected request is a way to be told no four more times, and
    against a metered endpoint it is a way to be billed for it."""
    attempts = []

    class Counting(SERVER.Handler):
        api_key = "probe-secret"
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
                            key="wrong", timeout=5, retries=3)
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
        rows = verdicts(probe_tool.probe(fixture.url, timeout=0.5,
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


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
