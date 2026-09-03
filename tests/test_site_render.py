"""What a participant actually sees, run against real published data.

Every other test here checks what the pipeline computes. This one checks that
the page renders it. Those are different failures: `site/data.json` carried a
scored profile board for weeks while `site/leaderboard.html` read no such key,
so the board existed, was correct, and was invisible -- and a participant asked
to answer a profile round had nowhere to see whether it was scored at all.

The page's own `<script>` is executed against a small DOM (`tests/site/`), so
what is checked is the shipped code rather than a paraphrase of it. Node is
used because the page is JavaScript; when it is absent the test says so and
passes, since a participant cloning this repository to file a forecast should
not need a JavaScript runtime.
"""
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOARDS = os.path.join(ROOT, "tests", "site", "render_boards.js")
INDEX = os.path.join(ROOT, "tests", "site", "render_index.js")


def _node():
    return shutil.which("node")


def _run(check, data_path):
    return subprocess.run([_node(), check, data_path],
                          capture_output=True, text=True, cwd=ROOT, timeout=120)


def _fixture_data():
    """A data.json to render. Prefers the committed one; otherwise builds the
    smallest object the page needs, from the committed season file.

    Never fetches. A test that reaches the network fails on a plane and passes
    for the wrong reason behind a proxy.
    """
    committed = os.path.join(ROOT, "site", "data.json")
    if os.path.exists(committed):
        return committed
    with open(os.path.join(ROOT, "questions", "season0.json")) as fh:
        season = json.load(fh)
    rounds = season["rounds"] if isinstance(season, dict) else season
    out = os.path.join(ROOT, "site", "_render_fixture.json")
    with open(out, "w") as fh:
        json.dump({"generated_at": "2026-09-03T00:00:00Z", "season": 0,
                   "rounds": rounds, "entrants": [], "trackers": {},
                   "charts": {}, "leaderboard": {"entries": [],
                                                 "resolved_rounds": 0},
                   "backtest": {}, "profile": {"board": [], "matched": [],
                                               "note": "energy score."},
                   "ranking": {"board": [], "matched": [],
                               "note": "rank distance."}}, fh)
    return out


def test_every_leaderboard_tab_renders_something_a_participant_can_read():
    if not _node():
        print("ok test_every_leaderboard_tab_renders_something_a_participant_"
              "can_read (skipped: no node on PATH)")
        return
    data = _fixture_data()
    try:
        got = _run(BOARDS, data)
    finally:
        if data.endswith("_render_fixture.json"):
            os.remove(data)
    sys.stdout.write(got.stdout)
    assert got.returncode == 0, got.stderr or got.stdout
    # Each of the four boards has to name its own third number. They are a
    # CRPS in points, an energy score in cell-space and a rank distance; one
    # shared column label would silently invite the reader to compare them.
    for want in ("col5=CRPS", "col5=Energy", "col5=Loss"):
        assert want in got.stdout, f"{want} missing from:\n{got.stdout}"
    print("ok test_every_leaderboard_tab_renders_something_a_participant_can_read")


def test_the_landing_page_names_each_round_shape_and_the_right_deadline():
    """Two things the first page a participant sees has to get right.

    It presented a sixteen-cell profile and a top-ten ranking exactly like a
    one-number round, so the season's other two answer shapes were invisible
    until the bundle arrived. And the midterm list labelled its date `locks`
    from `lock_at` -- 2026-10-30T22:00Z, whose batch deadline is the Monday
    four days earlier, so the page offered a date on which submissions were
    already closed.
    """
    if not _node():
        print("ok test_the_landing_page_names_each_round_shape_and_the_right_"
              "deadline (skipped: no node on PATH)")
        return
    data = _fixture_data()
    try:
        got = _run(INDEX, data)
    finally:
        if data.endswith("_render_fixture.json"):
            os.remove(data)
    sys.stdout.write(got.stdout)
    assert got.returncode == 0, got.stderr or got.stdout
    print("ok test_the_landing_page_names_each_round_shape_and_the_right_deadline")


def test_the_page_reads_only_keys_the_pipeline_publishes():
    """A renamed key is invisible until someone opens the page.

    `refresh.main` writes the object; the pages read it by name. This walks the
    names out of the shipped HTML and requires each to be a key the writer
    actually produces, so a rename breaks a test rather than a board.
    """
    import ast
    import re
    with open(os.path.join(ROOT, "ssa", "refresh.py")) as fh:
        tree = ast.parse(fh.read())
    # Parsed rather than grepped: the object is a dict literal several hundred
    # lines long with comments between the entries, and a regex over it picks
    # up every quoted word in those comments.
    written = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Dict):
            continue
        names = {t.id for t in node.targets if isinstance(t, ast.Name)}
        if not names & {"data", "payload"}:
            continue
        keys = {k.value for k in node.value.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)}
        if {"rounds", "leaderboard"} <= keys:      # the site payload, not a
            written |= keys                       # same-named local elsewhere
    assert written, "could not find the keys refresh writes into data.json"

    read = set()
    for page in ("index.html", "leaderboard.html"):
        with open(os.path.join(ROOT, "site", page)) as fh:
            read |= set(re.findall(r"\bdata\.([a-z_]+)\b", fh.read()))
    read.discard("json")                  # `data.json`, the file name
    missing = sorted(read - written)
    assert not missing, (
        f"the site reads keys refresh does not write: {missing}. Either the "
        f"key was renamed and a board is now blank, or the page is reading "
        f"something that never existed.")
    print(f"ok test_the_page_reads_only_keys_the_pipeline_publishes "
          f"({len(read)} keys read, all published)")


def test_no_page_promises_a_date_it_cannot_know():
    """A static page cannot know what happens next.

    `site/docs.html` carried "(next: preliminary Aug 14, final Aug 28, both
    10:00 ET)" beside a link to the very calendar that answers the question.
    It was three weeks stale by the time anyone was pointed at the site, which
    is the kind of wrong that makes a live benchmark read as abandoned.

    This is a rule rather than a date check, so it cannot itself go stale: a
    page may state a fixed event ("the midterm, Nov 3, 2026") or a fact about
    the past ("live rounds from Aug 11"), and may not claim to know the next
    occurrence of a recurring release. That belongs in `data.json`, which is
    rebuilt every six hours, or behind the link.
    """
    import re
    forward = re.compile(
        r"(next:|next release|upcoming release|coming up)[^<.]{0,40}"
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}",
        re.IGNORECASE)
    offenders = []
    for name in sorted(os.listdir(os.path.join(ROOT, "site"))):
        if not name.endswith(".html"):
            continue
        with open(os.path.join(ROOT, "site", name)) as fh:
            body = fh.read()
        for m in forward.finditer(body):
            offenders.append(f"{name}: {m.group(0)[:70]}")
    assert not offenders, (
        "a page hard-codes the next occurrence of a recurring release, which "
        "is wrong within a month of being written:\n  "
        + "\n  ".join(offenders))
    print(f"ok test_no_page_promises_a_date_it_cannot_know "
          f"({len([n for n in os.listdir(os.path.join(ROOT, 'site')) if n.endswith('.html')])} pages)")


if __name__ == "__main__":
    test_every_leaderboard_tab_renders_something_a_participant_can_read()
    test_the_landing_page_names_each_round_shape_and_the_right_deadline()
    test_the_page_reads_only_keys_the_pipeline_publishes()
    test_no_page_promises_a_date_it_cannot_know()
    print("4 passed")
