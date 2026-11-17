from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.modules.market.zones import necc_disclaimer


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class MarketZonePreferenceRequest(_StrictModel):
    zone: str = Field(min_length=1, max_length=120)


class MarketZoneItem(BaseModel):
    model_config = ConfigDict(extra='forbid')

    name: str
    today_value: str | None = None
    today_change: float | None = None


class MarketTickerItem(BaseModel):
    model_config = ConfigDict(extra='forbid')

    label: str
    value: str
    change: float
    zone: str


class MarketRateCard(BaseModel):
    """Mirrors frontend AdminDemoRate for eggs."""

    model_config = ConfigDict(extra='forbid')

    id: str = 'eggs'
    title: str = 'Eggs'
    unit: str = 'per 100 eggs (NECC suggested)'
    value: str
    previousValue: str
    nextValue: str
    month: list[float]
    year: list[float]
    history: list[float]
    live: bool = True
    source: str = 'necc'
    zone: str
    month_average: str | None = None
    necc_sheet_average: str | None = None


class MarketDashboardMetadata(BaseModel):
    model_config = ConfigDict(extra='forbid')

    source: str = 'necc'
    disclaimer: str = Field(default_factory=necc_disclaimer)
    display_unit: str = 'per_100_eggs'
    selected_zone: str
    resolved_zone: str | None
    match_status: str
    fallback_zone: str
    fetched_at: str | None
    zones_version: str | None = None
    price_as_of: str | None = None
    is_live_today: bool = True


class MarketDashboardResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    metadata: MarketDashboardMetadata
    rates: list[MarketRateCard]
    ticker: list[MarketTickerItem]


class MarketZonesResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    zones: list[MarketZoneItem]
    default_zone: str


class MarketCompareResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    zones: list[MarketTickerItem]
    as_of: str | None


class MarketTrendPoint(BaseModel):
    model_config = ConfigDict(extra='forbid')

    date: str
    value: float


class MarketTrendResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    zone: str
    window: str
    unit: str = 'per 100 eggs (NECC suggested)'
    series: list[float]
    points: list[MarketTrendPoint]
    year: int | None = None
    month: int | None = None
    sheet_average: str | None = None


class MarketDayPrice(BaseModel):
    model_config = ConfigDict(extra='forbid')

    day: int
    date: str
    price: str | None


class MarketEggSheetResponse(BaseModel):
    """NECC-like month grid for one city (from stored sync data)."""

    model_config = ConfigDict(extra='forbid')

    zone: str
    year: int
    month: int
    today: str | None
    yesterday: str | None
    tomorrow: str | None
    necc_sheet_average: str | None
    computed_daily_average: str | None
    days: list[MarketDayPrice]
