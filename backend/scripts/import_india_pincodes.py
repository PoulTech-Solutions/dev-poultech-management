"""Import India Post pincodes + village/post offices into geo_* tables.

Source (public OGD mirror): All India Pincode Directory CSV.

  python -m scripts.import_india_pincodes --csv path/to.csv
  python -m scripts.import_india_pincodes --url https://...
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.request import urlopen

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.modules.geo.repository import GeoRepository

_BATCH = 1500
_DEFAULT_URL = (
    'https://raw.githubusercontent.com/saravanakumargn/All-India-Pincode-Directory/'
    'master/all-india-pincode-html-csv.csv'
)


def _normalize_header(name: str) -> str:
    return name.strip().lower().replace(' ', '_')


def _pick_column(fieldnames: list[str], *candidates: str) -> str | None:
    norm = {_normalize_header(h): h for h in fieldnames}
    for c in candidates:
        if c in norm:
            return norm[c]
    return None


def _title(value: str) -> str:
    text = ' '.join(value.split())
    if not text:
        return text
    if text.isupper() or text.islower():
        return text.title()
    return text


def _iter_csv(text: str) -> Iterator[dict[str, str | None]]:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise SystemExit('CSV has no header row')
    pin_col = _pick_column(reader.fieldnames, 'pincode', 'pin_code', 'pin')
    state_col = _pick_column(reader.fieldnames, 'statename', 'state', 'state_name')
    district_col = _pick_column(reader.fieldnames, 'districtname', 'district', 'district_name')
    tehsil_col = _pick_column(reader.fieldnames, 'taluk', 'tehsil', 'taluka', 'regionname')
    office_col = _pick_column(reader.fieldnames, 'officename', 'office_name', 'office', 'officename')
    type_col = _pick_column(reader.fieldnames, 'officetype', 'office_type', 'officetype')
    if not pin_col or not state_col or not district_col:
        raise SystemExit('CSV must include pincode, state, and district columns')
    for raw in reader:
        pin = ''.join(ch for ch in str(raw.get(pin_col, '')).strip() if ch.isdigit())
        if len(pin) != 6 or pin[0] == '0':
            continue
        state = _title(str(raw.get(state_col, '')).strip())[:100]
        district = _title(str(raw.get(district_col, '')).strip())[:100]
        if not state or not district:
            continue
        taluk_raw = str(raw.get(tehsil_col, '')).strip() if tehsil_col else ''
        taluk = _title(taluk_raw)[:100] if taluk_raw and taluk_raw.upper() not in {'NA', 'NIL', 'NULL'} else None
        office = str(raw.get(office_col, '')).strip()[:150] if office_col else ''
        office_type = str(raw.get(type_col, '')).strip()[:20] if type_col else None
        yield {
            'pincode': pin,
            'state': state,
            'district': district,
            'tehsil': taluk,
            'office_name': office or None,
            'office_type': office_type or None,
        }


def _load_text(csv_path: Path | None, url: str | None) -> str:
    if csv_path is not None:
        return csv_path.read_text(encoding='utf-8-sig', errors='replace')
    assert url
    print(f'Downloading {url} …', file=sys.stderr)
    with urlopen(url, timeout=120) as response:  # noqa: S310 - operator-supplied URL
        return response.read().decode('utf-8-sig', errors='replace')


async def _run(*, csv_path: Path | None, url: str | None) -> int:
    settings = get_settings()
    rows = list(_iter_csv(_load_text(csv_path, url)))
    if not rows:
        print('No rows to import', file=sys.stderr)
        return 1

    unique_pins: dict[str, dict[str, str | None]] = {}
    offices: list[dict[str, str | None]] = []
    for row in rows:
        pin = str(row['pincode'])
        if pin not in unique_pins:
            unique_pins[pin] = {
                'pincode': pin,
                'state': row['state'],
                'district': row['district'],
                'tehsil': row['tehsil'],
            }
        if row.get('office_name'):
            offices.append(
                {
                    'id': uuid.uuid4(),
                    'pincode': pin,
                    'name': row['office_name'],
                    'office_type': row.get('office_type'),
                    'taluk': row.get('tehsil'),
                }
            )

    pin_list = list(unique_pins.values())
    seen_offices: set[tuple[str, str]] = set()
    unique_offices: list[dict[str, str | None]] = []
    for office in offices:
        key = (str(office['pincode']), str(office['name']).lower())
        if key in seen_offices:
            continue
        seen_offices.add(key)
        unique_offices.append(office)

    total_pins = 0
    total_offices = 0
    async with SessionLocal() as db:
        repo = GeoRepository(db)
        for start in range(0, len(pin_list), _BATCH):
            chunk = pin_list[start : start + _BATCH]
            total_pins += await repo.upsert_rows(chunk)
            await db.commit()
        for start in range(0, len(unique_offices), _BATCH):
            chunk = unique_offices[start : start + _BATCH]
            total_offices += await repo.upsert_offices(chunk)
            await db.commit()
    print(f'Imported {len(pin_list)} unique pincodes ({total_pins} upserts), {len(unique_offices)} offices')
    if settings.geo_pincode_strict:
        print('GEO_PINCODE_STRICT=true — profiles must use imported pincodes only.')
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description='Import India pincodes into Postgres')
    parser.add_argument('--csv', type=Path, help='Path to India Post CSV')
    parser.add_argument('--url', default=_DEFAULT_URL, help='Download CSV from this URL when --csv is omitted')
    args = parser.parse_args()
    if args.csv is not None and not args.csv.is_file():
        raise SystemExit(f'File not found: {args.csv}')
    raise SystemExit(asyncio.run(_run(csv_path=args.csv, url=None if args.csv else args.url)))


if __name__ == '__main__':
    main()
