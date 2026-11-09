"""The Urban Track sources, one registry keyed by source.

Every Urban Track source reads its answer from SSA's own archive, never from
a release a publisher schedules, so each one carries rules the generic path
cannot infer from a series: which period a round asks about, when its card
says it locks and resolves, how the ranked reference is computed from the
capture archived at the lock, and which archived capture resolves it. Before
this module those rules lived in each adapter and nothing called them, apart
from two housing hooks written straight into refresh.py and resolve.py.

One entry per source key (the `source` of its series in ssa/series.py and
its row in ssa/inventory.py). Each entry names the approved Data Card it
implements. The season validator (`schedule_problem`), the refresh
(`lock_reference`, `lock_check`), the resolver (`resolve`), the profile board
(`profile_hooks`) and the series registry (`history`) all read this table, so
a source is wired once and every caller takes the same path.

Adapters are imported inside the functions: the registry is read by
ssa/season.py and ssa/series.py, and importing eleven adapters to validate a
season that uses none of them would make every caller pay for all of them.
"""
import importlib
import re
from datetime import date, datetime, timedelta, timezone

from . import baselines

NotPostable = baselines.NotPostable


def _adapter(name):
    return importlib.import_module(f"ssa.adapters.{name}")


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


_DAY = re.compile(r"(20\d\d-\d\d-\d\d)$")
_MONTH = re.compile(r"(20\d\d-\d\d)$")
_EXERCISE = re.compile(r"(20\d\d-\d\d)-([12])$")


def _id_day(r, weekday, what):
    """The date a round id ends with, which must fall on `weekday` -- the
    Wikipedia pattern in ssa/season.py: the measured period is frozen in the
    id, so a typo in the date cannot silently move the week."""
    m = _DAY.search(r.get("round_id", ""))
    if not m:
        raise ValueError(f"round id does not end with the {what} it asks about")
    d = date.fromisoformat(m.group(1))
    if d.weekday() != weekday:
        names = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                 "Saturday", "Sunday")
        raise ValueError(f"round id names {d}, a {names[d.weekday()]}, not "
                         f"the {names[weekday]} that ends its {what}")
    return d


class Source:
    """One source's rules. `target(r)` turns a round into the adapter's own
    period key; the rest take that key."""

    def __init__(self, key, card, kind, *, target, lock=None, release=None,
                 reference=None, resolver=None, lock_check=None, history=None,
                 observed=None, schedule_rule=None, profile=None):
        self.key, self.card, self.kind = key, card, kind
        self._target, self._lock, self._release = target, lock, release
        self._reference, self._resolver = reference, resolver
        self._lock_check, self._history = lock_check, history
        self._observed = observed or (lambda t: str(t))
        self.schedule_rule = schedule_rule
        # {"month": fn(round), "resolve": fn(round, cells)} for a profile
        # source whose cells are dated by the month they measure
        # (ssa/profile_round.month_labelled).
        self.profile = profile

    def target(self, r):
        return self._target(r)

    # -- the schedule a card fixes, checked by ssa/season.py
    @property
    def has_schedule(self):
        return self._lock is not None

    def schedule_problem(self, r):
        """None when the round's lock and release are the card's, else why."""
        if self._lock is None:
            return None
        try:
            t = self._target(r)
            want_lock, want_release = self._lock(t), self._release(t)
        except (KeyError, ValueError) as e:
            return f"{self.card}: {e}"
        got_lock, got_release = _parse(r["lock_at"]), _parse(r["release_at"])
        if got_lock != want_lock:
            return (f"{self.card} ({self.schedule_rule}): lock_at is "
                    f"{r['lock_at']}, the card's lock for {t} is "
                    f"{_iso(want_lock)}")
        if got_release != want_release:
            return (f"{self.card} ({self.schedule_rule}): release_at is "
                    f"{r['release_at']}, the card's resolution for {t} opens "
                    f"{_iso(want_release)}")
        return None

    # -- the reference frozen at lock, attached by ssa/refresh.py
    @property
    def has_lock_reference(self):
        return self._reference is not None

    def lock_reference(self, r):
        """The adapter's reference from the capture archived at lock. Raises
        NotPostable when the card refuses to post one, or when the round names
        a zero point other than the one the card's rule computes."""
        out = self._reference(r, self._target(r))
        want = baselines.zero_point_name(r)
        if out.get("zero_point") != want:
            raise NotPostable(
                f"{self.card}: the round is ranked on {want!r} but the card's "
                f"lock-time rule computes {out.get('zero_point')!r}")
        return out

    def lock_check(self, r):
        """A freshness rule on a history-route reference (raises NotPostable)."""
        if self._lock_check is not None:
            self._lock_check(r)

    # -- the answer, read by ssa/resolve.py
    @property
    def resolves(self):
        return self._resolver is not None

    def resolve(self, r, now):
        """(resolution, None) or (None, reason). Pending, cancelled and
        review all leave the round unresolved, with the adapter's reason."""
        t = self._target(r)
        got = self._resolver(r, t, now)
        status = got.get("status")
        if status != "resolved":
            reason = got.get("reason") or status
            return None, f"{self.card}: {status}: {reason}"
        vintage = got.get("snapshot") or got.get("file") or got.get("capture")
        sha = got.get("sha256") or got.get("files")
        res = {
            "value": float(got["value"]),
            "observed_date": self._observed(t),
            "series": r["series"],
            "method": (f"{self.card}: the first archived capture its "
                       f"resolution rule accepts ({vintage}, fetched "
                       f"{got.get('fetched_at')})"),
            "vintage": vintage,
            "vintage_sha256": sha,
            "resolved_at": _iso(now),
        }
        for k in ("mode", "minute", "unknown_types", "unknown_agencies",
                  "window", "quota", "bids_received", "total_tap_in",
                  "weekdays", "backfill"):
            if got.get(k) is not None:
                res[k] = got[k]
        return res, None

    # -- the displayed history, read by ssa/series.py
    def history(self, spec):
        return self._history(spec) if self._history else []


# ---------------------------------------------------------------- week sources
#
# Seattle, NYC 311, MTA and TSA ask about a Monday-to-Sunday week named by its
# Sunday in the round id, lock before the week starts (Sunday 23:59 local),
# and resolve from a capture taken days after it ends.

def _week_monday(r):
    return _id_day(r, 6, "week") - timedelta(days=6)


def _week_rows(points):
    """{Monday: total} -> [{date: Sunday, value}], oldest first."""
    return [{"date": (m + timedelta(days=6)).isoformat(), "value": float(v)}
            for m, v in sorted(points.items())]


def _newest(fetches, ok=lambda row: True):
    days = sorted(d for d, row in fetches.items() if ok(row))
    return days[-1] if days else None


def _seattle_history(spec):
    a = _adapter("seattle_cad")
    day = _newest(a.load_fetches())
    if day is None:
        return []
    daily, _ = a.read_capture(day)
    return a.weekly_series(a.community_daily(daily))


def _nyc_history(spec):
    a = _adapter("nyc311")
    fetches = a.load_fetches()
    day = _newest(fetches, a.is_fresh)
    if day is None:
        return []
    daily, _ = a.read_capture(day, fetches)
    return a.weekly_series(daily)


def _mta_history(spec):
    a = _adapter("mta_daily")
    fetches = a.load_fetches()
    day = _newest(fetches)
    if day is None:
        return []
    parsed, _ = a.read_capture(day, fetches)
    return _week_rows(a.weekly(a.subway(parsed)))


def _tsa_history(spec):
    a = _adapter("tsa_volumes")
    fetches = a.load_fetches()
    day = _newest(fetches)
    if day is None:
        return []
    daily, _ = a.read_capture(day, fetches)
    return _week_rows(a.weekly(daily))


def _mta_release(monday):
    return _adapter("mta_daily").resolution_window(monday)[0]


# ---------------------------------------------------------------- Singapore

def _coe_target(r):
    m = _EXERCISE.search(r.get("round_id", ""))
    if not m:
        raise ValueError("round id does not end with the exercise it asks "
                         "about (<YYYY-MM>-<1|2>)")
    return m.group(1), m.group(2)


def _coe_close(t):
    a = _adapter("sg_coe")
    return a.window(*t)[1].astimezone(timezone.utc)


def _coe_history(spec):
    a = _adapter("sg_coe")
    rows = a.load_fetches()
    if not rows:
        return []
    key = max(rows, key=lambda k: rows[k]["fetched_at"])
    tab, _ = a.read_capture(key, rows)
    out = []
    for i, v in sorted(a.category_series(tab).items()):
        month, b = a.exercise_of(i)
        try:
            day = _coe_close((month, b)).date().isoformat()
        except KeyError:
            day = f"{month}-{'01' if str(b) == '1' else '15'}"
        out.append({"date": day, "value": float(v)})
    return out


def _minute_history(name):
    def history(spec):
        a = _adapter(name)
        src = a.source()
        rows = src.store().load()
        if not rows:
            return []
        series = src.series(src.evaluate(rows))
        return [{"date": d.isoformat(), "value": float(v)}
                for d, v in sorted(series.items())]
    return history


def _minute_release(name):
    def release(day):
        src = _adapter(name).source()
        return src.target_instant(src.check_day(day)).astimezone(timezone.utc)
    return release


def _lta_kind(r):
    a = _adapter("sg_lta_pv")
    for kind, spec in a.KINDS.items():
        if spec["series"] == r.get("series"):
            return kind
    raise ValueError(f"series {r.get('series')!r} is not a passenger-volume kind")


def _lta_target(r):
    m = _MONTH.search(r.get("round_id", ""))
    if not m:
        raise ValueError("round id does not end with the month it asks about")
    return _lta_kind(r), m.group(1)


def _lta_history(spec):
    a = _adapter("sg_lta_pv")
    kind = next(k for k, s in a.KINDS.items() if s["series"] == spec["sid"])
    if not a.load_fetches(kind):
        return []
    return [{"date": m + "-01", "value": float(v)}
            for m, v in sorted(a.history(kind).items())]


# ---------------------------------------------------------------- housing
#
# Both resolve on the first archived vintage that carries the month a round
# asks about. CPI rent's reference is history-route (a spec with `target`)
# plus a freshness rule at lock; the SCE moving profile reads month-labelled
# cells (ssa/profile_round.py).

def _bls_lock_check(r):
    a = _adapter("bls_cpi")
    a.lock_capture(r["lock_at"], a.reference_month(r["release_at"]))


def _bls_resolve(r, month, now):
    a = _adapter("bls_cpi")
    try:
        got = a.resolution(month, r["release_at"], now=now)
    except ValueError as e:
        return {"status": "pending", "reason": str(e)}
    return {"status": "resolved", "value": got["value"], "file": got["file"],
            "sha256": got["sha256"], "fetched_at": got["fetched_at"],
            "levels": (got["level"], got["level_prev"])}


def _sce_month(r):
    return _adapter("sce").survey_month(r["release_at"])


SOURCES = {}


def _register(src):
    SOURCES[src.key] = src
    return src


_register(Source(
    "seattle_cad", "seattle-community-calls@v4", "behavioral week",
    target=_week_monday,
    lock=lambda m: _adapter("seattle_cad").lock_instant(m),
    release=lambda m: _adapter("seattle_cad").resolution_instant(m),
    reference=lambda r, m: _adapter("seattle_cad").lock_reference(m),
    resolver=lambda r, m, now: _adapter("seattle_cad").resolve(m, now),
    history=_seattle_history, observed=lambda m: m.isoformat(),
    schedule_rule="locks Sunday 23:59 PT before the week, resolves Tuesday "
                  "12:00 PT eight days after it"))

_register(Source(
    "nyc311", "nyc-311-requests@v2", "behavioral week",
    target=_week_monday,
    lock=lambda m: _adapter("nyc311").lock_instant(m),
    release=lambda m: _adapter("nyc311").resolution_instant(m),
    reference=lambda r, m: _adapter("nyc311").lock_reference(m),
    # Route A (automated capture) is the card's; a round moved to the manual
    # fallback before it is posted would name its mode here (card v2).
    resolver=lambda r, m, now: _adapter("nyc311").resolve(m, now, mode="auto"),
    history=_nyc_history, observed=lambda m: m.isoformat(),
    schedule_rule="locks Sunday 23:59 ET before the week, resolves Tuesday "
                  "12:00 ET eight days after it"))

_register(Source(
    "mta_daily", "mta-subway-weekly@v2", "behavioral week",
    target=_week_monday,
    lock=lambda m: _adapter("mta_daily").lock_instant(m),
    release=_mta_release,
    reference=lambda r, m: _adapter("mta_daily").lock_reference(m),
    resolver=lambda r, m, now: _adapter("mta_daily").resolve(m, now),
    history=_mta_history, observed=lambda m: m.isoformat(),
    schedule_rule="locks Sunday 23:59 ET before the week, resolves from the "
                  "Saturday 00:00 ET download twelve days after it starts"))

_register(Source(
    "tsa_volumes", "tsa-checkpoint-weekly@v2", "behavioral week",
    target=_week_monday,
    lock=lambda m: _adapter("tsa_volumes").lock_instant(m),
    release=lambda m: _adapter("tsa_volumes").resolution_instant(m),
    reference=lambda r, m: _adapter("tsa_volumes").lock_reference(m),
    resolver=lambda r, m, now: _adapter("tsa_volumes").resolve(m, now),
    history=_tsa_history, observed=lambda m: m.isoformat(),
    schedule_rule="locks Sunday 23:59 ET before the week, resolves Tuesday "
                  "12:00 ET after it"))

_register(Source(
    "sg_coe", "sg-coe-cat-a-premium@v2", "early lock by design",
    target=_coe_target,
    lock=lambda t: _adapter("sg_coe").lock_instant(*t).astimezone(timezone.utc),
    release=_coe_close,
    reference=lambda r, t: _adapter("sg_coe").lock_reference(*t),
    resolver=lambda r, t, now: _adapter("sg_coe").resolve(*t, now),
    history=_coe_history, observed=lambda t: f"{t[0]} ({t[1]})",
    schedule_rule="locks one hour before bidding opens, because the running "
                  "price is public once it does; release is the exercise "
                  "close"))

_register(Source(
    "sg_taxi", "sg-taxi-downtown-fri2300@v2", "SSA-defined real-time metric",
    target=lambda r: _id_day(r, 4, "target Friday"),
    lock=lambda d: _adapter("sg_taxi").lock_instant(d).astimezone(timezone.utc),
    release=_minute_release("sg_taxi"),
    reference=lambda r, d: _adapter("sg_taxi").lock_reference(d),
    resolver=lambda r, d, now: _adapter("sg_taxi").resolve(d, now),
    history=_minute_history("sg_taxi"), observed=lambda d: d.isoformat(),
    schedule_rule="locks 48 h before the target minute, Friday 23:00 SGT, "
                  "which is the release"))

_register(Source(
    "sg_carpark_hdb", "sg-hdb-carpark-clementi-sat2000@v2",
    "SSA-defined real-time metric",
    target=lambda r: _id_day(r, 5, "target Saturday"),
    lock=lambda d: _adapter("sg_carpark").lock_instant(d).astimezone(timezone.utc),
    release=_minute_release("sg_carpark"),
    reference=lambda r, d: _adapter("sg_carpark").lock_reference(d),
    resolver=lambda r, d, now: _adapter("sg_carpark").resolve(d, now),
    history=_minute_history("sg_carpark"), observed=lambda d: d.isoformat(),
    schedule_rule="locks 48 h before the target minute, Saturday 20:00 SGT, "
                  "which is the release"))

_register(Source(
    "sg_lta_pv", "sg-lta-pv-train-jurong-east@v2 / sg-lta-pv-bus-boon-lay-int@v2",
    "early lock by design",
    target=_lta_target,
    lock=lambda t: _adapter("sg_lta_pv").lock_instant(t[1]).astimezone(timezone.utc),
    release=lambda t: _adapter("sg_lta_pv").release_instant(t[1]).astimezone(timezone.utc),
    reference=lambda r, t: _adapter("sg_lta_pv").lock_reference(*t),
    resolver=lambda r, t, now: _adapter("sg_lta_pv").resolve(*t, now),
    history=_lta_history, observed=lambda t: t[1] + "-01",
    schedule_rule="locks the day before the month starts; DataMall posts the "
                  "month's file from the 10th of the next"))

_register(Source(
    "bls_cpi", "bls-cpi-rent@v2", "scheduled release",
    target=lambda r: _adapter("bls_cpi").reference_month(r["release_at"]),
    lock_check=_bls_lock_check, resolver=_bls_resolve,
    observed=lambda month: month + "-01"))

def _sce_profile_resolve(r, cells):
    from . import profile_round
    return profile_round.sce_moving_resolution(r, cells)


_register(Source(
    "sce_moving", "nyfed-sce-moving@v2", "scheduled release",
    target=_sce_month,
    profile={"month": _sce_month, "resolve": _sce_profile_resolve}))


def get(source):
    """The registry entry for a source key, or None for every other source."""
    return SOURCES.get(source)
