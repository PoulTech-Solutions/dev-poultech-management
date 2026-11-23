from decimal import Decimal

from app.modules.market.pricing_util import neighbor_on_sheet, spot_from_sheet


def test_neighbor_skips_blank_calendar_days() -> None:
    month = {20: Decimal('700'), 22: Decimal('705')}
    assert neighbor_on_sheet(month, 22, -1) == Decimal('700')
    assert neighbor_on_sheet(month, 20, 1) == Decimal('705')
    assert neighbor_on_sheet(month, 20, -1) is None


def test_blank_calendar_day_has_no_price() -> None:
    month = {21: Decimal('695'), 22: Decimal('685')}
    assert month.get(23) is None
    assert month.get(22) == Decimal('685')
    assert month.get(21) == Decimal('695')


def test_spot_uses_latest_published_when_today_missing() -> None:
    month = {20: Decimal('700'), 21: Decimal('702')}
    day, price, live = spot_from_sheet(month, 22)
    assert day == 21
    assert price == Decimal('702')
    assert live is False
