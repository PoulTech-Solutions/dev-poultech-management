from __future__ import annotations

from calendar import monthrange
from datetime import date


def week_of_month(day: int) -> int:
    """Calendar week inside a month: 1-7 -> 1, 8-14 -> 2, ..."""
    if day < 1:
        raise ValueError('day must be >= 1')
    return (day - 1) // 7 + 1


def week_day_span(year: int, month: int, week: int) -> tuple[int, int]:
    """Inclusive day numbers for week 1-5 of year/month."""
    if week < 1 or week > 5:
        raise ValueError('week must be 1-5')
    last = monthrange(year, month)[1]
    start = (week - 1) * 7 + 1
    if start > last:
        raise ValueError('week is empty in this month')
    return start, min(week * 7, last)


def shift_month(year: int, month: int, offset: int) -> tuple[int, int]:
    """Return (year, month) offset months from the given period (offset 0 = same month)."""
    idx = year * 12 + (month - 1) + offset
    return idx // 12, (idx % 12) + 1


def list_recent_months(anchor: date, count: int) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    for offset in range(count):
        pairs.append(shift_month(anchor.year, anchor.month, -offset))
    return pairs
