"""When Morning Consult published, read out of Silver Bulletin's `url` column.

No network: every test injects a fake session. The one thing this module does
that cannot be faked -- that the asset host refuses a request without browser
headers -- is recorded in the module's comment, with the measurement.
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa.adapters import mc_release  # noqa: E402


class Fake:
    """A session whose `head` answers from a script, and counts the asks."""

    def __init__(self, answers):
        self.answers = answers
        self.asked = []

    def head(self, url, **kw):
        self.asked.append(url)
        status, headers = self.answers.get(url, (404, {}))

        class R:
            status_code = status

        R.headers = headers
        return R


DRIVE = "https://drive.google.com/file/d/1mz53nbxVmkDaSt2l6gfcGff3hiOikKPJ/view"
DRIVE_DL = ("https://drive.google.com/uc?export=download&"
            "id=1mz53nbxVmkDaSt2l6gfcGff3hiOikKPJ")
WEEKLY = ("https://pro-assets.morningconsult.com/wp-uploads/2026/07/"
          "MCPI-PI-Weekly_260727.html")
LANDING = ("https://pro.morningconsult.com/trackers/"
           "donald-trump-congress-policy-republicans-polling")


def rows(*pairs):
    return [{"pollster": p, "url": u, "enddate": d, "subgroup": "All polls"}
            for p, u, d in pairs]


def test_the_sheet_names_where_each_wave_was_published():
    """The column was there all along. Waves are grouped by the artifact they
    came from, so one weekly deck cited by two waves is one observation."""
    got = mc_release.urls_for(rows(
        ("Morning Consult", DRIVE, "8/24/2026"),
        ("Morning Consult", DRIVE, "8/30/2026"),
        ("Morning Consult", WEEKLY, "7/26/2026"),
        ("YouGov", WEEKLY, "8/30/2026")))          # another pollster, ignored
    assert got == {DRIVE: ["8/24/2026", "8/30/2026"], WEEKLY: ["7/26/2026"]}
    print("ok test_the_sheet_names_where_each_wave_was_published")


def test_only_a_dated_artifact_dates_a_wave():
    """`MCPI-PI-Weekly.html` and the tracker page are real URLs with real
    modification times that describe no particular wave -- the landing page is
    cited by 63 waves at once. A publication time is only evidence when the
    thing it dates says which week it is."""
    assert mc_release.wave_label("20260831_US_MorningConsult.pdf") == "2026-08-31"
    assert mc_release.wave_label(None, WEEKLY) == "2026-07-27"
    assert mc_release.wave_label(None, LANDING) is None
    assert mc_release.wave_label("MCPI-PI-Weekly.html") is None
    assert mc_release.wave_label("20261399_US_MorningConsult.pdf") is None
    print("ok test_only_a_dated_artifact_dates_a_wave")


def test_a_drive_link_is_asked_at_the_endpoint_that_answers():
    """The `/view` page is an HTML shell with no filename and no modification
    time; the download endpoint carries both."""
    fake = Fake({DRIVE_DL: (200, {
        "Content-Disposition": 'attachment; filename="20260831_US_MorningConsult.pdf"',
        "Last-Modified": "Mon, 31 Aug 2026 20:11:19 GMT"})})
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "observed.json")
        ledger, added = mc_release.update(
            rows(("Morning Consult", DRIVE, "8/30/2026")),
            path=path, session=fake, pause=0)
    assert fake.asked == [DRIVE_DL], fake.asked
    assert added[0]["wave_label"] == "2026-08-31"
    assert added[0]["published_at"] == "Mon, 31 Aug 2026 20:11:19 GMT"
    assert added[0]["waves"] == ["8/30/2026"]
    print("ok test_a_drive_link_is_asked_at_the_endpoint_that_answers")


def test_an_answered_url_is_never_asked_twice_and_a_failed_one_always_is():
    """Asking again would replace a wave's publication time with a later
    edit's. Not asking again after a 403 would lose it entirely: the CDN
    refuses a burst and these files are overwritten in place, so a failure
    that is never retried is a week with no recorded publication."""
    ok = (200, {"Content-Disposition": 'attachment; filename="20260831_x.pdf"',
                "Last-Modified": "Mon, 31 Aug 2026 20:11:19 GMT"})
    fake = Fake({DRIVE_DL: ok, WEEKLY: (403, {})})
    sheet = rows(("Morning Consult", DRIVE, "8/30/2026"),
                 ("Morning Consult", WEEKLY, "7/26/2026"))
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "observed.json")
        mc_release.update(sheet, path=path, session=fake, pause=0)
        assert sorted(fake.asked) == sorted([DRIVE_DL, WEEKLY])
        fake.asked.clear()
        fake.answers[WEEKLY] = (200, {
            "Last-Modified": "Mon, 27 Jul 2026 18:59:10 GMT"})
        ledger, added = mc_release.update(sheet, path=path, session=fake, pause=0)
    assert fake.asked == [WEEKLY], f"the 403 was not retried: {fake.asked}"
    assert len(ledger) == 2 and len(added) == 1
    assert {r["url"] for r in ledger} == {DRIVE, WEEKLY}
    print("ok test_an_answered_url_is_never_asked_twice_and_a_failed_one_always_is")


def test_the_schedule_summary_counts_only_dated_observations():
    ledger = [
        {"wave_label": "2026-08-31", "published_at": "Mon, 31 Aug 2026 20:11:19 GMT"},
        {"wave_label": "2026-07-14", "published_at": "Tue, 14 Jul 2026 12:36:11 GMT"},
        {"wave_label": None, "published_at": "Fri, 04 Sep 2026 09:00:00 GMT"},
        {"wave_label": "2026-07-06", "published_at": None},
    ]
    got = mc_release.schedule(ledger)
    assert got["n"] == 2, got
    assert got["weekdays"] == {0: 1, 1: 1}, got      # Monday and Tuesday
    assert got["hours_utc"] == [12, 20], got
    print("ok test_the_schedule_summary_counts_only_dated_observations")


def test_the_committed_ledger_is_readable_and_says_what_it_saw():
    """The file this writes is committed, so it is worth one assertion that it
    parses and that every row records the ask."""
    path = os.path.join(ROOT, "sources", "mc_release", "observed.json")
    if not os.path.exists(path):
        print("ok test_the_committed_ledger_is_readable_and_says_what_it_saw "
              "(nothing recorded yet)")
        return
    with open(path) as fh:
        ledger = json.load(fh)
    assert isinstance(ledger, list) and ledger
    for row in ledger:
        assert row.get("url") and row.get("first_seen_at"), row
        if row.get("published_at"):
            assert row.get("http_status") == 200, row
    dated = [r for r in ledger if r.get("wave_label") and r.get("published_at")]
    print(f"ok test_the_committed_ledger_is_readable_and_says_what_it_saw "
          f"({len(ledger)} urls, {len(dated)} dated publications)")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"{len(tests)} passed")
