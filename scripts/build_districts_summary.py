"""Build per-district portfolio summaries (BIDs + campuses).

For each of the 273 study districts, find member footprints by true
point-in-polygon (shapely STRtree over 1,083,047 footprint centroids, bbox
prefilter via the R-tree), then aggregate the portfolio table columns:

  buildings (count), ll84_props, gfa_total, median/mean site EUI,
  median net thermal, heating/cooling/DHW totals, evidence-tier counts,
  heating-dominant / cooling-dominant split.

Aggregate-only by design: no per-building rows leave here, and every stat
carries the property-vs-building grain caveat in the schema note.

Output: data/citywide/districts_summary.json — read by the API (/api/districts
table mode) and baked into the static export.
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
import time
from pathlib import Path

import shapely
from shapely.geometry import shape

ROOT = Path("/mnt/e/OC_Projects/projects/signalnyc")
FP_DB = ROOT / "data/citywide/footprints_citywide.sqlite"
DISTRICTS = ROOT / "src/nyc_decarbonization/api/districts.py"
OUT = ROOT / "data/citywide/districts_summary.json"

sys.path.insert(0, str(ROOT / "src"))
from nyc_decarbonization.api.districts import load_districts  # noqa: E402


def main() -> None:
    t0 = time.time()
    fc, meta = load_districts()
    print(f"districts loaded: {len(fc['features'])} ({time.time()-t0:.1f}s)")

    conn = sqlite3.connect(f"file:{FP_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    # footprint centroid + stats columns;几何 kept as WKB-ish via cx/cy columns.
    rows = conn.execute(
        """
        SELECT fid, bbl, name, cx, cy, height_roof, construction_year, has_ll84,
               site_eui_kbtu_ft, weather_normalized_site_eui,
               total_location_based_ghg, property_gfa_self_reported,
               space_heating_kbtu_ft2_yr, dhw_kbtu_ft2_yr,
               cooling_kbtu_ft2_yr, net_thermal_kbtu_ft2_yr, evidence_tier,
               min_x, min_y, max_x, max_y
        FROM footprints
        """
    )
    feats = rows.fetchall()
    print(f"footprints: {len(feats):,} ({time.time()-t0:.1f}s)")

    # STRtree over footprint CENTROIDS (cx, cy) — a district polygon contains a
    # building when its centroid falls inside (the same convention the map's
    # click-join uses). Geoms carry stats for later aggregation.
    from shapely import wkb  # noqa: F401  (not used, keep import surface honest)
    import shapely.geometry as sg

    pt_geoms = [sg.Point(r["cx"], r["cy"]) for r in feats]
    tree = shapely.STRtree(pt_geoms)
    print(f"STRtree built ({time.time()-t0:.1f}s)")

    # pre-extract arrays for speed
    stats_cols = (
        "site_eui_kbtu_ft", "weather_normalized_site_eui",
        "total_location_based_ghg", "property_gfa_self_reported",
        "space_heating_kbtu_ft2_yr", "dhw_kbtu_ft2_yr",
        "cooling_kbtu_ft2_yr", "net_thermal_kbtu_ft2_yr",
    )

    out_rows = []
    for i, feat in enumerate(fc["features"]):
        p = feat["properties"]
        poly = shape(feat["geometry"])
        # candidate via bbox of the polygon, then exact contains
        cand_idx = tree.query(poly)
        members = []
        if len(cand_idx):
            for j in cand_idx:
                if poly.contains(pt_geoms[int(j)]):
                    members.append(int(j))

        agg = {
            "members": len(members),
            "ll84_props": 0,
            "gfa_total": None,
            "site_eui_median": None,
            "site_eui_mean": None,
            "wn_eui_median": None,
            "net_thermal_median": None,
            "heating_kbtu_total": None,
            "cooling_kbtu_total": None,
            "dhw_kbtu_total": None,
            "tier1": 0, "tier2": 0, "tier4": 0,
            "heating_dominant": 0, "cooling_dominant": 0,
        }
        euis, wnis, nts = [], [], []
        heat_s = cool_s = dhw_s = 0.0
        gfa_s = 0.0
        for j in members:
            r = feats[j]
            if r["has_ll84"]:
                agg["ll84_props"] += 1
            for col, lst in (
                ("site_eui_kbtu_ft", euis),
                ("weather_normalized_site_eui", wnis),
                ("net_thermal_kbtu_ft2_yr", nts),
            ):
                if r[col] is not None:
                    lst.append(float(r[col]))
            for col, tot in (
                ("space_heating_kbtu_ft2_yr", "heat"),
                ("cooling_kbtu_ft2_yr", "cool"),
                ("dhw_kbtu_ft2_yr", "dhw"),
            ):
                v = r[col]
                if v is not None:
                    if tot == "heat":
                        heat_s += float(v)
                    elif tot == "cool":
                        cool_s += float(v)
                    else:
                        dhw_s += float(v)
            if r["property_gfa_self_reported"] is not None:
                gfa_s += float(r["property_gfa_self_reported"])
            tier = (r["evidence_tier"] or "")
            if tier.startswith("T1"):
                agg["tier1"] += 1
            elif tier.startswith("T2"):
                agg["tier2"] += 1
            elif tier.startswith("T4"):
                agg["tier4"] += 1
            ntv = r["net_thermal_kbtu_ft2_yr"]
            if ntv is not None:
                if ntv >= 0:
                    agg["heating_dominant"] += 1
                else:
                    agg["cooling_dominant"] += 1

        if euis:
            euis.sort(); wnis.sort()
            agg["site_eui_median"] = euis[len(euis)//2]
            agg["site_eui_mean"] = sum(euis) / len(euis)
        if wnis:
            wnis.sort()
            agg["wn_eui_median"] = wnis[len(wnis)//2]
        if nts:
            nts.sort()
            agg["net_thermal_median"] = nts[len(nts)//2]
        if heat_s: agg["heating_kbtu_total"] = round(heat_s)
        if cool_s: agg["cooling_kbtu_total"] = round(cool_s)
        if dhw_s: agg["dhw_kbtu_total"] = round(dhw_s)
        if gfa_s: agg["gfa_total"] = round(gfa_s)

        out_rows.append({
            "district_id": p["district_id"],
            "kind": p["kind"],
            "name": p.get("bid_name") or p.get("name") or p["district_id"],
            "borough": p.get("borough"),
            "year_found": p.get("year_found"),
            "website": p.get("website"),
            **agg,
        })
        if (i + 1) % 50 == 0:
            print(f"  … {i+1}/{len(fc['features'])} ({time.time()-t0:.1f}s)")

    payload = {
        "note": ("Per-district portfolio aggregates for the 273 study districts "
                 "(76 BIDs, 197 campuses). Membership = footprint centroid inside "
                 "the district polygon. BID/campus boundaries are administrative "
                 "study areas — NOT evidence of shared heating/cooling "
                 "infrastructure. EUI/GFA aggregates keep LL84's property-level "
                 "grain: a campus row may roll several buildings into one "
                 "reporting property. Thermal totals are modeled, not measured."),
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "districts": out_rows,
    }
    OUT.write_text(json.dumps(payload, separators=(",", ":")))
    print(f"wrote {OUT} ({OUT.stat().st_size/1e6:.1f} MB) in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
