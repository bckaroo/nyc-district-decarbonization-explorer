"""Typed response models. All missing source values are explicit `null`."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    detail: str


class SnapshotInfo(BaseModel):
    snapshot_utc: str | None = None
    sha256: str | None = None
    rows: int
    slice_name: str | None = None
    socrata_dataset: str | None = None


class Counters(BaseModel):
    total: int
    with_coordinates: int
    has_eui: int
    has_ghg: int
    has_gas: int
    has_electricity: int
    multi_bin_properties: int


class PropertySummary(BaseModel):
    property_id: str
    address_1: str | None
    bbl: str | None
    bin: str | None
    bin_count: int
    latitude: float | None
    longitude: float | None
    gfa_sqft: float | None
    site_eui_kbtu_ft: float | None
    total_ghg_tco2e: float | None
    electricity_kbtu: float | None
    natural_gas_kbtu: float | None
    missing_fields: list[str]


class PropertyDetail(PropertySummary):
    parent_property_id: str | None
    postal_code: str | None
    borough_block_lot_raw: str | None
    report_year: str | None
    weather_normalized_site_eui: float | None
    direct_ghg_tco2e: float | None


class SearchResponse(BaseModel):
    query: str | None
    missing_field: str | None
    total: int
    returned: int
    offset: int
    limit: int
    properties: list[PropertySummary]
