"""FRED adapter for Michigan Surveys of Consumers series.

Uses the keyless fredgraph CSV endpoint, so no API key is needed:

  GET https://fred.stlouisfed.org/graph/fredgraph.csv?id=UMCSENT

Caveat: FRED carries these series with a one month delay at the source's
request. Release-day values come from the official site
(data.sca.isr.umich.edu); the release calendar is on sca.isr.umich.edu.
"""
import csv
import io

import requests

BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv"


def series(series_id, timeout=30):
    """Full monthly history: [{date: 'YYYY-MM-DD', value: float}], oldest first."""
    r = requests.get(BASE, params={"id": series_id}, timeout=timeout)
    r.raise_for_status()
    rows = []
    reader = csv.reader(io.StringIO(r.text))
    header = next(reader, None)
    if header is None:
        raise RuntimeError("FRED returned an empty body for series " + series_id)
    for row in reader:
        if len(row) < 2 or row[1] in (".", ""):
            continue
        rows.append({"date": row[0], "value": float(row[1])})
    return rows


def umich_sentiment():
    return series("UMCSENT")


def umich_inflation_expectations():
    return series("MICH")
