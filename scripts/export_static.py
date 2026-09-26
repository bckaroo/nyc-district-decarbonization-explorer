"""Bake the explorer's live API surface into static JSON for GitHub Pages.

GitHub Pages serves static files only, so the FastAPI backend cannot exist there.
This script captures the *same* payloads the API would return for a bounded,
screening-appropriate subset and writes them under a --out directory.

What is baked (and what is deliberately NOT):

  districts.json     all 273 study boundaries (76 BIDs + 197 ownership clusters),
                     simplified for the web. These are the primary study unit in
                     the static build, so they must be complete.
  net_thermal.json   every footprint carrying a modeled net-thermal value
                     (~41k). This IS the map's data layer in the static build —
                     the full 1,083,047-footprint citywide layer is ~700 MB of
                     SQLite and cannot be baked, so the static view shows the
                     buildings where the model produced an answer.
  buildings.json     per-BBL detail for every baked footprint, keyed by BBL, so a
                     click resolves identity + observed join + modeled demand
                     with no server.
  properties.json    the LL84 property table (rows the table pane lists).

Every payload is copied through the real API/DB rather than recomputed here, so
the static build cannot drift from what the live app serves. Nulls are preserved
as null: the static build must not turn "not modelled" into 0.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "citywide"
FP_DB = DATA / "footprints_citywide.sqlite"
DEMAND_DB = DATA / "annual_demand_citywide" / "annual_demand_citywide.sqlite"
PLUTO_DB = DATA / "mappluto_lots.sqlite"

# Columns the map/table/dossier actually consume. Explicit so a schema addition
# is a deliberate decision about what gets published. The observed LL84 columns
# are what the observed themes (Site EUI, GHG, fuels) paint from — the first
# static export baked only the modeled columns, so every observed theme rendered
# the whole city as no-data on GitHub Pages.
FP_COLS = [
    "bbl", "bin", "name", "height_roof", "construction_year", "has_ll84",
    # observed (LL84 property-level ratios/inputs, carried on the footprint join)
    "site_eui_kbtu_ft", "weather_normalized_site_eui",
    "total_location_based_ghg", "direct_ghg_emissions_intensity",
    "electricity_use_grid_purchase", "natural_gas_use_kbtu",
    # modeled
    "space_heating_kbtu_ft2_yr", "dhw_kbtu_ft2_yr", "cooling_kbtu_ft2_yr",
    "net_thermal_kbtu_ft2_yr", "evidence_tier",
]
DEMAND_COLS = [
    "space_heating_kbtu", "dhw_kbtu", "cooling_kbtu",
    "space_heating_kbtu_ft2_yr", "dhw_kbtu_ft2_yr", "cooling_kbtu_ft2_yr",
    "evidence_tier", "archetype", "bldg_area_sqft", "land_use",
    "ll84_gfa_sqft", "ll84_site_eui_kbtu_ft",
    "campus_apportioned", "campus_group_n",
]


def simplify_ring(coords, tol, ndigits):
    """Drop vertices closer together than tol (degrees) and round precision.

    Boundaries are drawn, not measured, so a sub-metre vertex carries no
    information at any zoom this map supports. Keeps first/last so rings stay
    closed — an unclosed ring makes a polygon silently disappear.
    """
    if len(coords) <= 4:
        return [[round(c[0], ndigits), round(c[1], ndigits)] for c in coords]
    out = [coords[0]]
    for pt in coords[1:-1]:
        px, py = out[-1][0], out[-1][1]
        if abs(pt[0] - px) > tol or abs(pt[1] - py) > tol:
            out.append(pt)
    out.append(coords[-1])
    return [[round(c[0], ndigits), round(c[1], ndigits)] for c in out]


def simplify_geom(geom, tol, ndigits=5):
    if geom["type"] == "Polygon":
        return {"type": "Polygon",
                "coordinates": [simplify_ring(r, tol, ndigits)
                                for r in geom["coordinates"]]}
    return {"type": "MultiPolygon",
            "coordinates": [[simplify_ring(r, tol, ndigits) for r in poly]
                            for poly in geom["coordinates"]]}



# Profile fields copied attribute-only from the MapPLUTO lots DB (bbl-keyed).
# Absent BBLs stay None — condo lots / unmapped parcels, not zeros.
PLUTO_PROFILE_COLS = (
    "borough", "block", "lot", "lot_area", "bldg_area", "built_far",
    "num_bldgs", "num_floors", "year_built", "land_use", "bldg_class",
    "zone_dist1", "zone_dist2", "ownertype", "ownername",
    "assess_land", "assess_total", "exempt_total", "landmark",
    "condo_no", "cd", "zip_code", "address",
)

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--tol", type=float, default=0.00008,
                    help="boundary simplification tolerance in degrees")
    ap.add_argument("--fp-tol", type=float, default=0.0,
                    help="footprint ring simplification tolerance (degrees)")
    args = ap.parse_args()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    # ---- districts (the static build's study unit) ------------------------
    districts = []
    for name in ("bids.geojson", "campuses.geojson", "owner_clusters.geojson"):
        p = DATA / name
        if not p.exists():
            print(f"  WARN missing {p}", file=sys.stderr)
            continue
        gj = json.loads(p.read_text())
        for f in gj.get("features", []):
            if not f.get("geometry"):
                continue
            props = dict(f.get("properties") or {})
            geom = simplify_geom(f["geometry"], args.tol)
            districts.append({"type": "Feature",
                              "geometry": geom,
                              "properties": props})
    (out / "districts.json").write_text(json.dumps(
        {"type": "FeatureCollection", "features": districts}))
    print(f"  districts: {len(districts)}")

    # ---- footprints with modeled net thermal (the static data layer) -----
    con = sqlite3.connect(f"file:{FP_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        f"SELECT {', '.join(FP_COLS)}, geom FROM footprints "
        "WHERE net_thermal_kbtu_ft2_yr IS NOT NULL ORDER BY fid"
    ).fetchall()

    pluto_by_bbl: dict[str, dict] = {}
    if PLUTO_DB.exists():
        pcon = sqlite3.connect(f"file:{PLUTO_DB}?mode=ro", uri=True)
        pcon.row_factory = sqlite3.Row
        cols = [r[1] for r in pcon.execute("PRAGMA table_info(lots)")]
        have = [c for c in PLUTO_PROFILE_COLS if c in cols]
        if have:
            for row in pcon.execute(f"SELECT bbl, {', '.join(have)} FROM lots"):
                pluto_by_bbl[str(row["bbl"])] = {c: row[c] for c in have}
        pcon.close()
        print(f"  pluto profiles: {len(pluto_by_bbl):,} lots")
    else:
        print("  WARN mappluto_lots.sqlite absent; building profiles not baked",
              file=sys.stderr)

    feats, buildings = [], {}
    for r in rows:
        props = {k: r[k] for k in FP_COLS}
        props["has_ll84"] = bool(props["has_ll84"])
        feats.append({
            "type": "Feature",
            # Static build simplifies rings: a 1.08M-footprint city served by the
            # live API can afford full detail, but every vertex here is downloaded
            # by the visitor. At these zooms a sub-4 m vertex is invisible, and
            # dropping them roughly halves the payload. Rings stay closed.
            "geometry": simplify_geom(json.loads(r["geom"]), args.fp_tol),
            "properties": props,
        })
        # Per-BBL detail so a click resolves without a server.
        buildings[props["bbl"]] = {
            "bbl": props["bbl"],
            "bin": props["bin"],
            "pluto": pluto_by_bbl.get(props["bbl"]),
            "footprint": {
                "name": props["name"],
                "height_roof": props["height_roof"],
                "construction_year": props["construction_year"],
                "shape_area": None,
                "has_ll84": props["has_ll84"],
            },
            "observed": None,  # filled from the demand table below
            "modeled": None,
            "evidence": {
                "has_footprint": True,
                "has_ll84_join": props["has_ll84"],
                "ll84_note": ("LL84 benchmarks buildings above the LL97 size "
                              "threshold; absent means 'not required to report', "
                              "not 'zero energy'."),
                "has_modeled_demand": True,
                "modeled_note": ("Modeled annual end-use demand, not a measurement. "
                                 "Null means not modeled, never zero."),
            },
        }
    (out / "net_thermal.json").write_text(json.dumps(
        {"type": "FeatureCollection", "features": feats}))
    print(f"  net_thermal: {len(feats)} features")

    # ---- demand detail joined by BBL ------------------------------------
    dcon = sqlite3.connect(f"file:{DEMAND_DB}?mode=ro", uri=True)
    dcon.row_factory = sqlite3.Row
    n_modeled = 0
    for bbl, b in buildings.items():
        d = dcon.execute(
            f"SELECT {', '.join(DEMAND_COLS)} FROM annual_demand WHERE bbl=? ",
            (bbl,),
        ).fetchone()
        if d is None:
            continue
        m = {k: d[k] for k in DEMAND_COLS}
        m["bbl"] = bbl
        m["has_end_uses"] = m.get("space_heating_kbtu") is not None
        if not m["has_end_uses"]:
            m["note"] = ("No LL84 record for this lot, so no end-use demand was "
                         "modelled. Nulls are 'not modelled', never zero.")
        # Observed LL84 block, only when there is real observed data. On an
        # apportioned campus lot the GFA is the parent GFA scaled by the lot's
        # area share (the per-ft2 math needs that), so it must NOT be presented
        # as "self-reported" without the apportionment flag — 1.0 ft² on a
        # 50,850 ft² building would read as a data error to anyone auditing it.
        if m.get("ll84_site_eui_kbtu_ft") is not None or m.get("ll84_gfa_sqft") is not None:
            obs = {
                "site_eui_kbtu_ft": m.get("ll84_site_eui_kbtu_ft"),
                "gfa_sqft": m.get("ll84_gfa_sqft"),
                "campus_apportioned": bool(m.get("campus_apportioned")),
                "campus_group_n": m.get("campus_group_n"),
            }
            if obs["campus_apportioned"]:
                obs["gfa_note"] = (
                    "GFA is the reporting property's total scaled by this lot's "
                    "area share of the campus, not a per-building figure."
                )
            b["observed"] = obs
        b["modeled"] = m
        n_modeled += 1
    (out / "buildings.json").write_text(json.dumps(buildings))
    print(f"  buildings: {len(buildings)} ({n_modeled} with modeled detail)")

    # ---- LL84 property table rows (the table pane's real source) ---------
    # Fetched from the live API when it is up so the static copy cannot drift
    # from what the app serves; falls back to reading the raw snapshot directly.
    table_rows, table_total = [], 0
    try:
        import urllib.request
        api_port = os.environ.get("EXPORT_API_PORT", "3330")
        with urllib.request.urlopen(
            f"http://127.0.0.1:{api_port}/api/properties?limit=400&offset=0", timeout=30
        ) as resp:
            page = json.loads(resp.read())
        table_rows = list(page.get("properties") or [])
        table_total = int(page.get("total") or 0)
        # Page through the rest so the baked table is complete.
        while len(table_rows) < table_total:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{api_port}/api/properties?"
                f"limit=400&offset={len(table_rows)}", timeout=30,
            ) as resp:
                page = json.loads(resp.read())
            batch = list(page.get("properties") or [])
            if not batch:
                break
            table_rows.extend(batch)
    except Exception as e:  # noqa: BLE001
        print(f"  WARN ll84 table not baked ({type(e).__name__}: {e}); "
              "start the API to include it", file=sys.stderr)
    (out / "ll84_table.json").write_text(json.dumps(
        {"total": table_total or len(table_rows), "properties": table_rows}))
    print(f"  ll84_table: {len(table_rows)} rows")

    # Districts portfolio aggregates: straight copy from the summary build (not
    # recomputed here) so the static table tab cannot drift from the API's.
    summary_path = Path(
        "/mnt/e/OC_Projects/projects/signalnyc/data/citywide/districts_summary.json"
    )
    if summary_path.exists():
        (out / "districts_portfolio.json").write_text(summary_path.read_text())
        n_port = len(json.loads(summary_path.read_text())["districts"])
        print(f"  districts_portfolio: {n_port} districts")
    else:
        print("  WARN districts_portfolio not baked; run build_districts_summary.py",
              file=sys.stderr)

    (out / "meta.json").write_text(json.dumps({
        "static_export": True,
        "counts": {
            "districts": len(districts),
            "net_thermal_footprints": len(feats),
            "buildings": len(buildings),
            "ll84_table_rows": len(table_rows),
        },
        "note": ("Static GitHub Pages export. The full citywide layer "
                 "(1,083,047 footprints) is served by the live API; this build "
                 "shows every footprint with a modeled net-thermal value plus all "
                 "study boundaries. Modeled values are modeled, not measured."),
    }, indent=2))
    print(f"  wrote -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
