"""Economist/YouGov weekly tracker, one subgroup at a time.

The topline is the least interesting number YouGov publishes. A univariate
approval series can be forecast by extrapolating one curve, which is a claim
about a curve and not about a society. The subgroup breaks are the quantity
that silicon sampling actually claims to model: how approval decomposes across
party, age, race, gender and education in the same week.

YouGov serves all of it from one keyless endpoint as a workbook whose sheets
are the subgroups:

  GET https://api-test.yougov.com/public-data/v5/us/trackers/<tracker>/download/

Each sheet is six rows wide by one column per wave: the wave dates, Approve,
Disapprove, Not sure, the unweighted base and the weighted base. Values arrive
as fractions rounded to whole percentage points, which the arena carries in
points to match every other series.

Two things measured from 80 waves of this file decide how the arena uses it,
both recorded here because they are easy to get wrong:

- **The weekly wiggle is mostly measurement noise.** Every cell's first
  difference has autocorrelation near -0.5, the signature of a level plus
  independent noise rather than a moving level. Real weekly movement is 0.5 to
  1.5 points; the noise around it is 0.8 points on the topline and up to 4.8
  points on the smallest cell. Rounds on this tracker are therefore scored as
  profiles, and a cell's noise floor is published beside its score.

- **Base sizes differ by an order of magnitude.** White has a median base of
  842 and Other has 69. A cell that small cannot be forecast by anyone, so it
  is excluded by name rather than quietly carried at a noise floor no entrant
  could beat.

The host is literally api-test.yougov.com, which is also the host the public
tracker page uses. Treat it as fragile: every pull is archived as a dated
vintage under sources/yougov_xtab/, and `waves()` reads the newest vintage
rather than the network.

Why the network is opt-in (SSA_YOUGOV_FETCH): YouGov's public-data license
prohibits "bots, crawlers, or automated scripts to extract or copy the
Licensed Data" without written permission, and a permission request is with
their legal team. Until it is answered, a pull is a deliberate maintainer act
-- set SSA_YOUGOV_FETCH=1 or call `pull()` -- never a side effect of the
six-hourly refresh. The crosstab round aggregates a month of waves, so the
archive only needs refreshing in the weeks before its lock, not four times a
day.
"""
import io
import os
import posixpath
import zipfile
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import requests

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PACKAGE_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
WORKSHEET_REL = ("http://schemas.openxmlformats.org/officeDocument/2006/"
                 "relationships/worksheet")
URL = "https://api-test.yougov.com/public-data/v5/us/trackers/{tracker}/download/"
UA = "social-simulation-arena/1.0 (research benchmark; contact via repository)"

TOPLINE = "US Registered Voters"

# The cells the arena scores, grouped by the dimension they cut. Order is
# fixed: the profile vector is built from it, so scores stay comparable across
# waves and entrants.
DIMENSIONS = {
    "party": ["Democrat", "Independent", "Republican"],
    "age": ["Under 30", "30-44", "45-64", "65+"],
    "race": ["White", "Black", "Hispanic"],
    "gender": ["Male", "Female"],
    "education": ["HS or less", "Some college", "College grad", "Postgrad"],
}

# Cell -> why it is not scored. Kept explicit so the exclusion is auditable.
EXCLUDED = {
    "Other": ("median weighted base 69, weekly measurement noise 4.8 points "
              "against real weekly movement under 1 point"),
}

SCORED_CELLS = [c for cells in DIMENSIONS.values() for c in cells]

ROW_LABELS = {"Approve": "approve", "Disapprove": "disapprove",
              "Not sure": "not_sure", "Unweighted base": "unweighted_base",
              "Base": "base"}


def fetch(tracker="donald-trump-approval", timeout=60):
    """The workbook as bytes. Archive what this returns before parsing it."""
    r = requests.get(URL.format(tracker=tracker),
                     headers={"User-Agent": UA}, timeout=timeout)
    r.raise_for_status()
    if not r.content.startswith(b"PK"):
        raise ValueError("YouGov download did not return a workbook")
    return r.content


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARCHIVE = os.path.join(ROOT, "sources", "yougov_xtab")


def _utc_today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def archive_path(day):
    return os.path.join(ARCHIVE, f"{day}.xlsx")


def archive(body, day=None):
    """File one workbook under its capture day, write-once. Returns the path.

    An existing vintage for the day is left untouched -- not compared, not
    rewritten -- so nothing downstream of a morning capture can be changed by
    an afternoon one.
    """
    if not isinstance(body, (bytes, bytearray)):
        raise TypeError("archive takes the workbook body as bytes")
    day = day or _utc_today()
    path = archive_path(day)
    if os.path.exists(path):
        return path
    os.makedirs(ARCHIVE, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(bytes(body))
    os.replace(tmp, path)
    return path


def archived_days():
    if not os.path.isdir(ARCHIVE):
        return []
    return sorted(f[:-5] for f in os.listdir(ARCHIVE)
                  if f.endswith(".xlsx") and not f.startswith("."))


def newest_archived():
    days = archived_days()
    return archive_path(days[-1]) if days else None


def pull(tracker="donald-trump-approval"):
    """Fetch, archive (write-once for today), and parse -- a maintainer act."""
    body = fetch(tracker)
    archive(body)
    return parse(body)


def _cells(sheet_xml):
    """Sheet XML -> {row label: [values]}, values in column order."""
    out = {}
    for row in ET.fromstring(sheet_xml).iter(NS + "row"):
        values = []
        for c in row.iter(NS + "c"):
            if c.get("t") == "inlineStr":
                t = c.find(NS + "is/" + NS + "t")
                values.append(t.text if t is not None else "")
            else:
                v = c.find(NS + "v")
                values.append(v.text if v is not None else "")
        if values:
            out[values[0]] = values[1:]
    return out


def _sheet_paths(z, book):
    """Return ``[(sheet name, zip member)]`` from workbook relationships.

    OOXML does not promise that the first ``<sheet>`` is ``sheet1.xml``. The
    workbook names sheets through ``r:id`` and the companion relationships
    file maps that id to the real part. Pairing by ordinal silently attaches a
    subgroup's name to a different subgroup's values when Excel renumbers or
    reorders parts, so every mapping is explicit and guarded here.
    """
    try:
        rel_root = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    except KeyError as e:
        raise ValueError(
            "YouGov workbook has no xl/_rels/workbook.xml.rels; sheet names "
            "cannot be matched to worksheet data") from e

    relationships = {}
    for rel in rel_root.iter(PACKAGE_REL_NS + "Relationship"):
        rid = rel.get("Id")
        if not rid:
            raise ValueError("YouGov workbook has a relationship without an Id")
        if rid in relationships:
            raise ValueError(
                f"YouGov workbook repeats relationship id {rid!r}")
        relationships[rid] = rel

    out, names, targets = [], set(), set()
    for sheet in book.iter(NS + "sheet"):
        name = sheet.get("name")
        rid = sheet.get(REL_NS + "id")
        if not name or name in names:
            raise ValueError(
                f"YouGov workbook has a missing or repeated sheet name {name!r}")
        names.add(name)
        if not rid or rid not in relationships:
            raise ValueError(
                f"YouGov sheet {name!r} names missing relationship {rid!r}")
        rel = relationships[rid]
        if rel.get("Type") != WORKSHEET_REL:
            raise ValueError(
                f"YouGov sheet {name!r} relationship {rid!r} is not a worksheet")
        if rel.get("TargetMode") == "External":
            raise ValueError(
                f"YouGov sheet {name!r} points to an external worksheet")
        target = rel.get("Target") or ""
        if not target or "\\" in target:
            raise ValueError(
                f"YouGov sheet {name!r} has an invalid worksheet target {target!r}")
        if target.startswith("/"):
            path = posixpath.normpath(target.lstrip("/"))
        else:
            path = posixpath.normpath(posixpath.join("xl", target))
        if not path.startswith("xl/worksheets/") or path.endswith("/"):
            raise ValueError(
                f"YouGov sheet {name!r} target {target!r} escapes xl/worksheets")
        if path in targets:
            raise ValueError(
                f"YouGov worksheet part {path!r} is assigned to more than one sheet")
        if path not in z.namelist():
            raise ValueError(
                f"YouGov sheet {name!r} points to missing worksheet part {path!r}")
        targets.add(path)
        out.append((name, path))
    if not out:
        raise ValueError("YouGov workbook declares no sheets")
    return out


def parse(blob):
    """Workbook bytes -> [{date, question, cells: {label: {...}}}], oldest first.

    A wave is kept only when every scored cell reports it, so a profile is
    never scored with a hole in it.
    """
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        book = ET.fromstring(z.read("xl/workbook.xml"))
        mapped = _sheet_paths(z, book)

        sheets, question, dates = {}, None, None
        for name, path in mapped:
            rows = _cells(z.read(path))
            if not rows:
                raise ValueError(f"YouGov sheet {name!r} contains no rows")
            header = next(iter(rows))          # row 1 label is the question
            if question is None:
                question, dates = header, rows[header]
            sheets[name] = {ROW_LABELS[k]: v for k, v in rows.items()
                            if k in ROW_LABELS}

    waves = []
    for j, date in enumerate(dates):
        if not date:
            continue
        cells, complete = {}, True
        for name in [TOPLINE] + SCORED_CELLS:
            rows = sheets.get(name)
            if rows is None:
                complete = name != TOPLINE and complete
                continue
            try:
                cell = {
                    "approve": round(float(rows["approve"][j]) * 100, 4),
                    "disapprove": round(float(rows["disapprove"][j]) * 100, 4),
                    "not_sure": round(float(rows["not_sure"][j]) * 100, 4),
                    "base": float(rows["base"][j]),
                    "unweighted_base": float(rows["unweighted_base"][j]),
                }
            except (IndexError, ValueError, TypeError):
                complete = False
                break
            cells[name] = cell
        if complete and len(cells) == len(SCORED_CELLS) + 1:
            waves.append({"date": date, "question": question, "cells": cells})
    waves.sort(key=lambda w: w["date"])
    return waves


def waves(tracker="donald-trump-approval"):
    """Parsed waves from the newest archived vintage.

    Fetches only when SSA_YOUGOV_FETCH is set and today has no vintage yet;
    otherwise the committed archive is the source of truth (see the module
    docstring for why the network is opt-in).
    """
    if os.environ.get("SSA_YOUGOV_FETCH") and not os.path.exists(
            archive_path(_utc_today())):
        return pull(tracker)
    newest = newest_archived()
    if newest is None:
        raise RuntimeError(
            "no YouGov crosstab vintage under sources/yougov_xtab/ and live "
            "fetching is off; a maintainer bootstraps the archive with "
            "yougov_xtab.pull() or by setting SSA_YOUGOV_FETCH=1 -- see the "
            "module docstring for why this never happens on its own")
    with open(newest, "rb") as f:
        return parse(f.read())


def profile(wave, measure="approve", cells=None):
    """A wave -> the ordered vector the arena scores.

    Fixed order, so entrant submissions, resolutions and every stored score
    line up without carrying labels through the scoring code.
    """
    names = cells if cells is not None else SCORED_CELLS
    return [wave["cells"][n][measure] for n in names]


def bases(wave, cells=None):
    """Weighted base per scored cell, in profile order. The noise floor a
    round publishes is a function of these, so they resolve with the value."""
    names = cells if cells is not None else SCORED_CELLS
    return [wave["cells"][n]["base"] for n in names]
