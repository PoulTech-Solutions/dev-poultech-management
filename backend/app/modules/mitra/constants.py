from __future__ import annotations

from enum import StrEnum

MAX_FARM_UNITS_PER_OWNER = 10


class FarmUnitType(StrEnum):
    LAYER = 'layer'
    BROILER = 'broiler'
    PULLET = 'pullet'
    BREEDER = 'breeder'
    HATCHERY = 'hatchery'
    KADAKNATH = 'kadaknath'
    DUCK_LAYER = 'duck_layer'
    DUCK_MEAT = 'duck_meat'
    QUAIL = 'quail'
    TURKEY = 'turkey'
    DESI = 'desi'


DAILY_RECORD_TYPES: frozenset[FarmUnitType] = frozenset(
    t for t in FarmUnitType if t is not FarmUnitType.HATCHERY
)
