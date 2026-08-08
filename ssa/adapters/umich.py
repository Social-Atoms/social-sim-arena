"""Michigan Surveys of Consumers, from the survey's own published table.

FRED carries UMCSENT one month behind at Michigan's request, which is not a
labelling quirk -- it removes the most recent observation from every forecast
the arena makes. On 2026-08-08 FRED's newest point was June (49.5) while the
official table already carried July (55.2), a 5.7-point move that no entrant
could see. Forecasts for the August release were anchored on a level the series
had already left.

  GET http://www.sca.isr.umich.edu/files/tbmics.csv

Three columns, oldest first: Month, YYYY, ICS_ALL. The index of consumer
sentiment for all households, back to November 1952. Same series as UMCSENT,
published without the delay.

`ssa.adapters.fredcsv` stays as the fallback: this is a plain file on a
university web server, and the arena must not go dark if it moves.
"""
import csv
import io

import requests

URL = "http://www.sca.isr.umich.edu/files/tbmics.csv"

MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}


def parse(text):
    """CSV text -> [{date: 'YYYY-MM-01', value: float}], oldest first."""
    rows = []
    reader = csv.reader(io.StringIO(text))
    for row in reader:
        if len(row) < 3:
            continue
        month, year, value = (c.strip() for c in row[:3])
        if month not in MONTHS or not year.isdigit():
            continue          # header and any prose lines
        try:
            v = float(value)
        except ValueError:
            continue          # months with no reading yet
        rows.append({"date": f"{int(year):04d}-{MONTHS[month]:02d}-01",
                     "value": v})
    rows.sort(key=lambda r: r["date"])
    return rows


def umich_sentiment(timeout=30):
    r = requests.get(URL, timeout=timeout)
    r.raise_for_status()
    rows = parse(r.text)
    if not rows:
        raise RuntimeError(
            "Michigan table parsed to zero rows; refusing to publish an empty "
            "series (check whether the file moved)")
    return rows
