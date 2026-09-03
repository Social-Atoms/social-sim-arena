"""Sibling adapters may not make as-of decisions from a machine-local clock.

Run: PYTHONPATH=. python tests/test_adapter_clocks.py
"""
import ast
import os


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADAPTERS = os.path.join(ROOT, "ssa", "adapters")


def _local_clock_calls(path):
    with open(path) as f:
        tree = ast.parse(f.read(), filename=path)
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        # date.today() is inherently local. datetime.now() is local when it has
        # no timezone argument. strptime/fromisoformat are parsing, not clocks.
        if node.func.attr == "today" or (
                node.func.attr == "now" and not node.args and not node.keywords):
            bad.append(node.lineno)
    return bad


def test_every_adapter_clock_is_explicitly_utc():
    failures = []
    for name in sorted(os.listdir(ADAPTERS)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(ADAPTERS, name)
        for line in _local_clock_calls(path):
            failures.append(f"ssa/adapters/{name}:{line}")
    assert not failures, "machine-local clock used at " + ", ".join(failures)


if __name__ == "__main__":
    test_every_adapter_clock_is_explicitly_utc()
    print("ok test_every_adapter_clock_is_explicitly_utc")
