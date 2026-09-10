"""The task registry: registry/tasks.json, the one description of a task the site shows.

A task groups one or more series into the thing a visitor forecasts: a header
(population, cadence, question type), a source, and the rule that says which
rounds belong to it. refresh.py publishes the rows as data.tasks and the site
reads them, so the wording cannot drift from the pipeline. Add a source by
adding a row here, not by editing site/index.html.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "registry", "tasks.json")
SCHEMA = os.path.join(ROOT, "schema", "tasks.schema.json")
SEASON = os.path.join(ROOT, "questions", "season0.json")

PUBLISHED_FIELDS = ("id", "label", "group", "domain", "population", "cadence",
                    "question_type", "unit", "status")


def load(path=PATH):
    with open(path) as fh:
        doc = json.load(fh)
    validate(doc)
    return doc["tasks"]


def validate(doc):
    import jsonschema
    with open(SCHEMA) as fh:
        jsonschema.validate(doc, json.load(fh))
    ids = [t["id"] for t in doc["tasks"]]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise ValueError(f"duplicate task ids: {dup}")
    for t in doc["tasks"]:
        if t["question_type"] == "profile" and not t.get("cells"):
            raise ValueError(f"{t['id']}: a profile task lists its cells")


def known_series():
    """Every series id the pipeline knows: the series registry, the special
    sources, and whatever a season round names."""
    from . import season
    from . import series as series_registry
    ids = set(series_registry.SERIES) | set(getattr(season, "SPECIAL_SERIES_SOURCE", {}))
    with open(SEASON) as fh:
        ids |= {r["series"] for r in json.load(fh).get("rounds", []) if r.get("series")}
    return ids


def check_series(tasks, ids=None):
    """A task may only name series the pipeline knows. Returns the problems, empty when clean."""
    ids = known_series() if ids is None else ids
    problems = []
    for t in tasks:
        m = t["match"]
        for s in m.get("series", []):
            if s not in ids:
                problems.append(f"{t['id']}: series {s} is not registered")
        if "series_prefix" in m and not any(s.startswith(m["series_prefix"]) for s in ids):
            problems.append(f"{t['id']}: no series starts with {m['series_prefix']}")
        for c in t.get("cells", []):
            if c not in ids:
                problems.append(f"{t['id']}: cell {c} is not a registered series")
    return problems


def published(tasks=None):
    """The rows data.json carries: what the site shows, in registry order."""
    tasks = load() if tasks is None else tasks
    rows = []
    for t in tasks:
        row = {k: t[k] for k in PUBLISHED_FIELDS}
        row["source"] = dict(t["source"])
        row["match"] = dict(t["match"])
        if t.get("cells"):
            row["cells"] = list(t["cells"])
        rows.append(row)
    return rows


def publish_or_raise():
    """For refresh: load, cross-check, and return the rows; a bad registry does not publish."""
    tasks = load()
    problems = check_series(tasks)
    if problems:
        raise RuntimeError("registry/tasks.json names series the pipeline does not know:\n  "
                           + "\n  ".join(problems))
    return published(tasks)
