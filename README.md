# NYC District Decarbonization Explorer

A read-only screening tool over New York City building energy data. It maps every
DOB building footprint in the five boroughs, joins NYC LL84 benchmarking data where
a record exists, and adds an **evidence-tiered model of annual building demand**
(space heating, domestic hot water, cooling) for district-scale decarbonization
screening.

> **Not a compliance tool.** Nothing here is an LL97 assessment, a savings
> estimate, or an investment-grade analysis. See [Scope and limits](#scope-and-limits).

## What it shows

| Layer | Coverage | Nature |
|---|---|---|
| Building footprints | **1,083,047** (all five boroughs) | Observed — NYC DOB `5zhs-2jue` |
| MapPLUTO tax lots | **856,687** | Observed — NYC DCP |
| LL84 benchmarking join | **49,206** footprints | Observed — CY2024 `5zyy-y8am` |
| Modeled net thermal demand | **41,161** footprints | **Modeled, not measured** |
| District study boundaries | **273** (76 BIDs + 197 ownership clusters) | Derived boundaries, not thermal districts |

## The honesty rules this tool is built on

These are load-bearing, not decoration:

- **Modeled ≠ measured.** Annual end-use demand is derived from fixed archetype
  share parameters constrained by observed LL84 fuel totals. LL84 reports whole-
  property fuel and electricity, never end-use splits. Every modeled value carries
  an `evidence_tier`.
- **Null means "not modeled", never zero.** A missing value and a genuine zero are
  different facts and are never collapsed.
- **"Not required to report" ≠ "reported nothing."** LL84 covers only buildings
  above the benchmarking size threshold, so a low join rate is expected
  (`has_ll84=0`, not zero energy).
- **Districts are boundaries, not thermal systems.** A BID is administrative; a
  campus is a derived ownership cluster. Neither is evidence of shared heating or
  cooling infrastructure.

### Evidence tiers

| Tier | Count | Meaning |
|---|---|---|
| `T1_observed_full` | 25,645 | LL84 record; end-uses modeled from observed fuel |
| `T2_intensity_only` | 61 | Intensity only; no absolute totals modeled |
| `T4_footprint_numeric_only` | 830,981 | No LL84 record — present for coverage, end-uses **null** |

The conservation receipt (`implied_eta ≤ 1.0`) is asserted on every build: delivered
heat exceeding fuel input is impossible, and that invariant is what catches an
omitted heating fuel (steam, fuel oil) before it reaches the map.

## Running it

```bash
# API + built frontend on :3320
.venv/bin/python -m nyc_decarbonization.api --port 3320
```

Frontend development:

```bash
cd web && npm install && npm run dev     # dev server
cd web && npm run build                  # production bundle -> web/dist
```

### Environment variables

Primary names (legacy `SIGNALNYC_*` equivalents are still honoured):

| Variable | Purpose |
|---|---|
| `NYCDECO_SNAPSHOT` | LL84 raw snapshot path |
| `NYCDECO_MANIFEST` | Snapshot manifest path |
| `NYCDECO_WEB_DIST` | Built frontend directory |
| `NYCDECO_FOOTPRINTS` | Joined footprints GeoJSON |
| `NYCDECO_SCRATCH` | Native-disk staging dir for large builds |

## Building the data

Large builds stage on a **native** filesystem and publish by copy — the Windows
mount (`/mnt/*`) does buffered 4K writes at roughly 20× slower than native, which
turns a 90-second build into an hours-long one.

```bash
.venv/bin/python scripts/fetch_citywide_ll84.py       # LL84 CY2024, all boroughs
.venv/bin/python scripts/build_footprints_citywide.py # 1.08M footprints + R-tree
.venv/bin/python scripts/build_annual_demand_citywide.py  # modeled demand
.venv/bin/python scripts/fetch_bids.py               # 76 BID boundaries
.venv/bin/python scripts/fetch_colp.py && \
  .venv/bin/python scripts/build_campuses.py          # agency campuses
.venv/bin/python scripts/build_owner_clusters.py      # state/private clusters
```

Builds are **deterministic** (byte-identical reruns) and each writes a `manifest.json`
recording inputs, sha256s, and counts. A manifest is written *after* its database and
then **verified against it** — the pair is re-hashed and byte-counted, and the run
fails loudly on mismatch, because a stale manifest looks authoritative and is wrong.

## API

| Endpoint | Purpose |
|---|---|
| `/api/footprints/bbox?min_x=&min_y=&max_x=&max_y=&limit=` | Viewport query (R-tree indexed) |
| `/api/footprints/coverage` | Layer counts (served from the manifest) |
| `/api/building/{bbl}` | One building: identity, observed join, modeled demand |
| `/api/parcels/{bbl}` | Tax-lot detail |
| `/api/districts`, `/api/districts/{id}` | Study boundaries + member footprints |
| `/api/properties`, `/api/counters` | LL84 property table |

The map draws 1.08M footprints via **viewport refetching** (R-tree query, ~0.4s),
not a single response — 1.08M features cannot be one payload.

## Data sources

All public, no API key:

- **LL84 benchmarking** — `data.cityofnewyork.us/resource/5zyy-y8am.json`
- **DOB footprints** — `data.cityofnewyork.us/resource/5zhs-2jue.json`
- **MapPLUTO** — NYC DCP ArcGIS `MAPPLUTO/FeatureServer/0`
- **BIDs** — `data.cityofnewyork.us/resource/7jdm-inj8.json`
- **COLP** — `data.cityofnewyork.us/resource/fn4k-qyk2.json`
- **Basemap** — Esri World Dark Gray Canvas (key-free)

## Development

```bash
.venv/bin/python -m pytest tests/ -q     # 118 tests
cd web && npx tsc -b                     # typecheck
```

## Scope and limits

- Property-level reporting grain is preserved; a campus is one reporting property
  that may span multiple footprints. Assigning a property's EUI to an individual
  footprint does **not** establish per-building measured demand.
- Campus/boundary allocation is apportioned with explicit flags
  (`campus_apportioned`, `campus_group_n`, `campus_apportion_weight`); child
  records are withheld from aggregates rather than silently double-counted.
- Some ownership clusters over-merge (single-linkage through vacant parcels) — e.g.
  a 365-lot PARKS cluster averaging ~370 ft²/lot. Disclosed, not silently trimmed.
- One upstream LL84 outlier is carried as published (556 Fifth Ave reports a site EUI
  of 9,999.6 kBtu/ft² — 388,533,357 kBtu district steam on 39,000 ft²). Disclosed,
  not corrected; it is an upstream reporting error, not something this tool computed.
- The LL84↔parcel join is first-writer-wins per BBL where multiple contenders
  exist; `parent_property_id` is preserved for campus dedup.
- This tool does not model district energy systems, pipe networks, or costs.
