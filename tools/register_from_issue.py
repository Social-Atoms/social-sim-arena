"""Turn a "Register or update an entrant" issue into entrants/<id>.json.

Run by `.github/workflows/register.yml`. The issue is a GitHub issue form
(`.github/ISSUE_TEMPLATE/register.yml`), so its body is a fixed sequence of
`### <Label>` headings, each followed by the value typed into that field. This
reads those values and writes the registration file for
`tools/validate_submission.py` to judge.

Three cases, decided by whether `entrants/<id>.json` already exists on main:

  - absent: a new registration, `"github"` set to the issue's author (the one
    thing a participant cannot type: ownership is decided by who opened the
    issue, not by what it says);
  - present: an update; the form's fields replace the file's (an empty
    optional field removes it), everything else in the file (`github`,
    `method`, `arms`, `homepage`, ...) is kept, and the validator refuses the
    change unless the issue's author is the recorded owner;
  - present and "Retire" ticked: `status` becomes `retired` with `retired_at`
    set to the issue's time; the file stays so the board keeps its history.

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
RETIRE_LABEL = "Retire"
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


def registration(issue, existing=None):
    form = parse_form(issue.get("body"))
    retire = "[x]" in form.get(RETIRE_LABEL, "").lower()
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
    if existing:
        doc = dict(existing)
        doc.update(name=reg["name"], organization=reg["organization"])
        doc.pop("contact", None)
        doc["route"] = dict(existing.get("route") or {}, kind="agent_api", url=url)
    else:
        if retire:
            die("nothing to retire: no registration under this id")
        doc = {"entrant_id": entrant_id, "name": reg["name"],
               "organization": reg["organization"], "type": "participant",
               "github": login}
        doc["route"] = {"kind": "agent_api", "url": url}
    if reg.get("contact"):
        doc["contact"] = reg["contact"]
    if retire:
        doc["status"] = "retired"
        doc["retired_at"] = issue.get("created_at")
    return doc


def die(msg):
    print(f"::error::{msg}")
    sys.exit(2)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        die("usage: python tools/register_from_issue.py issue.json")
    with open(sys.argv[1], encoding="utf-8") as f:
        issue = json.load(f)
    form = parse_form(issue.get("body"))
    entrant_id = form.get("Entrant id", "").strip()
    rel = os.path.join("entrants", entrant_id + ".json")
    existing = None
    if ID_RE.match(entrant_id) and os.path.exists(os.path.join(ROOT, rel)):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
            existing = json.load(f)
    doc = registration(issue, existing)
    with open(os.path.join(ROOT, rel), "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    print(rel)
