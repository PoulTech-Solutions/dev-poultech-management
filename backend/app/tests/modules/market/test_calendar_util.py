from app.modules.market.calendar_util import week_day_span, week_of_month


def test_week_of_month_spans() -> None:
    assert week_of_month(1) == 1
    assert week_of_month(7) == 1
    assert week_of_month(8) == 2
    assert week_of_month(22) == 4
    assert week_of_month(31) == 5


def test_week_day_span_november() -> None:
    assert week_day_span(2026, 11, 1) == (1, 7)
    assert week_day_span(2026, 11, 4) == (22, 28)
    assert week_day_span(2026, 11, 5) == (29, 30)
