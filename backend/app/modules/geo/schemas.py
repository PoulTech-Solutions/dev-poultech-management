from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class GeoPincodeItem(BaseModel):
    model_config = ConfigDict(extra='forbid')

    pincode: str = Field(pattern=r'^[1-9][0-9]{5}$')
    state: str
    district: str
    tehsil: str | None = None
    label: str


class GeoPincodeSearchResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    items: list[GeoPincodeItem]
    query: str


class GeoNameItem(BaseModel):
    model_config = ConfigDict(extra='forbid')

    name: str = Field(min_length=1, max_length=100)


class GeoNameListResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    items: list[GeoNameItem]


class GeoOfficeItem(BaseModel):
    model_config = ConfigDict(extra='forbid')

    name: str
    pincode: str = Field(pattern=r'^[1-9][0-9]{5}$')
    taluk: str | None = None
    office_type: str | None = None
    label: str


class GeoOfficeListResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    items: list[GeoOfficeItem]
