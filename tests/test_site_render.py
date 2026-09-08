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
AGENT_PROBE = os.path.join(ROOT, "tests", "site", "agent_probe.js")


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


def test_agent_results_are_visible_without_getting_an_official_rank():
    if not _node():
        print("ok test_agent_results_are_visible_without_getting_an_official_rank "
              "(skipped: no node on PATH)")
        return
    got = subprocess.run([_node(), AGENT_PROBE], capture_output=True, text=True,
                         cwd=ROOT, timeout=120)
    sys.stdout.write(got.stdout)
    assert got.returncode == 0, got.stderr or got.stdout
    print("ok test_agent_results_are_visible_without_getting_an_official_rank")


def test_resizable_panels_and_chart_widths():
    if not _node():
        print("ok test_resizable_panels_and_chart_widths (skipped: no node on PATH)")
        return
    check = os.path.join(ROOT, "tests", "site", "resize_panels.js")
    got = subprocess.run([_node(), check], capture_output=True, text=True,
                         cwd=ROOT, timeout=120)
    sys.stdout.write(got.stdout)
    assert got.returncode == 0, got.stderr or got.stdout
    print("ok test_resizable_panels_and_chart_widths")


def test_model_filter_selection_and_persistence():
    if not _node():
        print("ok test_model_filter_selection_and_persistence (skipped: no node on PATH)")
        return
    check = os.path.join(ROOT, "tests", "site", "model_filters.js")
    got = subprocess.run([_node(), check], capture_output=True, text=True,
                         cwd=ROOT, timeout=120)
    sys.stdout.write(got.stdout)
    assert got.returncode == 0, got.stderr or got.stdout
    print("ok test_model_filter_selection_and_persistence")
def test_a_batch_is_open_until_its_last_question_closes():
    """Rendered against a week that straddles now, because that is the only
    state the bug appears in and the live payload is rarely in it.

    Each question closes on its own clock, so a batch spends most of its week
    part closed. The calendar decided the batch was finished when its *first*
    question closed: `batch-2026-09-28` went grey and dropped off the list on
    09-30 with eight of its twelve questions still open, and the countdown
    beside it ran negative. Measured on this fixture before the fix: shown as
    closed with 17 of 19 questions still open.
    """
    if not _node():
        print("ok test_a_batch_is_open_until_its_last_question_closes "
              "(skipped: no node on PATH)")
        return
    import tempfile
    from datetime import datetime, timedelta, timezone

    source = _fixture_data()
    try:
        with open(source) as fh:
            data = json.load(fh)
    finally:
        if source.endswith("_render_fixture.json"):
            os.remove(source)

    now = datetime.now(timezone.utc)
    by_batch = {}
    for r in data.get("rounds") or []:
        if r.get("batch_id"):
            by_batch.setdefault(r["batch_id"], []).append(r)
    straddled = next((rs for rs in by_batch.values() if len(rs) >= 4), None)
    if straddled is None:
        print("ok test_a_batch_is_open_until_its_last_question_closes "
              "(skipped: no batched rounds in the payload)")
        return
    for i, r in enumerate(straddled):
        when = now - timedelta(hours=1) if i < 2 else now + timedelta(days=3)
        r["deadline"] = r["lock_at"] = when.strftime("%Y-%m-%dT%H:%M:%SZ")

    tmp = tempfile.mkdtemp(prefix="ssa-straddle-")
    path = os.path.join(tmp, "data.json")
    with open(path, "w") as fh:
        json.dump(data, fh)
    try:
        got = _run(BOARDS, path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    sys.stdout.write(got.stdout)
    assert got.returncode == 0, got.stderr or got.stdout
    print("ok test_a_batch_is_open_until_its_last_question_closes")


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


def test_every_link_the_docs_send_a_participant_to_exists():
    """A doc that points at a section which is not there is worse than one that
    points nowhere: the reader assumes they misread the page.

    The quickstart now tells a participant where their answer shows up -- the
    calendar, the rounds table, the four boards, source freshness -- and those
    are four anchors on one page that a later edit can rename without anything
    noticing.
    """
    import re
    refs = set()
    docs = os.path.join(ROOT, "docs")
    for name in sorted(os.listdir(docs)):
        if not name.endswith(".md"):
            continue
        with open(os.path.join(docs, name)) as fh:
            body = fh.read()
        for m in re.finditer(
                r"social-simulation-arena\.com/([a-z]+\.html)#([A-Za-z0-9_-]+)",
                body):
            refs.add((m.group(1), m.group(2), name))
        # Relative links between the docs themselves rot the same way.
        for m in re.finditer(r"\]\((?!https?:)([a-zA-Z0-9_./-]+\.md)\)", body):
            target = os.path.normpath(os.path.join(docs, m.group(1)))
            assert os.path.exists(target), \
                f"{name} links to {m.group(1)}, which does not exist"

    missing = []
    for page, anchor, src in sorted(refs):
        path = os.path.join(ROOT, "site", page)
        if not os.path.exists(path):
            missing.append(f"{src} -> {page} (no such page)")
            continue
        with open(path) as fh:
            html = fh.read()
        if f'id="{anchor}"' not in html:
            missing.append(f"{src} -> {page}#{anchor} (no such section)")
    assert not missing, "docs point at sections that do not exist:\n  " + \
        "\n  ".join(missing)
    assert refs, "no doc sends a participant to the site at all"
    print(f"ok test_every_link_the_docs_send_a_participant_to_exists "
          f"({len(refs)} site anchors, all present)")


def test_the_publish_gate_covers_every_field_a_page_reads_unguarded():
    """`refresh.assert_site_contract` and the pages have to agree.

    The gate exists because the live `site/data.json` is rebuilt every six
    hours and never passes through these tests -- a hole would reach a
    participant as the word "undefined" before it reached CI. That only works
    while the gate knows about every field a page reads without checking
    first, and a page can gain one at any time.

    A read counts as guarded when it is followed by `&&`, `||`, `?`, or sits
    inside `typeof`; anything else is unguarded and belongs in the contract.
    """
    import re
    from ssa import refresh

    covered = set(refresh.SITE_ROUND_FIELDS) | set(refresh.SITE_ROUND_TYPES)
    # `r` is also the loop variable for things that are not rounds -- chart
    # points, DOM rects, entrant rows. Only names a round actually has can be
    # a contract violation.
    known = covered | {"series", "tracker", "resolve", "scoreable",
                       "release_estimated", "history_source"}

    unguarded = {}
    for page in ("index.html", "leaderboard.html"):
        with open(os.path.join(ROOT, "site", page)) as fh:
            body = fh.read()
        # A comparison cannot render, so `r.x === y` is safe whatever `x` is;
        # `&&`, `||` and `?` are the ordinary guards; `)` ends an argument
        # list that is usually a guard of its own.
        for m in re.finditer(
                r"\br\.([a-z_]+)\b(\s*(?:===|!==|==|!=|&&|\|\||\?|\)))?",
                body):
            field, guard = m.group(1), m.group(2)
            if field not in known or guard:
                continue
            start = max(0, m.start() - 12)
            if "typeof" in body[start:m.start()]:
                continue
            if field not in covered:
                unguarded.setdefault(field, page)

    assert not unguarded, (
        "a page reads these round fields without checking, and the publish "
        "gate does not require them:\n  "
        + "\n  ".join(f"{k} (in {v})" for k, v in sorted(unguarded.items()))
        + "\nAdd them to refresh.SITE_ROUND_FIELDS, or guard the read.")
    print(f"ok test_the_publish_gate_covers_every_field_a_page_reads_unguarded "
          f"({len(refresh.SITE_ROUND_FIELDS)} required, "
          f"{len(refresh.SITE_ROUND_TYPES)} type-checked)")


if __name__ == "__main__":
    test_every_leaderboard_tab_renders_something_a_participant_can_read()
    test_the_landing_page_names_each_round_shape_and_the_right_deadline()
    test_agent_results_are_visible_without_getting_an_official_rank()
    test_resizable_panels_and_chart_widths()
    test_model_filter_selection_and_persistence()
    test_a_batch_is_open_until_its_last_question_closes()
    test_the_page_reads_only_keys_the_pipeline_publishes()
    test_no_page_promises_a_date_it_cannot_know()
    test_every_link_the_docs_send_a_participant_to_exists()
    test_the_publish_gate_covers_every_field_a_page_reads_unguarded()
    print("10 passed")
