"""Execute the shipped docs navigation against deterministic scroll geometry."""
import shutil
import subprocess
from pathlib import Path


def test_docs_anchor_navigation():
    node = shutil.which("node")
    if not node:
        print("skipped docs navigation: node is not installed")
        return
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [node, "tests/site/docs_navigation.js"], cwd=root,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    print(result.stdout, end="")


if __name__ == "__main__":
    test_docs_anchor_navigation()
