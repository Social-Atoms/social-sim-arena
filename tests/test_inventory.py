"""The source inventory: one table, audited, and the only place rights live."""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import inventory                                   # noqa: E402
from ssa.series import SERIES                               # noqa: E402

spec = importlib.util.spec_from_file_location(
    "_gen", os.path.join(ROOT, "tools", "generate_rounds.py"))
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)

STATES = {inventory.INTEGRATED, inventory.PERMISSION_NEEDED, inventory.REJECTED}
RIGHTS = {inventory.APPROVED, inventory.APPROVED_NO_REDISTRIBUTION,
          inventory.PERMISSION_NEEDED, inventory.REJECTED,
          inventory.UNRESOLVED}


def test_every_registered_source_is_audited():
    """A new adapter must not reach the registry without a rights decision.

    Without this the gate would refuse the new source as `unresolved`, which
    reads identically to "considered and refused" -- and nobody could tell the
    two apart until a season came up short.
    """
    missing = inventory.unaudited(m.get("source") for m in SERIES.values())
    assert not missing, f"registered but not in the inventory: {missing}"
    print("ok test_every_registered_source_is_audited")


def test_the_generator_reads_the_inventory_and_not_a_second_table():
    """One source of truth. The old copy in the generator had already drifted.

    `trends_basket` was absent from the hand-typed RIGHTS dict, so the gate
    called it `unresolved` while three hand-written Trends basket rounds ran
    live in the season file. This test is what makes a repeat impossible.
    """
    assert gen.RIGHTS == inventory.rights_table()
    assert inventory.INVENTORY["trends_basket"]["rights"] == inventory.APPROVED
    src = open(os.path.join(ROOT, "tools", "generate_rounds.py")).read()
    assert "RIGHTS = inventory.rights_table()" in src
    print("ok test_the_generator_reads_the_inventory_and_not_a_second_table")


def test_states_and_rights_are_from_the_declared_vocabularies():
    for key, row in sorted(inventory.INVENTORY.items()):
        assert row["state"] in STATES, (key, row["state"])
        assert row["rights"] in RIGHTS, (key, row["rights"])
        assert row["role"] in (inventory.TARGET, inventory.INPUT), key
    print("ok test_states_and_rights_are_from_the_declared_vocabularies")


def test_every_row_carries_evidence_and_every_rejection_carries_a_way_back():
    """A verdict without evidence cannot be audited, and a rejection without a
    revisit condition is a dead end. `docs/sources.md` §6 is a rejection that
    was reversed once somebody re-read its load-bearing argument; that reversal
    was worth more than the original verdict, and it is only possible when the
    verdict says what would change it."""
    for key, row in sorted(inventory.INVENTORY.items()):
        assert row.get("evidence"), f"{key} has no evidence"
        assert len(row["evidence"]) > 60, f"{key}'s evidence is a label, not evidence"
        if row["state"] != inventory.INTEGRATED:
            assert row.get("revisit"), f"{key} is not integrated and has no revisit"
    print("ok test_every_row_carries_evidence_and_every_rejection_carries_a_way_back")


def test_only_approved_sources_can_generate():
    """The gate reads rights, and nothing else opens the door.

    Checked against the inventory rather than against a literal list, so a row
    edited to `approved` without the evidence being updated still has to pass
    the test above.
    """
    hist = [{"date": f"2026-0{1 + i // 28}-{1 + i % 28:02d}", "value": 40.0 + i}
            for i in range(40)]
    for key, row in sorted(inventory.INVENTORY.items()):
        ok, why = gen.gate("x", {"source": key}, hist)
        if row["rights"] not in inventory.GENERATING_RIGHTS:
            assert not ok and why["gate"] == "rights", key
            assert why["state"] == row["rights"], key
        else:
            # Approved gets past the rights gate; it may still be refused
            # further down for history, volatility, template or schedule.
            assert ok or why["gate"] != "rights", (key, why)
    print("ok test_only_approved_sources_can_generate")


def test_a_conditional_approval_names_the_archive_it_must_not_publish():
    """`approved-no-redistribution` is a promise about what does not ship.

    A promise kept only in prose is one refactor from being broken silently, so
    the verdict has to point at the directory it is about. Both directions are
    checked: a row carrying the verdict without a blocklist entry, and a
    blocklist entry for a source that no longer carries the verdict -- the
    second is how a stale entry would come to protect nothing while looking
    like it protects something.
    """
    conditional = {k for k, r in inventory.INVENTORY.items()
                   if r["rights"] == inventory.APPROVED_NO_REDISTRIBUTION}
    listed = set(inventory.PUBLISH_BLOCKLIST)
    assert conditional == listed, (
        f"rows carrying the verdict but not on the blocklist: "
        f"{sorted(conditional - listed)}; blocklist entries whose row no "
        f"longer carries it: {sorted(listed - conditional)}")
    for source, path in sorted(inventory.PUBLISH_BLOCKLIST.items()):
        assert os.path.isdir(os.path.join(ROOT, path)), \
            f"{source}: blocklist names {path}, which does not exist"
    print("ok test_a_conditional_approval_names_the_archive_it_must_not_publish")


def test_the_blocked_archives_are_not_reachable_from_the_published_site():
    """`site/` is what Vercel serves, so anything under it is public.

    The three conditionally-approved sources are approved *because* their
    bodies stay unpublished. Copying one into `site/` -- or letting a build
    step do it -- would revoke the verdict without editing the row that grants
    it, so the check is on the served directory rather than on intent.
    """
    site = os.path.join(ROOT, "site")
    served = set()
    for base, _dirs, files in os.walk(site):
        for f in files:
            served.add(os.path.relpath(os.path.join(base, f), site))
    for source, path in sorted(inventory.PUBLISH_BLOCKLIST.items()):
        leaked = sorted(n for n in served if n.startswith(os.path.basename(path)))
        assert not leaked, f"{source}: {path} bodies are inside site/: {leaked}"
        bodies = os.listdir(os.path.join(ROOT, path))
        for b in bodies:
            assert b not in served, \
                f"{source}: {b} from {path} is served by site/"
    print("ok test_the_blocked_archives_are_not_reachable_from_the_published_site")


def test_inputs_are_never_targets():
    """The news corpus and the search index are what an entrant reads, not what
    it is asked about. Registering one as a series would score a model on a
    number the arena itself chose to fetch."""
    inputs = {k for k, v in inventory.INVENTORY.items()
              if v["role"] == inventory.INPUT}
    used = {m.get("source") for m in SERIES.values()}
    assert not (inputs & used), f"input sources registered as targets: {inputs & used}"
    print("ok test_inputs_are_never_targets")


if __name__ == "__main__":
    test_every_registered_source_is_audited()
    test_the_generator_reads_the_inventory_and_not_a_second_table()
    test_states_and_rights_are_from_the_declared_vocabularies()
    test_every_row_carries_evidence_and_every_rejection_carries_a_way_back()
    test_only_approved_sources_can_generate()
    test_a_conditional_approval_names_the_archive_it_must_not_publish()
    test_the_blocked_archives_are_not_reachable_from_the_published_site()
    test_inputs_are_never_targets()
    print("8 passed")
