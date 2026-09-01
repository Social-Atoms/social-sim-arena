"""One row per upstream source: what state it is in, and the evidence for it.

This is the bounded backlog. Every source this repository has an adapter for,
every source `ssa/series.py` registers, and every source that was surveyed and
turned down in `docs/sources.md` or in the source issues gets exactly one row
here, carrying `state` and the evidence that put it there.

**Why it exists as code rather than as prose.** The rights decision was already
written down twice -- once in `docs/sources.md` as narrative, once in
`tools/generate_rounds.py` as a `RIGHTS` dict the gate reads -- and the two
could disagree without anything failing. A generator that refuses a source the
docs call approved is a silent gap in the season; a generator that *accepts* a
source the docs call rejected is a licence problem. So the dict moved here, the
generator imports `rights_table()`, and the prose points at this file. One
table, one place to change it.

**`state` and `rights` are different questions and both are needed.**

- `rights` is about the publisher's terms: may we retrieve this the way we do,
  and may we publish values derived from it. This is the field the generation
  gate reads, and only `approved` generates anything. Anything absent from this
  file is `unresolved`, so adding an adapter cannot quietly add rounds.
- `state` is about this repository: `integrated` (it feeds series or prompts
  today), `permission-needed` (we want it, the terms do not let us take it yet),
  or `rejected` (surveyed and turned down, with the reason).

A source can be rights-`approved` and still not `integrated` -- approved says
nothing about whether anyone has written the template. And a source can be
`integrated` and rights `permission-needed`: several here are read from a
committed archive a maintainer fetched by hand, which is exactly why the
generator must not schedule new rounds on them.

**`rejected` is not permanent, and the file says so.** `docs/sources.md` §6 is
a rejection that was reversed once somebody re-read its load-bearing argument,
and the reversal was worth more than the original verdict. Every `rejected` row
carries `revisit`: the specific fact that would reopen it. A rejection without
one is a dead end nobody can audit.

**Evidence is quoted, not summarised.** "Terms bar copying" is not evidence;
the sentence from the terms, the robots rule, the HTTP status, or the issue
number is. The rows below cite `docs/sources.md`, the adapter module that
verified the behaviour, or the issue that recorded the survey.
"""

# `role`: what the source is *for*. A target is something the arena asks
# questions about; an input is something an entrant is allowed to read before
# answering. They are audited to the same standard and gated differently -- an
# input never becomes a round, so the volatility and schedule gates do not
# apply to it, and it must never be mistaken for one.
TARGET = "target"
INPUT = "input"

INTEGRATED = "integrated"
PERMISSION_NEEDED = "permission-needed"
REJECTED = "rejected"

APPROVED = "approved"
UNRESOLVED = "unresolved"

INVENTORY = {
    # --- integrated targets -------------------------------------------------
    "sb_approval": {
        "publisher": "Silver Bulletin",
        "adapter": "ssa/adapters/silverbulletin.py",
        "role": TARGET,
        "state": INTEGRATED,
        "rights": APPROVED,
        "evidence": (
            "Published CSV export of the author's own poll database, already "
            "fetched on every refresh for live rounds since season 0 opened. "
            "docs/sources.md §5 records the concentration risk rather than a "
            "rights risk: most registered series arrive through this one pipe, "
            "so prefer a source with its own path over an 18th series here."),
    },
    "sb_generic": {
        "publisher": "Silver Bulletin",
        "adapter": "ssa/adapters/silverbulletin.py",
        "role": TARGET,
        "state": INTEGRATED,
        "rights": APPROVED,
        "evidence": (
            "The generic-ballot half of the same published sheet, fetched by "
            "the same adapter under the same terms as `sb_approval`, and "
            "carrying the same docs/sources.md §5 concentration risk: it is "
            "not an independent path, it is three more series behind one pipe."),
    },
    "civiqs": {
        "publisher": "Civiqs",
        "adapter": "ssa/adapters/civiqs.py",
        "role": TARGET,
        "state": INTEGRATED,
        "rights": APPROVED,
        "evidence": (
            "Terms permit download and copy with the publisher's notices kept, "
            "which the adapter does. Availability, not rights, is the live "
            "constraint: issue #35 established that Civiqs answers 403 to "
            "GitHub Actions datacenter IPs and no header fixes it, so every "
            "point is built from the dated snapshots under `civiqs/` rather "
            "than from the live page."),
    },
    "certified_election_results": {
        "publisher": "US state election authorities",
        "adapter": None,
        "role": TARGET,
        "state": INTEGRATED,
        "rights": APPROVED,
        "evidence": (
            "The Season 0 House-seat special resolves on official certified "
            "results published by state election authorities. Those public "
            "records are the final authority named in the reviewed round; AP "
            "calls are explicitly provisional and never replace certification."),
    },
    "umich": {
        "publisher": "University of Michigan Surveys of Consumers",
        "adapter": "ssa/adapters/umich.py",
        "role": TARGET,
        "state": INTEGRATED,
        "rights": APPROVED,
        "evidence": (
            "The national tables are published as CSV on the university's own "
            "site (`www.sca.isr.umich.edu/files/tbmics.csv`). "
            "`series.MICHIGAN_SOURCE` records which of two sources actually "
            "answered, so the site credits the one that replied."),
    },
    "sce": {
        "publisher": "Federal Reserve Bank of New York",
        "adapter": "ssa/adapters/sce.py",
        "role": TARGET,
        "state": INTEGRATED,
        "rights": APPROVED,
        "evidence": (
            "The published workbook carries its own licence sheet granting a "
            "worldwide, royalty-free right to reproduce and distribute the "
            "data (quoted in `ssa/series.py`, `_SCE_METHODOLOGY`). Release "
            "dates are preannounced on the NY Fed economic-indicators "
            "calendar, which is what §1.1 asks for."),
    },
    "wikipedia": {
        "publisher": "Wikimedia Foundation",
        "adapter": "ssa/adapters/wikipedia.py",
        "role": TARGET,
        "state": INTEGRATED,
        "rights": APPROVED,
        "evidence": (
            "Wikimedia's REST pageviews API is free and keyless. The one "
            "condition it enforces is a descriptive User-Agent -- a bare "
            "library UA is rejected outright -- which the adapter sends. A "
            "day's count is computed once from the request logs and never "
            "revised, so §1.2 is satisfied without an archive."),
    },
    "trends": {
        "publisher": "Google",
        "adapter": "ssa/adapters/trends.py",
        "role": TARGET,
        "state": INTEGRATED,
        "rights": APPROVED,
        "evidence": (
            "Public Trends web endpoints, no key, no paywall. The §1.2 "
            "objection -- relative values that change when the request window "
            "changes -- was answered rather than waived: the request window is "
            "a module constant, and every fetch is archived under `trends/`, "
            "so a completed week's value is whatever the earliest snapshot "
            "containing it showed, forever. Undocumented and unpromised "
            "endpoints, so `ssa/health.py` applies with extra force."),
    },
    "trends_basket": {
        "publisher": "Google",
        "adapter": "ssa/adapters/trends.py",
        "role": TARGET,
        "state": INTEGRATED,
        "rights": APPROVED,
        "evidence": (
            "The same adapter, the same endpoints and the same terms as "
            "`trends`; the separate key exists because a basket share is one "
            "comparison request covering five queries on a shared scale, not "
            "five single-keyword requests. It was missing from the old RIGHTS "
            "dict and therefore read as `unresolved` -- while three "
            "hand-written basket rounds ran live in `questions/season0.json`. "
            "That mismatch is the reason this file exists."),
    },
    "hhpoll": {
        "publisher": "Harvard-Harris Poll",
        "adapter": "ssa/adapters/hhpoll.py",
        "role": TARGET,
        "state": INTEGRATED,
        "rights": APPROVED,
        "evidence": (
            "No terms of use are published on harvardharrispoll.com. "
            "Retrieval is manual by design: the adapter reads topline PDFs a "
            "maintainer placed in the archive and nothing in the refresh "
            "fetches it unasked. Only three monthly observations exist so "
            "far, so the history gate refuses it and will keep refusing it "
            "until the series is twelve months long."),
    },

    # --- integrated inputs, never targets -----------------------------------
    "newsdigest": {
        "publisher": "Wikipedia (Portal:Current events)",
        "adapter": "ssa/adapters/newsdigest.py",
        "role": INPUT,
        "state": INTEGRATED,
        "rights": APPROVED,
        "evidence": (
            "CC BY-SA content, fetched by revision id as of the lock rather "
            "than as it reads today, and archived on first fetch. It is an "
            "*input* to the news arm's prompts, so it never becomes a round: "
            "issue #25 draws that line explicitly."),
    },
    "search": {
        "publisher": "Tavily",
        "adapter": "ssa/adapters/search.py",
        "role": INPUT,
        "state": INTEGRATED,
        "rights": APPROVED,
        "evidence": (
            "Keyed, paid API used as the arena's single search index so that "
            "the search arm compares models rather than vendors' private "
            "indexes. An input, never a target."),
    },
    "fredcsv": {
        "publisher": "Federal Reserve Bank of St. Louis",
        "adapter": "ssa/adapters/fredcsv.py",
        "role": INPUT,
        "state": INTEGRATED,
        "rights": APPROVED,
        "evidence": (
            "Keyless CSV endpoint, public domain. Registered as a redundancy "
            "cross-check only, never as a fallback: FRED republishes Michigan "
            "sentiment a month late at the university's request, and the one "
            "run that used it as a fallback froze `umich-2026-08-prelim` "
            "against a month-stale history and silently resolved it to July's "
            "final. See the module docstring."),
    },

    # --- permission needed --------------------------------------------------
    "yougov_xtab": {
        "publisher": "YouGov",
        "adapter": "ssa/adapters/yougov_xtab.py",
        "role": TARGET,
        "state": PERMISSION_NEEDED,
        "rights": PERMISSION_NEEDED,
        "evidence": (
            "YouGov's public-data licence prohibits using \"bots, crawlers, or "
            "automated scripts to extract or copy the Licensed Data\" without "
            "written permission. A permission request is with their legal "
            "team and unanswered. The network path is therefore opt-in "
            "(`SSA_YOUGOV_FETCH`) and is a deliberate maintainer act, never a "
            "side effect of the refresh; the sixteen registered cells are "
            "built from the committed vintages a maintainer already pulled."),
        "revisit": "Written permission from YouGov legal, or a licence change.",
    },
    "aaii": {
        "publisher": "American Association of Individual Investors",
        "adapter": "ssa/adapters/aaii.py",
        "role": TARGET,
        "state": PERMISSION_NEEDED,
        "rights": PERMISSION_NEEDED,
        "evidence": (
            "robots.txt disallows `/files/*`, which is where the sentiment "
            "workbook lives, and the site terms bar copying. The host has "
            "also been answering 503 to the runner, so generation reads the "
            "committed HTML archive rather than the network."),
        "revisit": "A robots rule or terms change, or written permission.",
    },
    "confboard": {
        "publisher": "The Conference Board",
        "adapter": "ssa/adapters/confboard.py",
        "role": TARGET,
        "state": PERMISSION_NEEDED,
        "rights": PERMISSION_NEEDED,
        "evidence": (
            "Terms bar extraction into a database. Separately, the history is "
            "not free: issue #36 priced the Data Central Consumer Confidence "
            "Survey dataset at $2,370 and established there is no free mirror "
            "(FRED's `CSCICP03USM665S` is the OECD amplitude-adjusted "
            "composite, a different series in different units). The release "
            "page itself is free and `confboard.parse` reads it, so the "
            "blocker is history plus terms, not parsing."),
        "revisit": (
            "Permission for the release page, or the Internet Archive "
            "backfill in `tools/backfill_cci.py` producing enough verified "
            "first prints to baseline against."),
    },
    "umichparty": {
        "publisher": "University of Michigan Surveys of Consumers",
        "adapter": "ssa/adapters/umichparty.py",
        "role": TARGET,
        "state": PERMISSION_NEEDED,
        "rights": PERMISSION_NEEDED,
        "evidence": (
            "The party cut lives only on the archive site "
            "(`data.sca.isr.umich.edu`), whose terms need written consent. "
            "The one timely artifact is a PDF addenda behind an opaque docid "
            "(`fetchdoc.php?docid=81624`) that gives no way to derive next "
            "month's, so `umichparty.fetch_latest` never fetches on its own "
            "and the three series are built from PDFs a maintainer committed. "
            "docs/sources.md §6 is the full history, including the reversal "
            "of the original rejection."),
        "revisit": (
            "Written consent, or the addenda gaining a non-PDF twin at a "
            "derivable URL."),
    },
    "pentaesi": {
        "publisher": "Penta-CivicScience",
        "adapter": "ssa/adapters/pentaesi.py",
        "role": TARGET,
        "state": PERMISSION_NEEDED,
        "rights": PERMISSION_NEEDED,
        "evidence": (
            "Biweekly Economic Sentiment Index, free, history back to 2013, "
            "and the adapter parses it (issue #36, PR #34) -- but the source "
            "archives nothing, so it cannot be rebuilt from a clean checkout "
            "and the generator reports it as `offline_unavailable` before the "
            "rights question is even reached. Both have to be answered before "
            "it can carry a round."),
        "revisit": (
            "Permission plus a committed archive of the release pages, so "
            "generation and resolution stop depending on the host being up."),
    },

    # --- rejected, with the fact that would reopen each ----------------------
    "census_marts": {
        "publisher": "US Census Bureau",
        "adapter": None,
        "role": TARGET,
        "state": REJECTED,
        "rights": APPROVED,
        "evidence": (
            "**Every mechanical gate passes, and it is refused anyway.** "
            "`tools/probe_marts.py` measured 36 monthly advance releases "
            "(2023-07 to 2026-06), and `sources/marts/probe.json` holds each "
            "one's URL, sha256 and parsed values so the run is reproducible: "
            "eight three-digit retail categories present in 36/36 with an "
            "advance value in 36/36 and one label spelling each; the advance "
            "differs from the next release's print for the same month in "
            "280/280 pairs (mean 0.52%), so \"resolve from the archived "
            "advance, never a revision\" is implementable *and* necessary; "
            "mean absolute month-over-month movement 0.48% to 2.47%; public "
            "domain, no key; 8:30 a.m. ET on a forward calendar Census "
            "publishes. The refusal is about what it asks, not how it is "
            "served: MARTS surveys *businesses* about sales receipts, so a "
            "simulated citizen structurally cannot answer it -- the objection "
            "issue #36 raised when the retail series were first considered. "
            "Scoring it would measure macroeconomic nowcasting, a different "
            "benchmark from the one this arena runs, and issue #48 lists "
            "redesigning the taxonomy as a non-goal."),
        "revisit": (
            "A maintainer decision that the arena scores establishment "
            "statistics alongside public opinion. Nothing else is missing: "
            "the probe, the fixture and `tests/test_marts_probe.py` are the "
            "integration work already done, and an adapter would be a day. "
            "Two operational notes for whoever takes that decision. The "
            "archive backfills late -- `rs2607.xlsx` was still 404 seventeen "
            "days after the July 2026 release -- so a resolver must snapshot "
            "`marts_current.xlsx` on release day and cite `rs{YY}{MM}.xlsx` "
            "once it appears. And the release interval is not a fixed day of "
            "the month; the 2025 shutdown moved it and the calendar has been "
            "catching up since, so lock times come from the published "
            "calendar, never from a rule."),
    },
    "acsi": {
        "publisher": "American Customer Satisfaction Index",
        "adapter": None,
        "role": TARGET,
        "state": REJECTED,
        "rights": UNRESOLVED,
        "evidence": (
            "Quarterly national and sector scores are free; company-level "
            "detail is paid. Issue #36 raised it as the closest free thing to "
            "brand and category perception and closed without a verdict. Four "
            "releases a year cannot fill a weekly batch, and no adapter "
            "exists, so it is out for season 0. Terms were never audited."),
        "revisit": (
            "A season whose cadence tolerates quarterly rounds, or a use for "
            "the sector scores as a slow-moving companion series."),
    },
    "mc_ics": {
        "publisher": "Morning Consult",
        "adapter": None,
        "role": TARGET,
        "state": REJECTED,
        "rights": UNRESOLVED,
        "evidence": (
            "Morning Consult's daily Index of Consumer Sentiment is behind a "
            "subscription (issue #36). §1.3 rules out a subscription with a "
            "redistribution clause outright, because this repository commits "
            "the raw bodies."),
        "revisit": "A free tier, or a licence that permits committing bodies.",
    },
    "yougov_brandindex": {
        "publisher": "YouGov",
        "adapter": None,
        "role": TARGET,
        "state": REJECTED,
        "rights": UNRESOLVED,
        "evidence": (
            "Enterprise contract only (issue #36). Same §1.3 objection as "
            "Morning Consult ICS, and the same unanswered YouGov permission "
            "question as `yougov_xtab`."),
        "revisit": "An academic or research tier.",
    },
    "ipsos_forbes": {
        "publisher": "Ipsos / Forbes Advisor",
        "adapter": None,
        "role": TARGET,
        "state": REJECTED,
        "rights": UNRESOLVED,
        "evidence": (
            "Dropped in issue #36 as macro expectations rather than brand or "
            "category perception, which is what it was surveyed for. Note the "
            "NY Fed SCE was dropped in the same line and later integrated on "
            "its own merits, so this row is a scope judgement, not a finding "
            "about the source itself."),
        "revisit": "A round family that wants a second macro-expectations house.",
    },
    "gdelt": {
        "publisher": "The GDELT Project",
        "adapter": None,
        "role": TARGET,
        "state": REJECTED,
        "rights": UNRESOLVED,
        "evidence": (
            "Keyless, supports date ranges, carries a tone score (issue #25). "
            "It updates every fifteen minutes, so under §1.1 it is a "
            "continuously-updated stream: no third party commits to a release "
            "moment, and asking about a window we choose makes the arena both "
            "question setter and resolver. Surveyed, no adapter written."),
        "revisit": (
            "A protocol decision to score class-B stream targets with the "
            "metric definition timestamped before the window opens, which "
            "issue #25 routes to #17 rather than to a source review."),
    },
    "x_community_notes": {
        "publisher": "X",
        "adapter": None,
        "role": TARGET,
        "state": REJECTED,
        "rights": UNRESOLVED,
        "evidence": (
            "Public daily TSV dumps (issue #25). Same §1.1 objection as "
            "GDELT: a daily dump of a continuous stream is not a release "
            "announced in advance, and the quantity scored would be one we "
            "defined. Surveyed, no adapter written, terms not audited."),
        "revisit": "The same protocol decision as GDELT.",
    },
    "bluesky": {
        "publisher": "Bluesky / AT Protocol",
        "adapter": None,
        "role": TARGET,
        "state": REJECTED,
        "rights": UNRESOLVED,
        "evidence": (
            "Fully public, free and timestamped -- issue #25 calls it the "
            "cleanest stream available -- and explicitly class B: a "
            "continuous stream the arena would window itself. #25's standing "
            "instruction is \"Prefer Class A. Do not start with Class B.\""),
        "revisit": "The protocol decision in #17, not a source review.",
    },
    "mastodon": {
        "publisher": "Mastodon (federated)",
        "adapter": None,
        "role": TARGET,
        "state": REJECTED,
        "rights": UNRESOLVED,
        "evidence": (
            "Class B like Bluesky, and additionally instance-fragmented, so "
            "there is no population a number could be about (issue #25)."),
        "revisit": "The protocol decision in #17.",
    },
}


def rights_table():
    """`{source: rights}` -- the table the generation gate reads.

    Returned as a fresh dict so a caller cannot mutate the inventory by
    editing what it was handed. Anything absent is `unresolved` at the call
    site, which is what makes adding an adapter unable to add rounds.
    """
    return {k: v["rights"] for k, v in INVENTORY.items()}


def state_of(source):
    """`integrated` / `permission-needed` / `rejected`, or None if unaudited."""
    row = INVENTORY.get(source)
    return row["state"] if row else None


def targets(state=None):
    """Source keys the arena asks questions about, optionally by state."""
    return sorted(k for k, v in INVENTORY.items()
                  if v["role"] == TARGET and (state is None
                                              or v["state"] == state))


def unaudited(sources):
    """Which of `sources` has no row here.

    Called with the registry's source keys by `tests/test_inventory.py`, so a
    new adapter that skips the audit fails a test rather than silently
    inheriting `unresolved` and looking like a deliberate refusal.
    """
    return sorted(set(sources) - set(INVENTORY))
