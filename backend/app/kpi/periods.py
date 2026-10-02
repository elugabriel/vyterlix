"""Calendar periods for KPIs: weeks (Monday to Sunday), months, quarters and years.

UK trading weeks start on Monday. Quarters and years are calendar ones for now; a business's
own financial year (from its profile) comes with the year-to-date KPIs.
"""

from datetime import date, timedelta


def start_of(day: date, granularity: str) -> date:
    if granularity == "week":
        return day - timedelta(days=day.weekday())
    if granularity == "month":
        return day.replace(day=1)
    if granularity == "quarter":
        return date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)
    if granularity == "year":
        return date(day.year, 1, 1)
    raise ValueError(f"Unknown granularity {granularity!r}")


def _add_months(day: date, months: int) -> date:
    index = day.year * 12 + (day.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


def shift(start: date, granularity: str, periods: int) -> date:
    """The start of the period `periods` away (negative = earlier) from a period start."""
    if granularity == "week":
        return start + timedelta(weeks=periods)
    months = {"month": 1, "quarter": 3, "year": 12}[granularity]
    return _add_months(start, months * periods)


def end_of(start: date, granularity: str) -> date:
    """The last day of the period (inclusive)."""
    return shift(start, granularity, 1) - timedelta(days=1)


def same_period_last_year(start: date, granularity: str) -> date:
    """52 weeks back for weeks (so the weekday matches), a year back for the rest."""
    if granularity == "week":
        return shift(start, "week", -52)
    return shift(start, "year", -1)


def series(first: date, last: date, granularity: str) -> list[date]:
    """Every period start from `first` to `last` inclusive (both period starts)."""
    out, current = [], first
    while current <= last:
        out.append(current)
        current = shift(current, granularity, 1)
    return out
