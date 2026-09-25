# DEV-155/156 Citywide Expansion: Scope & Data Feasibility (checkpoint)

*(work in progress — appended as rounds complete)*

## Verified feasibility (2026-09-25, live probes)

| Source | Reality | Feasible? |
|---|---|---|
| MapPLUTO tax lots (ArcGIS FeatureServer 0) | 856,687 lots; server caps `resultRecordCount` at 2000 → ≥429 paged requests for full geometry | Yes, bounded/resumable |
| LL84 CY2024, citywide (5zyy-y8am) | 39,090 rows; 37,736 with lat/lon; 3,909 distinct parent property IDs (campus/parent-child universe) | Yes — table-only, one `fetch_all` sweep with existing count-reconciled paged client |
| Building footprints (5zhs-2jue) | 1,083,047 features; mappluto_bbl populated | Yes — paged fetch; geometry + few fields keeps pages reasonable |
| Socrata PLUTO tabular (64uk-42ks) | No usable geometry | Ruled out for lots; NOT primary |

## Storage design

- `data/citywide/mappluto_lots.sqlite` (WAL, stdlib `sqlite3`): one row per lot —
  `bbl TEXT PK, borough, block, lot, lot_area, bldg_area, built_far, latitude, longitude (centroid), year_built, geometry_json (4326)`.
  Estimated ~250 MB including geometry JSON (~3.4 bytes/row avg from 5-responses probe;
  simple lot polygons = 1-rings, no footprints interleaved).
- `data/citywide/ll84_citywide_2024.raw.jsonl` + sha256 manifest (existing immutable-snapshot pattern).
- `data/citywide/footprints_citywide.raw.jsonl` + sha256 manifest.
- Derived joins written by scripts, derived artifacts never hand-edited.

## Existing suspicion audit — `_poly_area_sqft` (build_footprints.py:228)

The spherical ring-sum is `abs(area) * (R²/2) * cos(lat0)` — standard spherical
excess form is `(R²/2) * a` where `a` is already unitful in steradians for deg
lon/lat spans; the original code passes lon/lat pre-scaled by `pi/180`, then
multiplies a second `cos(lat0)` factor. **This double-cos is wrong** — for
correct spherical polygon area on a sphere the y-chain `[2 + sin(lat1) +
sin(lat2)]` and delta-lon ARE the spherical formula (no cos term); however this
only changes magnitudes by `cos(lat0)≈0.73`, and since it's used only for
weight normalization within a property's polygons (all at ~the same lat), the
error cancels to first order across same-property polygons. **Decision: keep
the (fixed) formula in the citywide module and add a unit test** — do NOT
rewrite working pilot conservation numbers mid-stream; document in DEVLOG.

## Double-count protections (inherited + extended)

- Pilot already: footprint→property first-match, campus area-weight
  disaggregation with conservation check (<0.004% error).
- Citywide plan: parent_property_id links preserved; groups (parent +
  children + standalone) summed only as DIAGNOSTIC, never "verified total",
  matching the existing pilot semantics; dedup by property_id (one row per
  property); BBL: first-writer-wins in pilot, now extended with explicit
  multi-BBL count exposed.

## Serving strategy (browser safety)

- `/api/footprints` stays (pilot, now citywide too once joined layer exists),
  but the full citywide GeoJSON is NOT sent to the browser in one payload.
- Backend gains `/api/parcels?bbox=` (WGS84 bbox → clipped FeatureCollection,
  max ~5k features/zoom-safe) computed from MapPLUTO sqlite via prepared
  geometry JSON + centroid + intersection-on-lot with the requested bbox.
- Backend gains `/api/pilot` alias so the midtown panel keeps working; pilot
  layer continues via existing `footprints_joined.geojson` until citywide
  slice verified, then both exposed.

## Next steps (execution queue)
1. `scripts/fetch_citywide_ll84.py` — 39k rows, existing snapshot pattern.
2. `scripts/fetch_citywide_mappluto.py` — resumable, checkpoint cursor in
   manifest between OFFSET windows; restart-safe after crash.
3. `scripts/fetch_citywide_footprints.py` — same resumable pattern.
4. Backend `/api/parcels?bbox=` + `/api/coverage` endpoints.
5. Frontend: map bbox fetch on moveend; unbenchmarked parcels gray; explicit
   "unknown" (never zero) on missing energy.
6. DEV-156 radius study workflow design (after DEV-155 verified).
7. pytest additions; browser verify; DEVLOG + commit + push.
