#!/usr/bin/env python3
"""Citywide LL84 CY2024 snapshot: bounded paged fetch with count reconciliation.

Reuses the project's immutable-snapshot pattern: one raw.jsonl + sha256
manifest; count(*) verified against paged rows; no assumption of any cap.

Run: .venv/bin/python3 scripts/fetch_citywide_ll84.py
"""
from __future__ import annotations
import os, sys
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src"))
from nyc_decarbonization.ingest.snapshots import snapshot_dataset

FID = "5zyy-y8am"
WHERE = "report_year = '2024'"
FIELDS = [
    "property_id", "parent_property_id", "report_year", "year_ending",
    "nyc_borough_block_and_lot", "nyc_building_identification", "address_1",
    "postal_code", "latitude", "longitude", "property_gfa_self_reported",
    "site_eui_kbtu_ft", "weather_normalized_site_eui",
    "direct_ghg_emissions_metric", "direct_ghg_emissions_intensity",
    "total_location_based_ghg", "electricity_use_grid_purchase",
    "natural_gas_use_kbtu", "water_use_all_water_sources",
    # District/fossil fuels omitted by the first ingest — their absence made
    # steam-served Manhattan read as cooling-dominant (everything blue).
    "district_steam_use_kbtu", "district_hot_water_use_kbtu",
    "district_chilled_water_use",
    "fuel_oil_1_use_kbtu", "fuel_oil_2_use_kbtu", "fuel_oil_4_use_kbtu",
    "fuel_oil_5_6_use_kbtu", "diesel_2_use_kbtu", "propane_use_kbtu",
]
OUT = os.path.join(REPO, "data", "citywide")

if __name__ == "__main__":
    m = snapshot_dataset(OUT, FID, WHERE, "ll84_citywide_2024_v2", fields=FIELDS, page_size=10000)
    print("manifest:", m["captured_utc"], "rows:", m["rows"], "sha256:", m["sha256"])
