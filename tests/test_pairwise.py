"""Offline calculation and visible-page checks for the proposed pairwise board."""
import os
import shutil
import subprocess


def test_pairwise_calculation_and_rendering():
    node = shutil.which('node')
    if not node:
        print('skipped: pairwise browser tests require Node')
        return
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    result = subprocess.run([node, 'tests/site/pairwise.js'], cwd=root,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    print(result.stdout.strip())


if __name__ == '__main__':
    test_pairwise_calculation_and_rendering()
