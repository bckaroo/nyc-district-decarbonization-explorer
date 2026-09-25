# LL84 Pilot Snapshot Profile (CY2024, Midtown Core bbox)

- Dataset `5zyy-y8am` — <https://data.cityofnewyork.us/resource/5zyy-y8am>
- Publisher: NYC Mayor's Office of Climate & Environmental Justice (via NYC Open Data)
- License: NYC Open Data Terms of Use
- Dataset: NYC Building Energy and Water Data Disclosure for Local Law 84 2023 to Present (Data for Calendar Year 2022-Present)
- Bbox: Midtown Core, Manhattan: lat [40.748, 40.765], lon [-73.988, -73.970]; contiguous rectangle spanning ~Bryant Park/Grand Central/Chrysler/ESB-adjacent core.
- Build mode: **offline**; raw snapshot is immutable and never rewritten.
- Captured (UTC): 2026-09-24T15:45:20Z
- SHA256 (raw.jsonl): `bdf65a8dbd7fc318fcde3f5f073a3ba4a11c4735fd01203e95e4bf48863e184a`

## Query

```soql
$where: report_year = '2024' AND latitude BETWEEN '40.748' AND '40.765' AND longitude BETWEEN '-73.988' AND '-73.970'
$fields: property_id, parent_property_id, report_year, year_ending, nyc_borough_block_and_lot, nyc_building_identification, address_1, postal_code, latitude, longitude, property_gfa_self_reported, site_eui_kbtu_ft, weather_normalized_site_eui, direct_ghg_emissions_metric, direct_ghg_emissions_intensity, total_location_based_ghg, electricity_use_grid_purchase, natural_gas_use_kbtu, water_use_all_water_sources
$limit: 10000 (pagination: offset-page loop, count(*)-reconciled)
```

Count query URL: `https://data.cityofnewyork.us/resource/5zyy-y8am.json?%24select=count%28%2A%29+as+n&%24where=report_year+%3D+%272024%27+AND+latitude+BETWEEN+%2740.748%27+AND+%2740.765%27+AND+longitude+BETWEEN+%27-73.988%27+AND+%27-73.970%27`

Unit note: metric tons CO2e (tCO2e) per source metadata

## Counts

| metric | value |
|---|---|
| Count-reconciled rows | 1096 |
| Distinct property IDs | 1096 |
| Duplicate property IDs | 0 |
| Rows == distinct properties? | true |
| Distinct canonical BBLs (union of all tokens) | 913 |
| BBL rows unparsed | 29 |
| BBL rows multi-valued | 10 |
| Distinct BINs (7-digit tokens, zeros kept) | 984 |
| Rows with multi-BIN strings | 40 |

Note: row count ≠ distinct property count by design — campuses and multi-BBL/BIN properties are preserved as-is; rows are NOT collapsed to distinct properties and campuses are never summed as a verified total here.

## Parent / child flags

| metric | value |
|---|---|
| Parent properties | 26 |
| Child properties | 89 |
| Standalone properties | 981 |
| Orphan children | 11 |
| Missing parent IDs | 8 |


Classification note: Via signalnyc.ingest.quality.campus_accounting: records whose parent_property_id equals their own property_id are classified as parents (not children); check adds to rows.

Orphan children (child points at parent not in cohort):
- child `2649545` → parent `26708346`
- child `2671366` → parent `15667692`
- child `2693044` → parent `56973493`
- child `2781114` → parent `33152689`
- child `2781115` → parent `33152689`
- child `15569048` → parent `59240792`
- child `20277545` → parent `25764370`
- child `22164413` → parent `22164271`
- child `54045494` → parent `54045098`
- child `54045495` → parent `54045098`

Missing parent IDs (referenced but absent from cohort) — full list:
- `15667692`
- `22164271`
- `25764370`
- `26708346`
- `33152689`
- `54045098`
- `56973493`
- `59240792`

## Per-metric completeness

| metric | label | total | non-null | null/blank/NA | explicit zero | % non-null |
|---|---|---|---|---|---|---|
| property_gfa_self_reported | Property GFA - Self-Reported (ft²) | 1096 | 1096 | 0 | 0 | 100.0% |
| site_eui_kbtu_ft | Site EUI (kBtu/ft²) | 1096 | 1007 | 89 | 0 | 91.88% |
| weather_normalized_site_eui | Weather Normalized Site EUI (kBtu/ft²) | 1096 | 969 | 127 | 0 | 88.41% |
| direct_ghg_emissions_metric | Direct GHG Emissions (Metric Tons CO2e) | 1096 | 843 | 253 | 56 | 76.92% |
| direct_ghg_emissions_intensity | Direct GHG Emissions Intensity (kgCO2e/ft²) | 1096 | 843 | 253 | 79 | 76.92% |
| total_location_based_ghg | Total (Location-Based) GHG Emissions (Metric Tons CO2e) | 1096 | 1007 | 89 | 0 | 91.88% |
| electricity_use_grid_purchase | Electricity Use - Grid Purchase (kBtu) | 1096 | 1021 | 75 | 0 | 93.16% |
| natural_gas_use_kbtu | Natural Gas Use (kBtu) | 1096 | 834 | 262 | 59 | 76.09% |
| water_use_all_water_sources | Water Use (All Water Sources) (kgal) | 1096 | 645 | 451 | 1 | 58.85% |

## Campus budget boundary

- Aggregate status: **WITHHELD_UNRESOLVED** — campus totals WITHHELD.
- Orphan child count: 11
- Unresolved campus references in accounting module: 45
- Top-level-only GHG sum (DIAGNOSTIC ONLY, NOT a verified campus total): 2,123,193.6 metric tons CO2e
- Sum over every row incl. children (DIAGNOSTIC: risk of double counting when campus parents already include children; NOT an upper bound): 2,123,193.6 metric tons CO2e

## Snapshot files

- `ll84_2024_midtown_core` raw snapshot: `data/snapshots/ll84_2024_midtown_core.raw.jsonl` (immutable)
- Manifest: `data/snapshots/ll84_2024_midtown_core.manifest.json`

## Reproduction

```bash
.venv/bin/python3 scripts/build_pilot.py           # offline (default)
.venv/bin/python3 scripts/build_pilot.py --online        # fresh bounded fetch
```

No joins, no legal-screening, no frontend, no compliance math. This is a bounded pilot snapshot + diagnostics only.
