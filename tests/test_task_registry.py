"""registry/tasks.json is the one description of a task; these keep it honest."""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import task_registry  # noqa: E402


def site_tasks():
    """The site's chart-wiring table: id -> the cell constant it uses, if any."""
    html = open(os.path.join(ROOT, "site", "index.html")).read()
    block = re.search(r"^const TASKS = \{\n(.*?)^\};", html, re.S | re.M).group(1)
    rows = re.findall(r"^  ([a-z0-9_]+):\{(.*)$", block, re.M)
    consts = {}
    for name in ("CIVIQS_PROFILE_CELLS", "CIVIQS_SUBGROUP_CELLS", "YOUGOV_PROFILE_CELLS", "TREND_CELLS"):
        m = re.search(r"const " + name + r" = \[(.*?)\];", html, re.S)
        consts[name] = re.findall(r"'([a-z0-9_]+)'", m.group(1))
    out = {}
    for key, body in rows:
        if key == "agg":
            continue
        m = re.search(r"cells:(\[[^\]]*\]|[A-Z_]+)", body)
        cells = None
        if m:
            cells = consts[m.group(1)] if m.group(1) in consts else re.findall(r"'([a-z0-9_]+)'", m.group(1))
        out[key] = cells
    return out


def test_the_registry_validates_and_names_only_known_series():
    tasks = task_registry.load()
    problems = task_registry.check_series(tasks)
    assert not problems, "\n".join(problems)
    print(f"ok test_the_registry_validates_and_names_only_known_series ({len(tasks)} tasks)")


def test_the_site_and_the_registry_describe_the_same_tasks():
    tasks = {t["id"]: t for t in task_registry.load()}
    site = site_tasks()
    missing = sorted(set(site) - set(tasks))
    extra = sorted(set(tasks) - set(site))
    assert not missing and not extra, f"site tasks without a registry row: {missing}; registry rows without chart wiring: {extra}"
    for key, cells in site.items():
        if cells:
            assert cells == tasks[key].get("cells"), f"{key}: the site's cell order differs from the registry"
    print(f"ok test_the_site_and_the_registry_describe_the_same_tasks ({len(site)} tasks)")


def test_published_rows_carry_what_the_site_reads():
    rows = task_registry.published()
    needed = {"id", "label", "group", "population", "cadence", "question_type", "source", "status"}
    for row in rows:
        assert needed <= set(row), f"{row.get('id')}: missing {sorted(needed - set(row))}"
        assert {"name", "url"} <= set(row["source"])
    print(f"ok test_published_rows_carry_what_the_site_reads ({len(rows)} rows)")


def test_data_json_carries_the_registry_when_present():
    with open(os.path.join(ROOT, "site", "data.json")) as fh:
        data = json.load(fh)
    if "tasks" not in data:
        print("skip test_data_json_carries_the_registry_when_present (data.json predates the registry)")
        return
    assert data["tasks"] == task_registry.published(), "site/data.json tasks differ from registry/tasks.json; run tools/publish_tasks.py"
    print("ok test_data_json_carries_the_registry_when_present")


if __name__ == "__main__":
    test_the_registry_validates_and_names_only_known_series()
    test_the_site_and_the_registry_describe_the_same_tasks()
    test_published_rows_carry_what_the_site_reads()
    test_data_json_carries_the_registry_when_present()
    print("4 passed")
