from decimal import Decimal
from pathlib import Path

import pytest

from app.modules.market.necc_parser import parse_necc_suggested_prices

FIXTURE = Path(__file__).resolve().parents[2] / 'fixtures' / 'necc_sample.html'


def test_parse_necc_sample_rows() -> None:
    html = FIXTURE.read_text(encoding='utf-8')
    rows = parse_necc_suggested_prices(html)
    names = {row.name for row in rows}
    assert 'Pune' in names
    assert 'Mumbai (CC)' in names
    pune = next(row for row in rows if row.name == 'Pune')
    assert pune.daily[1] == Decimal('670')
    assert pune.daily[3] == Decimal('690')


def test_parse_missing_section_raises() -> None:
    with pytest.raises(ValueError, match='section not found'):
        parse_necc_suggested_prices('<html></html>')
