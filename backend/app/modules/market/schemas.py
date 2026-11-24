from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.market.zones import necc_disclaimer


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class MarketZonePreferenceRequest(_StrictModel):
    zone: str = Field(min_length=1, max_length=120)


class MarketZoneItem(BaseModel):
    model_config = ConfigDict(extra='forbid')

    name: str
    state: str | None = None
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
    week: int | None = None
    sheet_average: str | None = None


class MarketCoverageResponse(BaseModel):
    """Years → months → weeks that already exist in Postgres for one hub."""

    model_config = ConfigDict(extra='forbid')

    zone: str
    years: list[int]
    months: dict[str, list[int]]
    weeks: dict[str, list[int]]


class MarketDayPrice(BaseModel):
    model_config = ConfigDict(extra='forbid')

    day: int
    date: str
    price: str | None


class ManualPoultryRateUpsertRequest(_StrictModel):
    product: Literal['culls', 'broilers']
    zone: str = Field(min_length=1, max_length=120)
    today: Decimal | None = Field(default=None, gt=0, decimal_places=2)
    yesterday: Decimal | None = Field(default=None, gt=0, decimal_places=2)
    tomorrow: Decimal | None = Field(default=None, gt=0, decimal_places=2)
    apply_all_zones: bool = False

    @field_validator('today', 'yesterday', 'tomorrow', mode='before')
    @classmethod
    def _coerce_decimal(cls, v):
        if v is None or v == '':
            return None
        return Decimal(str(v))

    @model_validator(mode='after')
    def _one_day(self) -> ManualPoultryRateUpsertRequest:
        if self.today is None and self.yesterday is None and self.tomorrow is None:
            raise ValueError('Provide at least one of today, yesterday, tomorrow')
        return self


class ManualPoultryRateUpsertResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    product: str
    zone: str
    source: str = 'manual'
    today: str
    yesterday: str | None
    tomorrow: str | None
    today_source_date: str | None = None
    yesterday_source_date: str | None = None
    today_carried: bool = False
    yesterday_carried: bool = False
    today_editable: bool = True
    yesterday_editable: bool = True
    tomorrow_editable: bool = True
    zones_updated: int = 1
    apply_all_zones: bool = False


class ManualPoultryRateReadResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    product: str
    zone: str
    source: str = 'manual'
    today: str | None
    yesterday: str | None
    tomorrow: str | None
    today_source_date: str | None = None
    yesterday_source_date: str | None = None
    today_carried: bool = False
    yesterday_carried: bool = False
    today_editable: bool = True
    yesterday_editable: bool = True
    tomorrow_editable: bool = True


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


class MarketPoultrySheetResponse(BaseModel):
    """Published manual rate days for one hub (exact rows only)."""

    model_config = ConfigDict(extra='forbid')

    product: str
    zone: str
    year: int
    month: int
    today: str | None
    yesterday: str | None
    tomorrow: str | None
    month_average: str | None
    days: list[MarketDayPrice]
