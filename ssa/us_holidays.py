"""US federal holidays, and which ones a 364-day comparison misaligns.

The year-over-year references (`baselines.yoy_k`) compare a week with the
week 364 days earlier: the same weekdays, so a holiday defined by its weekday
(Thanksgiving, Labor Day) usually lines up and one defined by its date
(Christmas, New Year's Day) moves by a day. Cards mta-subway-weekly@v2 and
tsa-checkpoint-weekly@v2 keep the 364-day shift and *flag* the weeks where it
misaligns a holiday in the round text (owner decision rev 2), rather than
changing the rule. `misaligned` is that flag, computed rather than remembered.

Dates are the holidays themselves, not the observed weekday off: the travel a
holiday moves follows the day, and the cards' evidence is stated that way.
"""
from datetime import date, timedelta


def _nth_weekday(year, month, weekday, n):
    """The n-th `weekday` (Mon=0) of the month; n = -1 is the last."""
    if n > 0:
        d = date(year, month, 1)
        d += timedelta(days=(weekday - d.weekday()) % 7)
        return d + timedelta(days=7 * (n - 1))
    d = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def holidays(year):
    """{date: name} for the eleven federal holidays of `year`."""
    return {
        date(year, 1, 1): "New Year's Day",
        _nth_weekday(year, 1, 0, 3): "Martin Luther King Jr. Day",
        _nth_weekday(year, 2, 0, 3): "Presidents' Day",
        _nth_weekday(year, 5, 0, -1): "Memorial Day",
        date(year, 6, 19): "Juneteenth",
        date(year, 7, 4): "Independence Day",
        _nth_weekday(year, 9, 0, 1): "Labor Day",
        _nth_weekday(year, 10, 0, 2): "Columbus Day",
        date(year, 11, 11): "Veterans Day",
        _nth_weekday(year, 11, 3, 4): "Thanksgiving",
        date(year, 12, 25): "Christmas Day",
    }


def _nearest(name, day):
    """The occurrence of holiday `name` nearest to `day` (New Year's Day
    2027-01-01 is the one nearest 2026-12-31, not 2026-01-01)."""
    occ = [d for y in (day.year - 1, day.year, day.year + 1)
           for d, n in holidays(y).items() if n == name]
    return min(occ, key=lambda d: abs((d - day).days))


def misaligned(start, end):
    """Holidays the 364-day shift does not map onto themselves, for the days
    [start, end] and the same days 364 earlier, sorted by date:
    [{"holiday", "date", "compared_with", "holiday_last_year"}].

    A holiday is listed when it falls in the window this year, or in the
    comparison window last year, and the other year's occurrence is not
    exactly 364 days away (e.g. Labor Day 2026-09-07 is compared with
    2025-09-08; the holiday was 2025-09-01).
    """
    shift = timedelta(days=364)
    found = {}
    for y in range((start - shift).year, end.year + 1):
        for h, name in holidays(y).items():
            if start <= h <= end:
                this, prev = h, _nearest(name, h - shift)
            elif start <= h + shift <= end:
                this, prev = _nearest(name, h + shift), h
            else:
                continue
            if this - shift != prev:
                found[(this, name)] = {
                    "holiday": name, "date": this.isoformat(),
                    "compared_with": (this - shift).isoformat(),
                    "holiday_last_year": prev.isoformat()}
    return [found[k] for k in sorted(found)]
