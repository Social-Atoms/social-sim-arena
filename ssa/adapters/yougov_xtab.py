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
tracker page uses. Treat it as fragile: archive every pull.
"""
import io
import zipfile
from xml.etree import ElementTree as ET

import requests

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
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


def parse(blob):
    """Workbook bytes -> [{date, question, cells: {label: {...}}}], oldest first.

    A wave is kept only when every scored cell reports it, so a profile is
    never scored with a hole in it.
    """
    z = zipfile.ZipFile(io.BytesIO(blob))
    book = ET.fromstring(z.read("xl/workbook.xml"))
    names = [s.get("name") for s in book.iter(NS + "sheet")]

    sheets, question, dates = {}, None, None
    for i, name in enumerate(names, start=1):
        rows = _cells(z.read(f"xl/worksheets/sheet{i}.xml"))
        header = next(iter(rows))              # row 1 label is the question
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
    return parse(fetch(tracker))


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
