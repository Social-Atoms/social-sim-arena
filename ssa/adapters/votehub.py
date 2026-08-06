"""VoteHub adapter.

Free JSON API, no key required. Poll-level records with pollster, field
dates, sample size, population, and answers.

  GET https://api.votehub.com/polls?poll_type=approval
  GET https://api.votehub.com/polls?poll_type=generic-ballot
"""
from datetime import date, datetime

import requests

BASE = "https://api.votehub.com/polls"


def fetch(poll_type, timeout=30):
    r = requests.get(BASE, params={"poll_type": poll_type}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _mid_date(poll):
    start = date.fromisoformat(poll["start_date"])
    end = date.fromisoformat(poll["end_date"])
    return start + (end - start) / 2


def _answer(poll, *names):
    for a in poll.get("answers", []):
        if a["choice"].lower() in names:
            return float(a["pct"])
    return None


def approval_polls(subject="Donald Trump"):
    """Trump approval polls, newest last: [{date, pollster, n, approve, disapprove}]."""
    out = []
    for p in fetch("approval"):
        if p.get("subject") != subject:
            continue
        approve = _answer(p, "approve")
        disapprove = _answer(p, "disapprove")
        if approve is None or disapprove is None:
            continue
        out.append({
            "date": _mid_date(p),
            "end_date": date.fromisoformat(p["end_date"]),
            "pollster": p.get("pollster") or "unknown",
            "sponsors": p.get("sponsors") or [],
            "n": p.get("sample_size") or 500,
            "population": p.get("population") or "a",
            "approve": approve,
            "disapprove": disapprove,
            "value": approve,
            "margin": approve - disapprove,
        })
    out.sort(key=lambda x: x["date"])
    return out


def generic_ballot_polls():
    """2026 generic ballot polls, newest last: [{date, pollster, n, dem, rep, margin}]."""
    out = []
    for p in fetch("generic-ballot"):
        dem = _answer(p, "dem", "democrat", "democrats")
        rep = _answer(p, "rep", "republican", "republicans")
        if dem is None or rep is None:
            continue
        out.append({
            "date": _mid_date(p),
            "end_date": date.fromisoformat(p["end_date"]),
            "pollster": p.get("pollster") or "unknown",
            "sponsors": p.get("sponsors") or [],
            "n": p.get("sample_size") or 500,
            "population": p.get("population") or "a",
            "dem": dem,
            "rep": rep,
            "value": dem - rep,
            "margin": dem - rep,
        })
    out.sort(key=lambda x: x["date"])
    return out


def from_pollster(polls, pollster_substr, sponsor_substr=None):
    """Polls whose pollster name contains the substring (case-insensitive),
    optionally also requiring a sponsor substring. Oldest first."""
    hits = []
    for p in polls:
        if pollster_substr.lower() not in p["pollster"].lower():
            continue
        if sponsor_substr is not None:
            sponsors = " ".join(p.get("sponsors", [])).lower()
            if sponsor_substr.lower() not in sponsors:
                continue
        hits.append(p)
    return hits
