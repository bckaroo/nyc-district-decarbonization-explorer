#!/usr/bin/env python3
"""Ingest ALL citywide DOB building footprints into bbox-queryable SQLite.

Source: NYC Open Data Socrata `5zhs-2jue` (DOB BUILDING footprints), bulk
GeoJSON export (~1.08M features, ~350MB). Bulk export is used instead of
paginated fetches because 1M+ MultiPolygons paginated would be ~800MB of
requests and hours of round-trips; the export returns the same rows in one
streaming response.

Design mirrors mappluto_lots.sqlite (the existing citywide parcel store):
  * streaming parse via ijson so the 350MB file never loads whole
  * per-feature bbox columns + indexes so /api/footprints can serve a viewport
  * LL84 energy joined by BBL (first-BBL rule) and BIN fallback
  * geometry stored as compact GeoJSON text, exactly as source

IMPORTANT — this replaces the 932-feature Midtown pilot as the map layer. The
pilot only covered a small bbox; the map "not showing all the city" was that
literal limitation, not a rendering bug.

Run: .venv/bin/python scripts/build_footprints_citywide.py
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path

import ijson  # streaming JSON: the raw export never loads whole

REPO = Path(__file__).resolve().parent.parent
RAW = REPO / "data" / "citywide" / "footprints_citywide.raw.geojson"
LL84 = REPO / "data" / "citywide" / "ll84_citywide_2024_v2.raw.jsonl"
OUT = REPO / "data" / "citywide" / "footprints_citywide.sqlite"
MANIFEST = REPO / "data" / "citywide" / "footprints_citywide.manifest.json"
DEMAND = REPO / "data" / "citywide" / "annual_demand_citywide" / "annual_demand_citywide.sqlite"

# Build the SQLite on NATIVE disk, then copy the finished file to the repo.
# SQLite does many small synced random writes while inserting ~1M rows, and on
# the /mnt/* Windows mounts (9p/drvfs) those run ~20x slower than on native
# ext4 (measured: 11.3 MB/s vs 220 MB/s for 4K fsync writes). Building directly
# on /mnt/e decelerated from 5k rows/s to under 1k rows/s and would have taken
# hours; staging natively is minutes. The repo copy is the deliverable.
SCRATCH = Path(
    os.environ.get("SIGNALNYC_SCRATCH", "/home/abuck/.hermes/cache/scratch/signalnyc_build")
)
STAGE = SCRATCH / "footprints_citywide.sqlite"

# LL84 energy columns carried onto each footprint. All are kBtu except where
# noted; the unsuffixed electricity column is kBtu (its _1 sibling is kWh).
ENERGY_FIELDS = [
    "site_eui_kbtu_ft",
    "weather_normalized_site_eui",
    "total_location_based_ghg",
    "direct_ghg_emissions_intensity",
    "property_gfa_self_reported",
    "electricity_use_grid_purchase",
    "natural_gas_use_kbtu",
    "district_steam_use_kbtu",
    "district_hot_water_use_kbtu",
    "district_chilled_water_use",
    "fuel_oil_1_use_kbtu",
    "fuel_oil_2_use_kbtu",
    "fuel_oil_4_use_kbtu",
    "fuel_oil_5_6_use_kbtu",
    "diesel_2_use_kbtu",
    "propane_use_kbtu",
]

_NUM_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*(?:[eE][-+]?\d+)?")


def num(v):
    """LL84 ships 'Not Available' strings and comma-grouped numbers."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = _NUM_RE.search(str(v).replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group())
    except ValueError:
        return None


def first_bbl(raw) -> str | None:
    """One BBL from LL84's raw multi-valued field (plain 10-digit preferred)."""
    if raw is None:
        return None
    chosen = None
    for part in str(raw).replace(";", " ").split():
        part = part.strip()
        if part.isdigit() and len(part) == 10 and part[0] in "12345":
            return part
        if part.count(".") == 2:
            cand = "".join(part.split("."))
            if cand.isdigit() and len(cand) == 10 and cand[0] in "12345":
                chosen = cand
    return chosen


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_ll84() -> tuple[dict, dict, int]:
    """Index LL84 by BBL (first-writer-wins) and by BIN for fallback."""
    by_bbl: dict[str, dict] = {}
    by_bin: dict[str, dict] = {}
    rows = 0
    with LL84.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            rows += 1
            rec = {"property_id": r.get("property_id")}
            for k in ENERGY_FIELDS:
                rec[k] = num(r.get(k))
            bbl = first_bbl(r.get("nyc_borough_block_and_lot"))
            if bbl and bbl not in by_bbl:
                by_bbl[bbl] = rec
            binv = num(r.get("nyc_building_identification"))
            if binv:
                bink = f"{int(binv)}"
                if bink not in by_bin:
                    by_bin[bink] = rec
    return by_bbl, by_bin, rows


SCHEMA = """
CREATE TABLE IF NOT EXISTS footprints (
    fid INTEGER PRIMARY KEY,
    bin TEXT,
    bbl TEXT,
    doitt_id TEXT,
    shape_area REAL,
    height_roof REAL,
    construction_year INTEGER,
    feature_code TEXT,
    name TEXT,
    min_x REAL, min_y REAL, max_x REAL, max_y REAL,
    cx REAL, cy REAL,
    geom TEXT,
    has_ll84 INTEGER DEFAULT 0,
    -- LL84 joined energy (property-level; see notes in the manifest)
    site_eui_kbtu_ft REAL,
    weather_normalized_site_eui REAL,
    total_location_based_ghg REAL,
    direct_ghg_emissions_intensity REAL,
    property_gfa_self_reported REAL,
    electricity_use_grid_purchase REAL,
    natural_gas_use_kbtu REAL,
    district_steam_use_kbtu REAL,
    district_hot_water_use_kbtu REAL,
    district_chilled_water_use REAL,
    fuel_oil_1_use_kbtu REAL,
    fuel_oil_2_use_kbtu REAL,
    fuel_oil_4_use_kbtu REAL,
    fuel_oil_5_6_use_kbtu REAL,
    diesel_2_use_kbtu REAL,
    propane_use_kbtu REAL,
    ll84_property_id TEXT,
    -- modeled annual demand (NULL where not modeled — never zero-filled)
    space_heating_kbtu REAL,
    dhw_kbtu REAL,
    cooling_kbtu REAL,
    space_heating_kbtu_ft2_yr REAL,
    dhw_kbtu_ft2_yr REAL,
    cooling_kbtu_ft2_yr REAL,
    net_thermal_kbtu_ft2_yr REAL,
    evidence_tier TEXT,
    archetype TEXT
);
-- The bbox search itself is served by the fp_rtree virtual table (created
-- after load, below); it indexes both axes, which a plain B-tree cannot.
CREATE INDEX IF NOT EXISTS idx_fp_bbl ON footprints(bbl);
CREATE INDEX IF NOT EXISTS idx_fp_bin ON footprints(bin);
"""


def rings_of(geom: dict) -> list[list[list[float]]]:
    """All coordinate rings (each a list of [x, y] points) at any nesting depth.

    Written as a plain recursive descent that identifies a RING as "a list
    whose entries are point pairs", rather than branching on Polygon-vs-
    MultiPolygon nesting shape. That shape-branching version crashed on a
    coordinate that was not a list: ijson yields Decimal unless use_float=True,
    and the old test walked into a scalar and tried to index it.
    """
    cs = geom.get("coordinates") or []
    if not cs:
        return []

    def is_point(p) -> bool:
        return (
            isinstance(p, (list, tuple))
            and len(p) >= 2
            and isinstance(p[0], (int, float))
            and isinstance(p[1], (int, float))
        )

    out: list[list[list[float]]] = []
    stack = [cs]
    while stack:
        cur = stack.pop()
        if not isinstance(cur, (list, tuple)) or not cur:
            continue
        if is_point(cur[0]):
            # every element is a point -> this list is a ring
            if all(is_point(p) for p in cur):
                out.append([list(p) for p in cur])
            continue
        stack.extend(cur)
    return out


def bbox_of(geom: dict):
    xs = []
    ys = []
    for ring in rings_of(geom):
        for p in ring:
            xs.append(p[0])
            ys.append(p[1])
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def acquire_lock() -> None:
    """Refuse to start if another build is already running.

    Two builds writing the same staged/served paths produced a
    `sqlite3.OperationalError: disk I/O error` at the final commit and left a
    half-written DB behind. A pid lockfile turns that silent corruption into an
    immediate, obvious refusal.
    """
    LOCK = SCRATCH / "build_footprints_citywide.lock"
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    if LOCK.exists():
        try:
            other = int(LOCK.read_text().strip())
        except ValueError:
            other = None
        if other and Path(f"/proc/{other}").exists():
            raise SystemExit(
                f"another build_footprints_citywide is running (pid {other}); "
                f"refusing to start a second one. Remove {LOCK} if that is stale."
            )
        print(f"  clearing stale lock (pid {other} gone)", flush=True)
    LOCK.write_text(str(os.getpid()))


def main() -> None:
    acquire_lock()
    if not RAW.exists():
        print(f"missing raw footprints: {RAW}", file=sys.stderr)
        sys.exit(1)

    t0 = time.time()
    print("loading LL84 citywide index…")
    by_bbl, by_bin, ll84_rows = load_ll84()
    print(f"  ll84 rows={ll84_rows} distinct_bbl={len(by_bbl)} distinct_bin={len(by_bin)}")

    # Modeled demand by BBL (optional — the map still works without it).
    demand: dict[str, tuple] = {}
    if DEMAND.exists():
        d = sqlite3.connect(f"file:{DEMAND}?mode=ro", uri=True)
        for row in d.execute(
            "SELECT bbl, space_heating_kbtu, dhw_kbtu, cooling_kbtu,"
            " space_heating_kbtu_ft2_yr, dhw_kbtu_ft2_yr, cooling_kbtu_ft2_yr,"
            " evidence_tier, archetype FROM annual_demand"
        ):
            demand[row[0]] = row[1:]
        d.close()
        print(f"  modeled demand rows: {len(demand)}")

    if STAGE.exists():
        STAGE.unlink()
    STAGE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(STAGE)
    conn.execute("PRAGMA journal_mode=MEMORY")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA cache_size=-300000")  # ~300MB page cache
    conn.executescript(SCHEMA)

    n = 0
    joined_bbl = 0
    joined_bin = 0
    no_geom = 0
    batch = []
    BATCH = 20000

    cols = (
        "bin", "bbl", "doitt_id", "shape_area", "height_roof",
        "construction_year", "feature_code", "name",
        "min_x", "min_y", "max_x", "max_y", "cx", "cy", "geom",
        "has_ll84", *ENERGY_FIELDS, "ll84_property_id",
        "space_heating_kbtu", "dhw_kbtu", "cooling_kbtu",
        "space_heating_kbtu_ft2_yr", "dhw_kbtu_ft2_yr", "cooling_kbtu_ft2_yr",
        "net_thermal_kbtu_ft2_yr", "evidence_tier", "archetype",
    )
    placeholders = ",".join("?" * len(cols))

    with RAW.open("rb") as fh:
        # use_float keeps coordinates as float (default is Decimal, which then
        # needs conversion everywhere and broke the ring walk).
        for feat in ijson.items(fh, "features.item", use_float=True):
            props = feat.get("properties") or {}
            geom = feat.get("geometry") or {}
            gtype = geom.get("type")
            if gtype not in ("Polygon", "MultiPolygon"):
                continue
            bb = bbox_of(geom)
            if bb is None:
                no_geom += 1
                continue
            n += 1
            bbl = (props.get("mappluto_bbl") or props.get("base_bbl") or "").strip() or None
            if bbl and (not bbl.isdigit() or len(bbl) != 10):
                bbl = None
            binv = props.get("bin")
            bink = f"{int(float(binv))}" if binv not in (None, "") else None

            rec = by_bbl.get(bbl) if bbl else None
            if rec is not None:
                joined_bbl += 1
            elif bink:
                rec = by_bin.get(bink)
                if rec is not None:
                    joined_bin += 1

            dem = demand.get(bbl) if bbl else None
            if dem:
                sh, dhw, cool, sh_i, dhw_i, cool_i, tier, arch = dem
                net = None
                if sh_i is not None and dhw_i is not None and cool_i is not None:
                    net = round(sh_i + dhw_i - cool_i, 2)
            else:
                sh = dhw = cool = sh_i = dhw_i = cool_i = None
                net = None
                tier = arch = None

            batch.append((
                bink, bbl,
                props.get("doitt_id") or props.get("objectid"),
                num(props.get("shape_area")),
                num(props.get("height_roof")),
                int(num(props.get("construction_year")) or 0) or None,
                props.get("feature_code"),
                props.get("name"),
                bb[0], bb[1], bb[2], bb[3],
                (bb[0] + bb[2]) / 2.0, (bb[1] + bb[3]) / 2.0,
                json.dumps(geom, separators=(",", ":")),
                1 if rec else 0,
                *[(rec or {}).get(k) for k in ENERGY_FIELDS],
                (rec or {}).get("property_id"),
                sh, dhw, cool, sh_i, dhw_i, cool_i, net, tier, arch,
            ))
            if len(batch) >= BATCH:
                conn.executemany(f"INSERT INTO footprints ({','.join(cols)}) VALUES ({placeholders})", batch)
                conn.commit()
                batch.clear()
                print(f"  … {n:,} features ({time.time()-t0:.0f}s)", flush=True)

    if batch:
        conn.executemany(f"INSERT INTO footprints ({','.join(cols)}) VALUES ({placeholders})", batch)
        conn.commit()
        batch.clear()

    # Spatial index. Built AFTER the load: populating an rtree row-by-row during
    # the insert loop is far slower than one bulk INSERT..SELECT, and the rtree
    # indexes BOTH axes so a viewport query is a real 2-D range search.
    # Without it SQLite can only seek one axis and must fetch every row sharing
    # that range to test the other in-row (~19 s per Midtown viewport on the
    # /mnt/e mount, vs ~0 s with the rtree).
    print("  building rtree spatial index…", flush=True)
    t_idx = time.time()
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS fp_rtree "
        "USING rtree(fid, min_x, max_x, min_y, max_y)"
    )
    conn.execute("DELETE FROM fp_rtree")
    conn.execute(
        "INSERT INTO fp_rtree(fid, min_x, max_x, min_y, max_y) "
        "SELECT fid, min_x, max_x, min_y, max_y FROM footprints"
    )
    conn.commit()
    print(
        f"  rtree indexed {conn.execute('SELECT COUNT(*) FROM fp_rtree').fetchone()[0]:,}"
        f" in {time.time()-t_idx:.1f}s",
        flush=True,
    )
    # With MEMORY journaling there is nothing to checkpoint; just switch the
    # staged file to the default rollback journal so the published copy is a
    # single self-contained file with no sidecars.
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.commit()
    conn.close()

    # Publish: copy the finished, checkpointed DB into the repo.
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if OUT.exists():
        OUT.unlink()
    shutil.copyfile(STAGE, OUT)
    print(f"published {OUT} ({OUT.stat().st_size/1e6:.1f} MB)", flush=True)

    # BBL/BIN join-rate audit. LL84 covers ~1% of DOB footprints by design
    # (only large buildings report), so a low rate is expected — record it so
    # the number is explicit rather than surprising.
    chk = sqlite3.connect(f"file:{OUT}?mode=ro", uri=True)
    total = chk.execute("SELECT COUNT(*) FROM footprints").fetchone()[0]
    with_ll = chk.execute("SELECT COUNT(*) FROM footprints WHERE has_ll84=1").fetchone()[0]
    with_dem = chk.execute("SELECT COUNT(*) FROM footprints WHERE net_thermal_kbtu_ft2_yr IS NOT NULL").fetchone()[0]
    chk.close()

    manifest = {
        "source": "NYC Open Data Socrata 5zhs-2jue (DOB BUILDING footprints), bulk GeoJSON export",
        "raw_file": RAW.name,
        "raw_bytes": RAW.stat().st_size,
        "raw_sha256": sha256_file(RAW),
        "features_ingested": total,
        "skipped_no_geom": no_geom,
        "ll84_snapshot": LL84.name,
        "ll84_sha256": sha256_file(LL84),
        "ll84_rows": ll84_rows,
        "ll84_distinct_bbl": len(by_bbl),
        "ll84_distinct_bin": len(by_bin),
        "join": {
            "by_bbl": joined_bbl,
            "by_bin": joined_bin,
            "total_with_ll84": with_ll,
            "join_rate_pct": round(100.0 * with_ll / total, 2) if total else None,
            "note": (
                "LL84 benchmarks buildings above the LL97/benchmarking size threshold, "
                "so most DOB footprints have no energy record by design. has_ll84=0 is "
                "'not required to report', NOT 'zero energy'."
            ),
        },
        "modeled_demand": {
            "source": DEMAND.name if DEMAND.exists() else None,
            "bbls_with_demand": len(demand),
            "footprints_with_net_thermal": with_dem,
            "note": "modeled, not measured; NULLs preserved as NULL",
        },
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "generation_seconds": round(time.time() - t0, 1),
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2))
    print(json.dumps({k: manifest[k] for k in
                      ("features_ingested", "join", "modeled_demand", "generation_seconds")}, indent=2))
    print(f"wrote {OUT} ({OUT.stat().st_size/1e6:.1f} MB)")


if __name__ == "__main__":
    main()
