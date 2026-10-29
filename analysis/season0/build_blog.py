#!/usr/bin/env python3
"""Render site/blog/season-0.md into site/blog.html.

    python analysis/season0/build_blog.py

Replaces everything between <!-- blog:start --> and <!-- blog:end --> in
site/blog.html: the left nav, the article and the "On this page" list. The
page's styles, charts and scripts outside the markers stay untouched.
The Markdown syntax is described at the top of season-0.md.
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "site" / "blog" / "season-0.md"
PAGE = ROOT / "site" / "blog.html"
START, END = "<!-- blog:start -->", "<!-- blog:end -->"

LEGEND = {
    "model": '<div class="legend"><span><i style="background:var(--model)"></i>Model</span><span><i style="background:var(--base)"></i>Persistence and Crowd, for reference</span><span><i style="background:var(--model);opacity:.6"></i>Lighter: skipped some questions</span></div>',
    "full": '<div class="legend"><span><i style="background:var(--agent)"></i>Agent</span><span><i style="background:var(--model)"></i>Model</span><span><i style="background:var(--base)"></i>Baseline</span><span><i style="background:var(--agent);opacity:.6"></i>Lighter: skipped some questions</span></div>',
}
SCATTER_CONTROLS = ('<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px"><div class="seg" id="scatter-x">'
                    '<button data-x="aa_intelligence" class="on">AA Intelligence</button><button data-x="or_tokens_7d">OpenRouter tokens</button>'
                    '<button data-x="cutoff">Training cutoff</button></div><div class="seg" id="scatter-w">'
                    '<button data-w="preseason" class="on">Pre-season · 12 models</button><button data-w="s0">Season 0 · 6 models</button></div></div>')

# Each panel: (opening div, header sub-title html, body html). {sub} is the editable subtitle.
PANELS = {
    "toy": ('<div class="panel" id="toy-panel">', '<span class="panel-sub">{sub}</span>',
            '<div class="toy"><label class="truth" for="truth">True result: <b id="truth-val">44.0</b></label>'
            '<input id="truth" type="range" min="37" max="49" step="0.1" value="44"></div>\n<table class="t" id="toy-table"></table>'),
    "model-board": ('<div class="panel">', '<span class="panel-sub" data-caption></span>', LEGEND["model"] + '\n<div id="model-board"></div>'),
    "scatter": ('<div class="panel">', '<span class="panel-sub" id="scatter-rho"></span>', SCATTER_CONTROLS + '\n<div id="scatter"></div>'),
    "full-board": ('<div class="panel">', '<span class="panel-sub" data-caption></span>', LEGEND["full"] + '\n<div id="full-board"></div>'),
    "trajectory": ('<div class="panel">', '<span class="panel-sub" data-caption></span>', '<div class="legend" id="traj-legend"></div><div id="trajectory"></div>'),
    "weekly": ('<div class="panel">', '<span class="panel-sub">{sub}</span>', '<div id="weekly"></div>'),
    "h2h": ('<div class="panel">', '<span class="panel-sub">{sub}</span>', '<div id="h2h"></div>'),
    "strip": ('<div class="panel">', '<span class="panel-sub">{sub}</span>', '<div id="strip"></div>'),
}
TABLES = {"harness", "group", "early"}
GLOSSARY: dict[str, str] = {}
NOTES: dict[str, str] = {}
NOTE_ORDER: list[str] = []


def prepare(md: str) -> str:
    """Pull the glossary and footnote definitions out before rendering."""
    global GLOSSARY, NOTES, NOTE_ORDER
    GLOSSARY, NOTES, NOTE_ORDER = {}, {}, []

    def take_glossary(m):
        for line in m.group(1).splitlines():
            line = line.strip().lstrip("-").strip()
            if "|" in line:
                key, value = line.split("|", 1)
                GLOSSARY[key.strip()] = value.strip()
        return ""

    md = re.sub(r"^::: glossary\s*\n(.*?)\n:::[ \t]*$", take_glossary, md, flags=re.S | re.M)

    def take_note(m):
        NOTES[m.group(1)] = m.group(2).strip()
        return ""

    return re.sub(r"^\[\^([A-Za-z0-9_-]+)\]:[ \t]*(.+)$", take_note, md, flags=re.M)


def inline(text: str, terms=True) -> str:
    t = html.escape(text, quote=False)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", t)

    def link(m):
        label, url = m.group(1), m.group(2)
        ext = ' target="_blank" rel="noopener"' if url.startswith("http") else ""
        return f'<a href="{url}"{ext}>{label}</a>'

    t = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, t)

    def footnote(m):
        key = m.group(1)
        if key not in NOTES:
            return m.group(0)
        if key not in NOTE_ORDER:
            NOTE_ORDER.append(key)
        n = NOTE_ORDER.index(key) + 1
        return f'<a class="fn" href="#fn-{key}" id="fnref-{key}">{n}</a>'

    t = re.sub(r"\[\^([A-Za-z0-9_-]+)\]", footnote, t)
    if not terms:
        return t

    def term(m):
        key = m.group(1)
        hit = next((k for k in GLOSSARY if k.lower() == key.lower()), None)
        if hit is None:
            raise SystemExit(f"unknown hover term [[{key}]]; add it under ::: glossary")
        return (f'<span class="term" tabindex="0">{html.escape(key)}'
                f'<span class="def" role="tooltip">{inline(GLOSSARY[hit], terms=False)}</span></span>')

    return re.sub(r"\[\[([^\[\]|]+)\]\]", term, t)


def blocks(lines):
    """Split plain Markdown lines into paragraphs and lists."""
    out, para, items, kind = [], [], [], None

    def flush():
        nonlocal para, items, kind
        if para:
            out.append(("p", " ".join(para)))
        if items:
            out.append((kind, items))
        para, items, kind = [], [], None
    for raw in lines:
        line = raw.strip()
        m = re.match(r"^(-|\d+\.)\s+(.*)$", line)
        if not line:
            flush()
        elif m:
            k = "ul" if m.group(1) == "-" else "ol"
            if para or (kind and kind != k):
                flush()
            kind = k
            items.append(m.group(2))
        elif items:
            items[-1] += " " + line
        else:
            para.append(line)
    flush()
    return out


def render_plain(lines, pclass=None):
    html_out = []
    for kind, val in blocks(lines):
        if kind == "p":
            cls = f' class="{pclass}"' if pclass else ""
            html_out.append(f"<p{cls}>{inline(val)}</p>")
        else:
            html_out.append(f"<{kind}>\n" + "\n".join(f"<li>{inline(i)}</li>" for i in val) + f"\n</{kind}>")
    return "\n".join(html_out)


def fields(lines):
    d, key = {}, None
    for raw in lines:
        m = re.match(r"^(title|sub|note):\s*(.*)$", raw.strip())
        if m:
            key = m.group(1)
            d[key] = m.group(2)
        elif raw.strip() and key:
            d[key] += " " + raw.strip()
    return d


def directive(name, arg, body):
    if name == "lede":
        return render_plain(body, "lede")
    if name == "note":
        return render_plain(body, "note")
    if name == "callout":
        return f'<div class="callout"><b>{inline(arg)}</b>' + render_plain(body) + "</div>"
    if name == "cheer":
        return f'<div class="cheer"><span class="tag">{inline(arg)}</span>' + render_plain(body) + "</div>"
    if name == "steps":
        rows = []
        for i, item in enumerate(blocks(body)[0][1], 1):
            m = re.match(r"^\*\*(.+?)\*\*\s*(.*)$", item)
            head, rest = (m.group(1), m.group(2)) if m else (item, "")
            rows.append(f'<div class="step"><span class="num">{i}</span><div><b>{inline(head)}</b><p>{inline(rest)}</p></div></div>')
        return '<div class="steps">\n' + "\n".join(rows) + "\n</div>"
    if name == "cards":
        cards = []
        for item in blocks(body)[0][1]:
            tag, title, text = (s.strip() for s in item.split("|", 2))
            cards.append(f'<div class="card"><span class="tag">{inline(tag)}</span><h4>{inline(title)}</h4><p>{inline(text)}</p></div>')
        cls = "cards five" if arg == "five" else "cards"
        return f'<div class="{cls}">\n' + "\n".join(cards) + "\n</div>"
    if name == "panel":
        if arg not in PANELS:
            raise SystemExit(f"unknown panel {arg!r}; known: {', '.join(PANELS)}")
        f = fields(body)
        opening, sub, inner = PANELS[arg]
        title = inline(f.get("title", ""))
        data_title = f' data-title="{title}"' if "data-caption" in sub else ""
        head = (f'<div class="panel-head"><span class="panel-title"{data_title}>{title}</span>'
                + sub.format(sub=inline(f.get("sub", ""))) + "</div>")
        note = f'\n<p class="note">{inline(f["note"])}</p>' if f.get("note") else ""
        return f"{opening}{head}\n{inner}{note}</div>"
    if name == "flow":
        return render_flow(body)
    if name == "glossary":
        return ""
    raise SystemExit(f"unknown block ::: {name}")


def render_flow(body):
    """Two paths, models and agents, for one shared question."""
    sections, current, join = {}, None, ""
    for raw in body:
        line = raw.strip()
        head = re.match(r"^(question|models|agents):\s*$", line)
        joined = re.match(r"^join:\s*(.*)$", line)
        if head:
            current = head.group(1)
            sections[current] = []
        elif joined:
            current = None
            join = joined.group(1)
        elif line.startswith("- ") and current:
            sections[current].append(line[2:])
        elif current and sections[current]:
            sections[current][-1] += " " + line
        elif join:
            join += " " + line
    missing = [k for k in ("question", "models", "agents") if not sections.get(k)]
    if missing or not join:
        raise SystemExit("::: flow needs question:, models:, agents:, and a join: line")

    def items(key):
        return "".join(f"<li>{inline(x)}</li>" for x in sections[key])

    shared = "".join(f"<p>{inline(x)}</p>" for x in sections["question"])
    return (
        '<figure class="flow">'
        f'<div class="flow-q"><div class="flow-k">One question, shared</div>{shared}</div>'
        '<div class="flow-fork" aria-hidden="true"><span></span></div>'
        '<div class="flow-cols">'
        f'<div class="flow-col model"><div class="flow-k">Models</div><ol>{items("models")}</ol></div>'
        f'<div class="flow-col agent"><div class="flow-k">Agents</div><ol>{items("agents")}</ol></div>'
        '</div>'
        '<div class="flow-fork join" aria-hidden="true"><span></span></div>'
        f'<div class="flow-end">{inline(join)}</div>'
        '</figure>')


def render(md: str):
    meta = {}
    md = re.sub(r"<!--.*?-->", "", md, flags=re.S)
    m = re.match(r"^---\n(.*?)\n---\n", md, flags=re.S)
    if m:
        for line in m.group(1).splitlines():
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
        md = md[m.end():]
    md = prepare(md)
    parts, plain, sections = [], [], []
    lines = md.splitlines()
    i = 0

    def flush():
        if plain:
            parts.append(render_plain(plain))
            plain.clear()
    while i < len(lines):
        line = lines[i]
        if line.startswith(":::"):
            flush()
            name, _, arg = line[3:].strip().partition(" ")
            body = []
            i += 1
            while i < len(lines) and lines[i].strip() != ":::":
                body.append(lines[i])
                i += 1
            parts.append(directive(name, arg.strip(), body))
        elif re.match(r"^\[\[table:\s*(\w+)\]\]$", line.strip()):
            flush()
            t = re.match(r"^\[\[table:\s*(\w+)\]\]$", line.strip()).group(1)
            if t not in TABLES:
                raise SystemExit(f"unknown table {t!r}; known: {', '.join(sorted(TABLES))}")
            parts.append(f'<table class="t" id="{t}-table"></table>')
        elif line.startswith("## "):
            flush()
            hm = re.match(r"^##\s+(.*?)\s*\{#([\w-]+)\}\s*$", line)
            if not hm:
                raise SystemExit(f"a ## heading needs an id, like '## Models {{#models}}': {line!r}")
            sections.append((hm.group(2), hm.group(1)))
            parts.append(f'\n<h2 id="{hm.group(2)}">{inline(hm.group(1))}</h2>')
        elif line.startswith("### "):
            flush()
            parts.append(f"<h3>{inline(line[4:].strip())}</h3>")
        else:
            plain.append(line)
        i += 1
    flush()
    title = meta.get("title", "")
    nav = meta.get("nav") or title
    links = "".join(f'<a href="#{sid}">{html.escape(name)}</a>' for sid, name in sections)
    left = (f'<aside class="left" aria-label="Blog"><a class="back" href="index.html">← Back to arena</a>'
            f'<div class="navlabel">Posts</div><a class="on" href="blog.html">{html.escape(nav)}</a>'
            f'<div class="navlabel">Sections</div>{links}</aside>')
    byline = f'\n<div class="byline">{inline(meta["author"])}</div>' if meta.get("author") else ""
    head = f'<div class="eyebrow">{inline(meta.get("eyebrow", ""))}</div>\n<h1>{inline(title)}</h1>{byline}'
    notes = ""
    if NOTE_ORDER:
        rows = []
        for n, key in enumerate(NOTE_ORDER, 1):
            rows.append(f'<li id="fn-{key}">{inline(NOTES[key])} <a class="fnback" href="#fnref-{key}">{n}</a></li>')
        notes = '\n<ol class="footnotes">\n' + "\n".join(rows) + "\n</ol>"
    article = "<article>\n" + head + "\n" + "\n".join(parts).strip() + notes + "\n</article>"
    right = f'<aside class="right" aria-label="On this page"><div class="navlabel">On this page</div>{links}</aside>'
    return meta, f'<div class="layout">{left}\n{article}\n{right}'


def main():
    meta, body = render(SRC.read_text(encoding="utf-8"))
    page = PAGE.read_text(encoding="utf-8")
    if START not in page or END not in page:
        raise SystemExit(f"{PAGE} is missing the {START} / {END} markers")
    a, b = page.index(START) + len(START), page.index(END)
    page = page[:a] + "\n" + body + "\n" + page[b:]
    if meta.get("title"):
        page = re.sub(r"<title>.*?</title>", f"<title>{html.escape(meta['title'])} · Blog · Social Simulation Arena</title>", page, count=1)
    if meta.get("description"):
        page = re.sub(r'<meta name="description" content="[^"]*">',
                      f'<meta name="description" content="{html.escape(meta["description"])}">', page, count=1)
    PAGE.write_text(page, encoding="utf-8", newline="\n")
    print(f"wrote {PAGE.relative_to(ROOT)} from {SRC.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
