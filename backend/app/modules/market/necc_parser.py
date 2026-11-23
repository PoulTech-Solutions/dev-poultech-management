from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

_START = 'NECC SUGGESTED EGG PRICES'
_END_MARKERS = ('Prevailing Prices', 'Prevailing prices')

_ROW_RE = re.compile(
    r"<tr[^>]*>\s*<td[^>]*align=['\"]left['\"][^>]*>([^<]+)</td>(.*?)</tr>",
    re.IGNORECASE | re.DOTALL,
)
_CELL_RE = re.compile(r'<td[^>]*>([^<]*)</td>', re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ZoneRow:
    name: str
    daily: dict[int, Decimal]  # day of month 1..31
    sheet_average: Decimal | None = None  # NECC "Average" column for the month


def parse_necc_suggested_prices(html: str) -> list[ZoneRow]:
    start = html.find(_START)
    if start < 0:
        raise ValueError('NECC suggested price section not found')
    section = html[start:]
    end = len(section)
    for marker in _END_MARKERS:
        pos = section.find(marker)
        if pos > 0:
            end = min(end, pos)
    section = section[:end]

    rows: list[ZoneRow] = []
    for match in _ROW_RE.finditer(section):
        name = match.group(1).strip()
        if not name or name.upper().startswith('NECC'):
            continue
        cells = _CELL_RE.findall(match.group(2))
        if not cells:
            continue
        sheet_average: Decimal | None = None
        day_cells = cells
        if len(cells) > 1:
            last = cells[-1].strip().replace(',', '')
            if last and last not in {'-', '—'}:
                try:
                    sheet_average = Decimal(last)
                    day_cells = cells[:-1]
                except InvalidOperation:
                    day_cells = cells[:-1]
        daily: dict[int, Decimal] = {}
        for day_index, raw in enumerate(day_cells, start=1):
            if day_index > 31:
                break
            token = raw.strip().replace(',', '')
            if not token or token in {'-', '—'}:
                continue
            try:
                daily[day_index] = Decimal(token)
            except InvalidOperation:
                continue
        if daily:
            rows.append(ZoneRow(name=name, daily=daily, sheet_average=sheet_average))
    if not rows:
        raise ValueError('No NECC zone rows parsed')
    return rows
