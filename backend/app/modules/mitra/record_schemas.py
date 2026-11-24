"""Per-type daily / batch payloads (workbook-derived, validated in code only)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.modules.mitra.constants import FarmUnitType


class _DailyBase(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

    age_days: int | None = Field(default=None, ge=0, le=2000)
    opening_birds: int | None = Field(default=None, ge=0)
    mortality: int | None = Field(default=None, ge=0)
    closing_birds: int | None = Field(default=None, ge=0)
    feed_kg: float | None = Field(default=None, ge=0)
    feed_g_per_bird: float | None = Field(default=None, ge=0)
    remarks: str | None = Field(default=None, max_length=2000)


class BroilerLikeDailyRecord(_DailyBase):
    birds_lifted: int | None = Field(default=None, ge=0)
    kg_lifted: float | None = Field(default=None, ge=0)
    avg_weight_kg: float | None = Field(default=None, ge=0)
    std_weight_kg: float | None = Field(default=None, ge=0)
    weight_vs_std: float | None = None
    cum_feed_kg: float | None = Field(default=None, ge=0)
    fcr: float | None = Field(default=None, ge=0)
    cum_mortality_pct: float | None = Field(default=None, ge=0, le=100)


class PulletDailyRecord(BroilerLikeDailyRecord):
    culls_removed: int | None = Field(default=None, ge=0)
    sold_or_moved: int | None = Field(default=None, ge=0)
    std_feed_g: float | None = Field(default=None, ge=0)
    uniformity_pct: float | None = Field(default=None, ge=0, le=100)


class LayerLikeDailyRecord(_DailyBase):
    culls_removed: int | None = Field(default=None, ge=0)
    egg_numbers: int | None = Field(default=None, ge=0)
    egg_trays: float | None = Field(default=None, ge=0)
    lay_percent: float | None = Field(default=None, ge=0, le=100)
    broken_eggs: int | None = Field(default=None, ge=0)
    egg_weight_g: float | None = Field(default=None, ge=0)
    production_pct: float | None = Field(default=None, ge=0, le=100)


class BreederDailyRecord(_DailyBase):
    hens: int | None = Field(default=None, ge=0)
    cocks: int | None = Field(default=None, ge=0)
    hen_mortality: int | None = Field(default=None, ge=0)
    cock_mortality: int | None = Field(default=None, ge=0)
    total_eggs: int | None = Field(default=None, ge=0)
    hatching_eggs: int | None = Field(default=None, ge=0)
    rejects: int | None = Field(default=None, ge=0)
    hatching_egg_pct: float | None = Field(default=None, ge=0, le=100)
    hen_day_production_pct: float | None = Field(default=None, ge=0, le=100)
    feed_hens_kg: float | None = Field(default=None, ge=0)
    feed_cocks_kg: float | None = Field(default=None, ge=0)
    cock_hen_ratio: float | None = Field(default=None, ge=0)


class HatcheryBatchRecord(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

    batch_code: str = Field(min_length=1, max_length=64)
    source_farm: str | None = Field(default=None, max_length=200)
    set_date: str | None = Field(default=None, max_length=10)
    eggs_set: int | None = Field(default=None, ge=0)
    candling_date: str | None = Field(default=None, max_length=10)
    candling_loss: int | None = Field(default=None, ge=0)
    hatch_date: str | None = Field(default=None, max_length=10)
    fertility_pct: float | None = Field(default=None, ge=0, le=100)
    chicks_hatched: int | None = Field(default=None, ge=0)
    grade_a: int | None = Field(default=None, ge=0)
    grade_b: int | None = Field(default=None, ge=0)
    cull_chicks: int | None = Field(default=None, ge=0)
    remarks: str | None = Field(default=None, max_length=2000)


_DAILY_MODELS: dict[FarmUnitType, type[BaseModel]] = {
    FarmUnitType.BROILER: BroilerLikeDailyRecord,
    FarmUnitType.KADAKNATH: BroilerLikeDailyRecord,
    FarmUnitType.DUCK_MEAT: BroilerLikeDailyRecord,
    FarmUnitType.DESI: BroilerLikeDailyRecord,
    FarmUnitType.TURKEY: BroilerLikeDailyRecord,
    FarmUnitType.PULLET: PulletDailyRecord,
    FarmUnitType.LAYER: LayerLikeDailyRecord,
    FarmUnitType.DUCK_LAYER: LayerLikeDailyRecord,
    FarmUnitType.QUAIL: LayerLikeDailyRecord,
    FarmUnitType.BREEDER: BreederDailyRecord,
}


def validate_daily_data(farm_type: FarmUnitType, data: dict[str, Any]) -> dict[str, Any]:
    model = _DAILY_MODELS.get(farm_type)
    if model is None:
        raise ValueError(f'daily records are not used for farm type {farm_type.value}')
    return model.model_validate(data).model_dump(mode='json')


def validate_hatchery_batch(data: dict[str, Any]) -> dict[str, Any]:
    return HatcheryBatchRecord.model_validate(data).model_dump(mode='json')
