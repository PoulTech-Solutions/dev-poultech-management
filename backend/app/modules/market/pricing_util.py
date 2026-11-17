from __future__ import annotations

from decimal import Decimal


def latest_day_on_sheet(month_map: dict[int, Decimal], *, through_day: int) -> int | None:
    """Latest NECC column day in this month sheet, not after ``through_day`` (usually today)."""
    if not month_map:
        return None
    eligible = [d for d in month_map if d <= through_day]
    return max(eligible) if eligible else None


def spot_from_sheet(
    month_map: dict[int, Decimal],
    calendar_day: int,
) -> tuple[int, Decimal, bool]:
    """Return (sheet_day, price, is_calendar_today). Uses latest published day if today missing."""
    if calendar_day in month_map:
        return calendar_day, month_map[calendar_day], True
    latest = latest_day_on_sheet(month_map, through_day=calendar_day)
    if latest is None:
        raise ValueError('no price on sheet')
    return latest, month_map[latest], False


def neighbor_on_sheet(month_map: dict[int, Decimal], sheet_day: int, offset: int) -> Decimal | None:
    target = sheet_day + offset
    return month_map.get(target)
