#!/usr/bin/env python3
"""DEV-160: citywide annual building-level end-use demand (additive split v1.0).

Extends the DEV-159 pilot (scripts/build_annual_demand.py) citywide using the
completed citywide stores WITHOUT re-fetching anything:
  LL84 CY2024:  data/citywide/ll84_citywide_2024.raw.jsonl (39,090 rows)
  MapPLUTO:     data/citywide/mappluto_lots.sqlite      (856,687 lots, geom+bldg_area)

Per-lot model = SAME model_one() arithmetic as the pilot (same params file,
imported verbatim): space_heating / dhw / cooling kBtu (+ per-ft²-yr) with
evidence_tier  T1_observed_full | T2_intensity_only | T3_archetype_prior |
T4_footprint_numeric_only, plus ll84_bbl (the exact join key ParcelStore's
/api/parcels serves) and citywide coverage flags.

Campus / parent-child accounting (pilot logic at citywide grain): the LL84
property row is mapped first-writer-wins per BBL (ParcelStore convention) onto
the whole lot polygon. Where one parent property maps to several lots, each
lot is flagged (campus_apportioned=1, campus_group_n, campus_apportion_weight)
and its observed FUEL/GFA inputs are scaled by bldg_area share within the
parent's mapped lots; Site EUI (a per-ft² intensity) intentionally stays
property-level. Children/unmapped Campus area is a recorded coverage gap.

Tier guardrail (no synthesis): T3 archetype intensity priors exist in params
but are deliberately NOT applied citywide in v1 — that would paint hundreds of
thousands of lots with invented energy. Lots without an LL84 row are T4 with
null end-uses, never zero. Only T1/T2 lots carry end-use values; citywide
end-use totals are therefore LL84-covered totals, never citywide totals.

Outputs (geometry stays in mappluto_lots.sqlite — not duplicated here):
  data/citywide/annual_demand_citywide/annual_demand_citywide.sqlite
      (table annual_demand, bbl PK + tier/borough/bbox indexes)
  data/citywide/annual_demand_citywide/manifest.json
Terminal print: counts, conservation receipt, borough distributions.

Optional: a .parquet mirror is written when pandas+pyarrow are importable. It is
NOT a dependency, so on a venv without pandas the mirror is skipped and the
manifest records status "skipped" rather than implying the file exists. Nothing
in the app reads it — the SQLite is the served artifact.
Run: .venv/bin/python3 scripts/build_annual_demand_citywide.py
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import statistics
import sys
import importlib.util
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_pilot_spec = importlib.util.spec_from_file_location(
    "build_annual_demand", REPO / "scripts" / "build_annual_demand.py")
_pilot_spec.check = None  # type: ignore[attr-defined]
pilot = importlib.util.module_from_spec(_pilot_spec)
_pilot_spec.loader.exec_module(pilot)  # type: ignore[union-attr]

PARAMS = pilot.PARAMS
MODEL_VERSION = f"{pilot.MODEL_VERSION} (citywide per-lot)"
ETA = pilot.ETA
ENDUSES = ("space_heating", "dhw", "cooling")
# LL84 observed inputs the model consumes (pilot keys). MUST include every
# heating-bearing fuel: when this list carried only gas + electricity, the
# citywide model reproduced the pilot's all-blue bug (steam-served buildings
# read as heating-free). See pilot.model_one for the unit/basis rules.
OBS_KEYS = (
    "property_gfa_self_reported",
    "site_eui_kbtu_ft",
    "natural_gas_use_kbtu",
    "electricity_use_grid_purchase",
    "district_steam_use_kbtu",
    "district_hot_water_use_kbtu",
    "district_chilled_water_use",
    "fuel_oil_1_use_kbtu",
    "fuel_oil_2_use_kbtu",
    "fuel_oil_4_use_kbtu",
    "fuel_oil_5_6_use_kbtu",
    "diesel_2_use_kbtu",
    "propane_use_kbtu",
)
# Area-scaled inputs under campus apportionment; EUI stays unscaled (per-ft²).
# Every fuel total scales with floor area, so each must be apportioned too —
# scaling only gas/electricity would under-count apportioned campuses.
AREA_SCALED_KEYS = (
    "property_gfa_self_reported",
    "natural_gas_use_kbtu",
    "electricity_use_grid_purchase",
    "district_steam_use_kbtu",
    "district_hot_water_use_kbtu",
    "district_chilled_water_use",
    "fuel_oil_1_use_kbtu",
    "fuel_oil_2_use_kbtu",
    "fuel_oil_4_use_kbtu",
    "fuel_oil_5_6_use_kbtu",
    "diesel_2_use_kbtu",
    "propane_use_kbtu",
)

LL84_ROWS = REPO / "data" / "citywide" / "ll84_citywide_2024_v2.raw.jsonl"
LL84_MANIFEST = REPO / "data" / "citywide" / "ll84_citywide_2024_v2.manifest.json"
LOT_DB = REPO / "data" / "citywide" / "mappluto_lots.sqlite"
OUT_DIR = REPO / "data" / "citywide" / "annual_demand_citywide"
OUT_SQLITE = OUT_DIR / "annual_demand_citywide.sqlite"
OUT_PARQUET = OUT_DIR / "annual_demand_citywide.parquet"
OUT_MANIFEST = OUT_DIR / "manifest.json"

# Build on native disk then copy: ~857k inserts of small synced random writes
# are ~20x slower on the /mnt/* Windows mounts (measured 11.3 vs 220 MB/s for
# 4K fsync), which turns a minutes-long build into hours.
SCRATCH = Path(
    os.environ.get("NYCDECO_SCRATCH") or os.environ.get("SIGNALNYC_SCRATCH", "/home/abuck/.hermes/cache/scratch/signalnyc_build")
)
STAGE_SQLITE = SCRATCH / "annual_demand_citywide.sqlite"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def first_bbl(raw) -> str | None:
    """Identical semantics to ParcelStore._first_bbl (the citywide join key)."""
    if raw is None:
        return None
    text = str(raw).replace(";", " ")
    for part in text.split():
        part = part.strip()
        if part.isdigit() and len(part) == 10 and part[0] in "12345":
            return part
        if part.count(".") == 2:
            cand = "".join(part.split("."))
            if cand.isdigit() and len(cand) == 10 and cand[0] in "12345":
                return cand
    return None


def load_landuse() -> dict[str, str]:
    con = sqlite3.connect(f"file:{LOT_DB}?mode=ro", uri=True)
    try:
        return {str(b): str(lu) for b, lu in con.execute(
            "SELECT bbl, land_use FROM lots WHERE land_use IS NOT NULL")}
    finally:
        con.close()


def publish_and_write_manifest(manifest: dict) -> int:
    """Write the manifest, then verify it actually describes the DB on disk.

    An interrupted run can publish the SQLite and die before the manifest is
    written, leaving a manifest whose sha256/byte-count describe a DIFFERENT
    database. That happened here: the served DB was sha 9f9dc678…/T1 25645 while
    the manifest beside it claimed 7ab58754…/T1 25616. Nothing detected it until
    a human compared the numbers.

    Writing the manifest last is unavoidable (two files cannot be swapped
    atomically), so instead of pretending otherwise this VERIFIES the pair and
    fails loudly.
    """
    OUT_MANIFEST.write_text(json.dumps(manifest, indent=2))

    db_sha = sha256_file(OUT_SQLITE)
    claimed = manifest["outputs"]["sqlite"]["sha256"]
    ok_sha = db_sha == claimed
    ok_bytes = OUT_SQLITE.stat().st_size == manifest["outputs"]["sqlite"]["bytes"]
    if not (ok_sha and ok_bytes):
        print(
            "ERROR: manifest does not describe the published database "
            f"(db sha256 {db_sha} != manifest {claimed}); the pair is inconsistent.",
            file=sys.stderr,
        )
        return 1
    print(f"  manifest verified against db (sha256 {db_sha[:12]}…)", flush=True)
    return 0


def acquire_lock() -> None:
    """Refuse to start if another demand build is live.

    Concurrent runs share OUT_SQLITE and OUT_MANIFEST; the loser can leave a
    manifest and a database that describe different data.
    """
    LOCK = SCRATCH / "build_annual_demand_citywide.lock"
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    if LOCK.exists():
        try:
            other = int(LOCK.read_text().strip())
        except ValueError:
            other = None
        if other and Path(f"/proc/{other}").exists():
            raise SystemExit(
                f"another build_annual_demand_citywide is running (pid {other}); "
                f"refusing to start a second one. Remove {LOCK} if that is stale."
            )
        print(f"  clearing stale lock (pid {other} gone)", flush=True)
    LOCK.write_text(str(os.getpid()))


def release_lock() -> None:
    """Remove the lock on a clean finish.

    Only if it still names THIS process: a stale lock is auto-cleared on the next
    run, but leaving one behind after a successful exit makes `ls *.lock` look
    like a build is in flight when nothing is.
    """
    LOCK = SCRATCH / "build_annual_demand_citywide.lock"
    try:
        if LOCK.exists() and LOCK.read_text().strip() == str(os.getpid()):
            LOCK.unlink()
    except OSError:
        pass


def main() -> None:
    acquire_lock()
    # Invalidate any existing manifest up front: if this run is interrupted after
    # publishing the DB, a MISSING manifest is honest and obvious, whereas a
    # stale one looks authoritative and is wrong.
    if OUT_MANIFEST.exists():
        OUT_MANIFEST.unlink()
    landuse = load_landuse()

    # ---- LL84 citywide: keep raw rows grouped by parent (campus groups)
    ll84_rows = 0
    rows_by_bbl: dict[str, dict] = {}
    row_by_property: dict[str, dict] = {}
    parent_of: dict[str, str] = {}
    with LL84_ROWS.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            ll84_rows += 1
            pid = str(raw.get("property_id") or "")
            if pid and pid not in row_by_property:
                row_by_property[pid] = raw
            bbl = first_bbl(raw.get("nyc_borough_block_and_lot"))
            if not bbl or bbl in rows_by_bbl:
                continue
            parent = str(raw.get("parent_property_id") or "")
            parent_of[bbl] = parent
            rows_by_bbl[bbl] = raw

    # ---- MapPLUTO lots (already complete; no fetch)
    con = sqlite3.connect(f"file:{LOT_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    lots = con.execute(
        "SELECT bbl, borough, block, lot, latitude, longitude, land_use, bldg_area"
        " FROM lots WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
    ).fetchall()
    con.close()
    bldg_area_by_bbl: dict[str, float] = {}
    for r in lots:
        a = r["bldg_area"]
        if a is not None and a > 0:
            bldg_area_by_bbl[str(r["bbl"])] = float(a)

    # ---- campus groups: within each parent, lots with fuel-bearing property
    #      rows AND mapped building area share the parent's mapped-lot area.
    mapped_of_parent: dict[str, list[str]] = defaultdict(list)
    for bbl in rows_by_bbl:
        if bbl in bldg_area_by_bbl:
            mapped_of_parent[parent_of.get(bbl, bbl)].append(bbl)
    weight_by_bbl: dict[str, tuple[float, int]] = {}
    apportion_groups = 0
    apportioned_lots = 0
    for parent, members in mapped_of_parent.items():
        if len(members) < 2:
            continue
        tot = sum(bldg_area_by_bbl[m] for m in members)
        if tot <= 0:
            continue
        apportion_groups += 1
        for m in members:
            weight_by_bbl[m] = (bldg_area_by_bbl[m] / tot, len(members))
            apportioned_lots += 1
    # uncovered campus area (mapped LL84 children absent from MapPLUTO bldg_area)
    _w_as_bbl = set(weight_by_bbl)
    unmapped_campus_bbls = [
        b for b in rows_by_bbl
        if b not in weight_by_bbl and str(parent_of.get(b, "")).strip()
        not in ("", "Not Applicable: Standalone Property")
    ]

    # ---- Effective per-lot model inputs (apportionment-scaled where flagged)
    def effective_inputs(bbl: str) -> tuple[dict, int, int, float | None]:
        raw = rows_by_bbl.get(bbl)
        w = weight_by_bbl.get(bbl, (None, None))[0]
        eff: dict = {"bbl": bbl}
        for k in OBS_KEYS:
            eff[k] = pilot.num(raw.get(k)) if raw else None
        if raw:
            ptxt = str(raw.get("parent_property_id") or "")
            parent_present = bool(ptxt.strip()) and ptxt != "Not Applicable: Standalone Property"
        else:
            parent_present = False
        if w is not None:
            for k in AREA_SCALED_KEYS:
                if eff[k] is not None:
                    eff[k] = round(eff[k] * w, 1)
        eff["bldg_area_sqft"] = bldg_area_by_bbl.get(bbl)
        return eff, 1 if parent_present else 0, 2 if w is not None else 0, w

    # ---- write sqlite (canonical)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    STAGE_SQLITE.parent.mkdir(parents=True, exist_ok=True)
    if STAGE_SQLITE.exists():
        STAGE_SQLITE.unlink()
    out = sqlite3.connect(STAGE_SQLITE)
    # Bulk-load pragmas: this is a disposable build we checkpoint before
    # publishing, so durability during the run is not needed, and NORMAL sync
    # plus a large page cache cuts the small-write cost dramatically.
    out.execute("PRAGMA journal_mode=MEMORY")
    out.execute("PRAGMA synchronous=OFF")
    out.execute("PRAGMA cache_size=-200000")  # ~200MB page cache
    out.execute("""CREATE TABLE annual_demand (
        bbl TEXT PRIMARY KEY,
        borough TEXT, block TEXT, lot TEXT,
        latitude REAL, longitude REAL, land_use TEXT, bldg_area_sqft REAL,
        space_heating_kbtu REAL, dhw_kbtu REAL, cooling_kbtu REAL,
        space_heating_kbtu_ft2_yr REAL, dhw_kbtu_ft2_yr REAL,
        cooling_kbtu_ft2_yr REAL,
        evidence_tier TEXT, archetype TEXT,
        method_heating TEXT, method_cooling TEXT, model_version TEXT,
        ll84_bbl TEXT, ll84_parent_property_id TEXT,
        ll84_gfa_sqft REAL, ll84_site_eui_kbtu_ft REAL,
        ll84_natural_gas_kbtu REAL, ll84_electricity_kbtu REAL,
        campus_apportioned INTEGER, campus_group_n INTEGER,
        campus_apportion_weight REAL)""")
    out.execute("CREATE INDEX idx_annual_tier ON annual_demand(evidence_tier)")
    out.execute("CREATE INDEX idx_annual_boro ON annual_demand(borough)")
    out.execute("CREATE INDEX idx_annual_latlon ON annual_demand(latitude, longitude)")

    tier_count: Counter[str] = Counter()
    borough_agg: dict[str, tuple[Counter, Counter]] = {}
    intens: dict[str, list[float]] = {}
    cons_gas = cons_alloc = 0.0
    inserted = 0
    for r in lots:
        bbl = str(r["bbl"])
        raw = rows_by_bbl.get(bbl)
        w, group_n = weight_by_bbl.get(bbl, (None, None))
        eff, _p, campus_apportioned, _x = effective_inputs(bbl)
        m = pilot.model_one(eff, landuse)
        # per-ft2 intensities: gfa in eff is already the apportion-scaled item
        gfa = eff.get("property_gfa_self_reported")
        if gfa:
            for e in ENDUSES:
                v = m[f"{e}_kbtu"]
                m[f"{e}_kbtu_ft2_yr"] = round(v / gfa, 2) if v is not None else None
        # Conservation receipt (T1). The denominator must sum EVERY
        # heating-bearing fuel the model consumed — using gas alone produced
        # implied_eta > 1.0 (delivered heat exceeding fuel input), which is how
        # the omitted-fuels bug was caught in the pilot. District chilled water
        # is excluded: it is cooling, not heating.
        if m["evidence_tier"] == "T1_observed_full":
            fuel_in = 0.0
            for k in ("natural_gas_use_kbtu", "district_steam_use_kbtu",
                      "district_hot_water_use_kbtu", "fuel_oil_1_use_kbtu",
                      "fuel_oil_2_use_kbtu", "fuel_oil_4_use_kbtu",
                      "fuel_oil_5_6_use_kbtu", "diesel_2_use_kbtu",
                      "propane_use_kbtu"):
                v = eff.get(k)
                if v and v > 0:
                    fuel_in += v
            if fuel_in > 0:
                cons_gas += fuel_in
                cons_alloc += (m["space_heating_kbtu"] or 0) + (m["dhw_kbtu"] or 0)
        tier_count[m["evidence_tier"]] += 1
        b = str(r["borough"] or "?")
        c_ends, c_tiers = borough_agg.setdefault(b, (Counter(), Counter()))
        c_tiers[m["evidence_tier"]] += 1
        for e in ENDUSES:
            v = m[f"{e}_kbtu"]
            if v is not None:
                c_ends[f"{e}_kbtu"] += v
                key = f"{e}_kbtu_ft2_yr"
                iv = m.get(key)
                if iv is not None:
                    intens.setdefault(key, []).append(iv)
        parent_txt = str((raw or {}).get("parent_property_id") or "")
        out.execute(
            "INSERT INTO annual_demand VALUES (" + ",".join("?" * 28) + ")",
            (bbl, r["borough"], r["block"], r["lot"], r["latitude"], r["longitude"],
             r["land_use"], eff.get("bldg_area_sqft"),
             m["space_heating_kbtu"], m["dhw_kbtu"], m["cooling_kbtu"],
             m.get("space_heating_kbtu_ft2_yr"), m.get("dhw_kbtu_ft2_yr"),
             m.get("cooling_kbtu_ft2_yr"),
             m["evidence_tier"], m["archetype"],
             m["method_heating"], m["method_cooling"], m["model_version"],
             bbl if raw else None,
             parent_txt if (raw and parent_txt.strip()
                            and parent_txt != "Not Applicable: Standalone Property") else None,
             eff.get("property_gfa_self_reported"), eff.get("site_eui_kbtu_ft"),
             eff.get("natural_gas_use_kbtu"), eff.get("electricity_use_grid_purchase"),
             1 if campus_apportioned else 0, group_n, w),
        )
        inserted += 1
        # Commit periodically: this loop inserts ~857k rows, and holding them
        # all in one transaction means any interruption discards the entire
        # build (which happened — a mid-run kill left an empty table). Batched
        # commits keep completed work.
        if inserted % 25000 == 0:
            out.commit()
            print(f"  … {inserted:,} lots modeled", flush=True)
    out.commit()
    out.execute("PRAGMA journal_mode=DELETE")
    out.commit()
    out.close()

    # Publish the checkpointed DB into the repo.
    if OUT_SQLITE.exists():
        OUT_SQLITE.unlink()
    shutil.copyfile(STAGE_SQLITE, OUT_SQLITE)
    print(f"published {OUT_SQLITE} ({OUT_SQLITE.stat().st_size/1e6:.1f} MB)", flush=True)

    # ---- parquet mirror (canonical is sqlite)
    parquet_status = "skipped"
    try:
        import pandas as pd
        con = sqlite3.connect(f"file:{OUT_SQLITE}?mode=ro", uri=True)
        try:
            frame = pd.read_sql_query("SELECT * FROM annual_demand", con)
        finally:
            con.close()
        frame.to_parquet(OUT_PARQUET, index=False)
        parquet_status = f"written ({OUT_PARQUET.stat().st_size} bytes)"
    except Exception as e:
        parquet_status = f"skipped ({type(e).__name__}: {e})"

    meds = {}
    for key, vals in intens.items():
        vals.sort()
        meds[key] = {
            "n": len(vals),
            "median": round(statistics.median(vals), 2) if vals else None,
            "p10": round(vals[len(vals) // 10], 2) if vals else None,
            "p90": round(vals[(len(vals) * 9) // 10], 2) if vals else None,
        }

    lots_with_ll84 = sum(1 for r in lots if str(r["bbl"]) in rows_by_bbl)
    # Built once and reused in the manifest (see the note there on why the set
    # must not be rebuilt inside the comprehension).
    lot_bbl_set = {str(r["bbl"]) for r in lots}
    manifest = {
        "model_version": MODEL_VERSION,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "inputs": {
            "ll84_citywide_file": LL84_ROWS.name,
            "ll84_citywide_sha256": sha256_file(LL84_ROWS),
            "ll84_manifest_sha256": sha256_file(LL84_MANIFEST) if LL84_MANIFEST.exists() else None,
            "mappluto_lots_sqlite": LOT_DB.name,
            "mappluto_sqlite_sha256": sha256_file(LOT_DB),
            "params_sha256": sha256_file(REPO / "scripts" / "annual_demand_params.json"),
            "citywide_footprint_geojson": (
                "data/citywide/footprints_citywide.raw.geojson (DOB 5zhs-2jue bulk "
                "export) is ingested into footprints_citywide.sqlite and served by "
                "/api/footprints/bbox; MapPLUTO lot polygons (856,687) remain the "
                "separate per-lot layer served by /api/parcels."
            ),
        },
        "counts": {
            "lots_total": len(lots),
            "lots_with_ll84": lots_with_ll84,
            "ll84_rows_total": ll84_rows,
            "ll84_distinct_bbl": len(rows_by_bbl),
            # Hoist the lot-BBL set OUT of the comprehension. As written
            # (`sum(1 for b in rows_by_bbl if b in {str(r["bbl"]) for r in lots})`)
            # the set was rebuilt for every one of the ~26k keys, i.e. ~22 billion
            # operations: measured 0.255s/key => ~111 minutes, which is why a
            # build that had already published its database then sat at 100% CPU
            # for half an hour instead of writing its manifest.
            "ll84_bbl_mapped_to_lots": sum(
                1 for b in rows_by_bbl if b in lot_bbl_set
            ),
            "lots_with_bldg_area": len(bldg_area_by_bbl),
            "campus_apportion_groups": apportion_groups,
            "campus_apportioned_lots": apportioned_lots,
            "campus_unmapped_child_bbls": len(unmapped_campus_bbls),
        },
        "counts_by_tier": {k: tier_count[k] for k in sorted(tier_count)},
        "aggregate_conservation_check": {
            "note": ("T1 rows: Σ allocated (space_heating + DHW) = Σ (apportionment-scaled) "
                     "observed_gas × 1.0 × η=0.80, by algebraic construction (shares sum to "
                     "1.0). Same receipt semantics as the pilot manifest."),
            "observed_gas_kbtu_t1": round(cons_gas, 1),
            "allocated_heat_plus_dhw_kbtu_t1": round(cons_alloc, 1),
            "implied_eta": round(cons_alloc / cons_gas, 4) if cons_gas else None,
            "as_expected": bool(cons_gas) and abs((cons_alloc / cons_gas) - ETA) < 0.001,
        },
        "intensity_distributions_kbtu_ft2_yr": meds,
        "end_use_totals_kbtu_by_borough": {
            b: {k: round(v, 1) for k, v in sorted(c[0].items())}
            for b, c in sorted(borough_agg.items())},
        "tiers_by_borough": {b: dict(c[1]) for b, c in sorted(borough_agg.items())},
        "outputs": {
            "sqlite": {"file": OUT_SQLITE.name, "sha256": sha256_file(OUT_SQLITE),
                       "bytes": OUT_SQLITE.stat().st_size, "rows": inserted},
            "parquet": {"file": OUT_PARQUET.name, "status": parquet_status},
            "geometry_note": "lot geometry remains in mappluto_lots.sqlite (served by /api/parcels) — not duplicated here",
        },
        "no_synthesis_rule": ("T3 archetype intensity priors are NOT applied. Lots without an "
                              "LL84 row are T4 with null end-uses. Citywide end-use totals are "
                              "LL84-covered totals, explicitly NOT citywide totals."),
        "honest_limits": PARAMS["limits_and_caveats"],
        "shares_parameterized_replacement_note": PARAMS["notes"],
    }
    # The verifier writes the manifest itself, so there is no separate write.
    rc = publish_and_write_manifest(manifest)
    release_lock()
    if rc != 0:
        raise SystemExit(rc)
    print(json.dumps({
        "rows": inserted,
        "counts": manifest["counts"],
        "tiers": dict(manifest["counts_by_tier"]),
        "conservation_implied_eta": manifest["aggregate_conservation_check"]["implied_eta"],
        "conservation_as_expected": manifest["aggregate_conservation_check"]["as_expected"],
        "medians": meds,
        "parquet": parquet_status,
        "tiers_by_borough": manifest["tiers_by_borough"],
    }, indent=2))


if __name__ == "__main__":
    main()
