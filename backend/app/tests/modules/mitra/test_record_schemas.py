from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.mitra.constants import FarmUnitType
from app.modules.mitra.record_schemas import validate_daily_data, validate_hatchery_batch


def test_broiler_daily_rejects_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        validate_daily_data(FarmUnitType.BROILER, {'fcr': 1.2, 'not_a_column': 1})


def test_hatchery_batch_requires_batch_code() -> None:
    with pytest.raises(ValidationError):
        validate_hatchery_batch({'eggs_set': 100})


def test_layer_accepts_egg_fields() -> None:
    data = validate_daily_data(FarmUnitType.LAYER, {'egg_numbers': 500, 'lay_percent': 88.5})
    assert data['egg_numbers'] == 500
