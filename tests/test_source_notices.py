"""One standardized data-source notice per source (decision of 2026-11-15).

The notices live in ssa/source_notices.py and nowhere else; site/sources.json
is generated from them and the pages print that file. These tests keep the
three in step and make sure no live question goes out without its credit.
"""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import consumer_round, inventory, source_notices   # noqa: E402

PASSED = []


def ok(fn):
    PASSED.append(fn.__name__)
    print("ok", fn.__name__)


def test_every_notice_names_an_inventory_source_and_is_complete():
    for key, row in sorted(source_notices.NOTICES.items()):
        assert key in inventory.INVENTORY, f"{key}: notice for a source the inventory does not know"
        assert set(row) == {"name", "attribution", "disclaimer", "url", "details"}, key
        assert row["name"] and len(row["attribution"]) > 15, key
        assert row["url"].startswith("https://"), key
        assert row["disclaimer"] is None or len(row["disclaimer"]) > 40, key
        # A notice is a credit, never a grant: rejected or unresolved sources
        # carry none, so a notice cannot make a source look admitted.
        assert inventory.INVENTORY[key]["rights"] in inventory.GENERATING_RIGHTS, (
            key, inventory.INVENTORY[key]["rights"])
    ok(test_every_notice_names_an_inventory_source_and_is_complete)


def test_every_live_round_has_a_notice():
    """Every round in the season, past or future, resolves to a source with a
    notice, so every question page prints one."""
    rounds = json.load(open(os.path.join(ROOT, "questions", "season0.json")))["rounds"]
    sources = source_notices.series_sources()
    missing = sorted({r["series"] for r in rounds
                      if sources.get(r["series"]) not in source_notices.NOTICES})
    assert not missing, f"rounds whose source has no notice: {missing}"
    ok(test_every_live_round_has_a_notice)


def test_the_published_file_is_generated_from_the_table():
    got = subprocess.run([sys.executable, "tools/publish_source_notices.py", "--check"],
                         cwd=ROOT, capture_output=True, text=True)
    assert got.returncode == 0, got.stdout + got.stderr
    data = json.load(open(os.path.join(ROOT, "site", "sources.json")))
    assert data["general"] == source_notices.GENERAL_NOTICE
    assert {s["source"] for s in data["sources"]} == set(source_notices.NOTICES)
    ok(test_the_published_file_is_generated_from_the_table)


def test_the_general_notice_says_what_it_must():
    g = source_notices.GENERAL_NOTICE
    for phrase in ("not affiliated with", "endorsed", "derived by SSA from public sources",
                   "trademarks belong to their owners"):
        assert phrase in g, phrase
    ok(test_the_general_notice_says_what_it_must)


def test_consumer_credit_lines_come_from_the_one_table():
    for key, src in source_notices.CONSUMER_SOURCE.items():
        assert consumer_round.ATTRIBUTION[key] == source_notices.text(src), key
    assert consumer_round.ATTRIBUTION["sce"] == source_notices.FRBNY_SCE
    ok(test_consumer_credit_lines_come_from_the_one_table)


def test_the_pages_print_the_notice_from_the_file():
    index = open(os.path.join(ROOT, "site", "index.html")).read()
    docs = open(os.path.join(ROOT, "site", "docs.html")).read()
    assert "fetch('sources.json'" in index and "fetch('sources.json'" in docs
    # Both the question page and the forecast page end with the block.
    assert index.count("h+=sourceNoticeBlock(r);") == 2
    assert 'id="data-sources"' in docs and 'href="#data-sources"' in docs
    assert 'href="docs.html#data-sources"' in index
    # No page carries its own copy of a notice: the credit text is in the JSON only.
    for row in source_notices.NOTICES.values():
        for text in filter(None, (row["attribution"], row["disclaimer"])):
            assert text not in index and text not in docs, text[:60]
    assert not re.search(r"not affiliated with", index + docs)
    ok(test_the_pages_print_the_notice_from_the_file)


def test_the_question_page_renders_the_notice():
    """Run the page's own script under the DOM stub with a notices file set."""
    import shutil
    node = shutil.which("node")
    if not node:
        print("ok test_the_question_page_renders_the_notice (skipped: no node)")
        return
    script = r"""
const fs=require('fs'), path=require('path');
const {installGlobals}=require('./tests/site/dom_stub.js');
const html=fs.readFileSync('site/index.html','utf8');
const js=(html.match(/<script>([\s\S]*?)<\/script>/g)||[]).map(b=>b.replace(/^<script>/,'').replace(/<\/script>$/,'')).join('\n');
const {els}=installGlobals();
const data=JSON.parse(fs.readFileSync('site/data.json','utf8'));
eval(js);
render(data);
window.__notices=JSON.parse(fs.readFileSync('site/sources.json','utf8'));
const r=(data.rounds||[]).find(x=>window.__notices.series[x.series]);
renderQuestion(r.round_id);
const q=els['question-col'].innerHTML||'';
const src=window.__notices.series[r.series], row=window.__notices.sources.find(x=>x.source===src);
const esc=t=>String(t).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
if(!q.includes('Data source and terms')) throw new Error('no notice block on '+r.round_id);
if(!q.includes(esc(row.attribution).slice(0,40))) throw new Error('attribution missing on '+r.round_id);
if(!q.includes('docs.html#data-sources')) throw new Error('no link to the terms list');
console.log('notice rendered on '+r.round_id+' ('+src+')');
"""
    got = subprocess.run([node, "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert got.returncode == 0, got.stdout + got.stderr
    sys.stdout.write(got.stdout)
    ok(test_the_question_page_renders_the_notice)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"{len(PASSED)} passed")
