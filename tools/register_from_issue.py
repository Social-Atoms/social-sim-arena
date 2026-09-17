"""Turn a "Register an entrant" issue into entrants/<id>.json.

Run by `.github/workflows/register.yml`. The issue is a GitHub issue form
(`.github/ISSUE_TEMPLATE/register.yml`), so its body is a fixed sequence of
`### <Label>` headings, each followed by the value typed into that field. This
reads those values, sets `"github"` to the issue's author (the one thing a
participant cannot type, because ownership is decided by who opened the
issue, not by what it says), and writes the registration file for
`tools/validate_submission.py` to judge.

    python tools/register_from_issue.py issue.json

`issue.json` is the issue as returned by the GitHub API (`user.login`, `body`,
`created_at`). Prints the path it wrote. Exits 2 with a message when a
required field is missing or the entrant id is malformed, so the workflow can
quote the message back on the issue.
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Issue-form label -> registration field. Labels must match register.yml.
FIELDS = {
    "Entrant id": "entrant_id",
    "Display name": "name",
    "Company / organization": "organization",
    "Endpoint URL": "url",
    "Contact email (optional)": "contact",
}
EMPTY = {"", "_No response_"}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,47}$")


def parse_form(body):
    """`### Label\n\nvalue` sections -> {label: value}. GitHub renders an empty
    optional field as `_No response_`; both count as absent."""
    out = {}
    for m in re.finditer(r"^### (.+?)\s*\n(.*?)(?=^### |\Z)", body or "", re.S | re.M):
        label, value = m.group(1).strip(), m.group(2).strip()
        out[label] = "" if value in EMPTY else value
    return out


def registration(issue):
    form = parse_form(issue.get("body"))
    login = (issue.get("user") or {}).get("login") or ""
    if not login:
        die("the issue has no author login")
    reg = {}
    for label, key in FIELDS.items():
        value = form.get(label, "").strip()
        if key != "contact" and not value:
            die(f"the field \"{label}\" is empty; edit the issue to fill it in")
        if value:
            reg[key] = value
    entrant_id = reg["entrant_id"]
    if not ID_RE.match(entrant_id):
        die(f"entrant id {entrant_id!r}: lower-case letters, digits, . _ -, "
            "2 to 48 characters, starting with a letter or digit")
    url = reg.pop("url")
    doc = {"entrant_id": entrant_id, "name": reg["name"],
           "organization": reg["organization"], "type": "participant",
           "github": login}
    if reg.get("contact"):
        doc["contact"] = reg["contact"]
    doc["route"] = {"kind": "agent_api", "url": url}
    return doc


def die(msg):
    print(f"::error::{msg}")
    sys.exit(2)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        die("usage: python tools/register_from_issue.py issue.json")
    with open(sys.argv[1], encoding="utf-8") as f:
        issue = json.load(f)
    doc = registration(issue)
    rel = os.path.join("entrants", doc["entrant_id"] + ".json")
    with open(os.path.join(ROOT, rel), "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    print(rel)
