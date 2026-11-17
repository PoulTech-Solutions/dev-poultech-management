from __future__ import annotations

from datetime import date


def shift_month(year: int, month: int, offset: int) -> tuple[int, int]:
    """Return (year, month) offset months from the given period (offset 0 = same month)."""
    idx = year * 12 + (month - 1) + offset
    return idx // 12, (idx % 12) + 1


def list_recent_months(anchor: date, count: int) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    for offset in range(count):
        pairs.append(shift_month(anchor.year, anchor.month, -offset))
    return pairs
