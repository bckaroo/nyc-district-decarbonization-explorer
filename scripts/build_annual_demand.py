"""DEV-159 v1: annual building-level end-use demand model (pilot footprint set).

Reads data/snapshots/footprints_joined.geojson (already carries LL84 observed
fields + campus area-weighted disaggregation from build_pilot.py) plus
MapPLUTO landuse (data/citywide/mappluto_lots.sqlite) to derive:
  space_heating_kbtu, dhw_kbtu, cooling_kbtu (yearly),
  and intensity versions (kBtu/ft2/yr), conditioned area from MapPLUTO
  bldg_area (or LL84 GFA when building footprint area missing).

Method: additive end-use split model — observed fuels → fixed, cited,
parameterized shares (scripts/annual_demand_params.json) → delivered
end-uses (heating efficiency η=0.80, cooling COP=3.0). Every output row
carries evidence_tier + archetypes + method tags. Missing inputs → null,
never zero. No invented confidence intervals.

Outputs: data/citywide/annual_demand_pilot/annual_demand.geojson +
manifest.json (sha256 of geojson, counts, per-tier aggregates).

Run: python3 scripts/build_annual_demand.py [--offline]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PARAMS = json.loads((REPO / "scripts" / "annual_demand_params.json").read_text())
IN_GEOJSON = REPO / "data" / "snapshots" / "footprints_joined.geojson"
IN_SQLITE = REPO / "data" / "citywide" / "mappluto_lots.sqlite"
OUT_DIR = REPO / "data" / "citywide" / "annual_demand_pilot"
OUT_GEO = OUT_DIR / "annual_demand.geojson"
OUT_MANIFEST = OUT_DIR / "manifest.json"

MODEL_VERSION = PARAMS["model_version"]
ETA = PARAMS["heating_efficiency"]["value"]
COP = PARAMS["cooling_cop"]["value"]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_landuse_bbl() -> dict:
    """Map bbl -> MapPLUTO landuse code (1-11)."""
    out = {}
    if not IN_SQLITE.exists():
        return out
    con = sqlite3.connect(f"file:{IN_SQLITE}?mode=ro", uri=True)
    try:
        cur = con.execute(
            "SELECT bbl, land_use FROM lots WHERE land_use IS NOT NULL"
        )
        for bbl, lu in cur:
            if bbl:
                out[str(bbl)] = str(lu)
    finally:
        con.close()
    return out


def archetype_for(props: dict, landuse_map: dict) -> str:
    """Match the archetype bucket used in params, deterministic: bbl lookup (via
    lots.land_use / bldg_class twins — MapPLUTO stores BBL both bbl-clean and
    as the join key footprints use) > 'other' fallback."""
    bbl = str(props.get("bbl") or "")
    lu = landuse_map.get(bbl)
    if lu is None:
        lu = landuse_map.get(bbl.lstrip("0"))
    if lu:
        # MapPLUTO stores zero-padded codes ('05'); params keys are plain ints-as-strings
        return PARAMS["archetype_by_pluto_landuse"].get(str(int(lu)), "other")
    return "other"


def num(v):
    try:
        x = float(v)
        return x if x == x else None  # NaN guard
    except (TypeError, ValueError):
        return None


def model_one(props: dict, landuse_map: dict) -> dict:
    gfa = num(props.get("property_gfa_self_reported"))
    eui = num(props.get("site_eui_kbtu_ft"))
    gas = num(props.get("natural_gas_use_kbtu"))
    elec_kwh = num(props.get("electricity_use_grid_purchase"))  # LL84 field is kWh
    obs_gas = bool(gas and gas > 0)
    obs_elec = bool(elec_kwh and elec_kwh > 0)
    arch = archetype_for(props, landuse_map)

    tier = "T4_footprint_numeric_only"
    site_est = None
    if obs_gas or obs_elec:
        # Any metered fuel → T1 regardless of EUI presence (fuel end-uses usable)
        tier = "T1_observed_full"
    elif eui is not None and gfa is not None:
        site_est = eui * gfa
        tier = "T2_intensity_only"

    gs = PARAMS["gas_split_by_archetype"][arch]
    es = PARAMS["elec_split_by_archetype"][arch]

    modeled = {}
    if obs_gas and gas > 0:
        modeled["space_heat"] = gas * gs["space_heating"] * ETA
        modeled["dhw"] = gas * gs["dhw"] * ETA
        # declared end-use shares must sum to 1.0 per archetype; no residual bucket in v1
    elif site_est is not None:
        # Fall back to archetype fuel split when no gas metered; assume all else equal gas-only proxy
        modeled["space_heat"] = site_est * 0.70 * ETA
        modeled["dhw"] = site_est * 0.30 * ETA

    if obs_elec and elec_kwh > 0:
        # metered kWh → kBtu electric input (1 kWh = 3.412 kBtu); delivered cooling = input × COP
        modeled["cooling"] = elec_kwh * es["cooling"] * 3.412 * COP

    out = {
        "space_heating_kbtu": round(modeled["space_heat"], 1) if modeled.get("space_heat") is not None else None,
        "dhw_kbtu": round(modeled["dhw"], 1) if modeled.get("dhw") is not None else None,
        "cooling_kbtu": round(modeled["cooling"], 1) if modeled.get("cooling") is not None else None,
        "evidence_tier": tier,
        "archetype": arch,
        "method_heating": "gas_split×η" if obs_gas else ("intensity_prior×η" if site_est else None),
        "method_cooling": "elec×share×3.412kWh→kBtu" if obs_elec else None,
        "model_version": MODEL_VERSION,
    }
    if gfa:
        for key in ("space_heating", "dhw", "cooling"):
            v = out[f"{key}_kbtu"]
            out[f"{key}_kbtu_ft2_yr"] = round(v / gfa, 2) if v is not None else None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="no network (model is offline anyway)")
    ap.add_argument("--limit", type=int, default=None, help="cap features (debug)")
    args = ap.parse_args()

    landuse = load_landuse_bbl()
    feat_in = json.loads(IN_GEOJSON.read_text())["features"]
    if args.limit:
        feat_in = feat_in[: args.limit]

    feats_out = []
    tier_count = Counter()
    agg_heating = defaultdict(float)  # per-tier totals
    null_cooling = 0
    null_heating = 0
    missing_first_writer_notes = []
    for f in feat_in:
        p = dict(f["properties"])
        m = model_one(p, landuse)
        if m["space_heating_kbtu"] is None:
            null_heating += 1
        if m["cooling_kbtu"] is None:
            null_cooling += 1
        tier_count[m["evidence_tier"]] += 1
        for k in ("space_heating", "dhw", "cooling"):
            if m[f"{k}_kbtu"] is not None:
                agg_heating[f'{m["evidence_tier"]}:{k}'] += m[f"{k}_kbtu"]
        if p.get("_pilot_disagg_flagged"):
            missing_first_writer_notes.append(p.get("bbl"))
        f["properties"].update(m)
        feats_out.append(f)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    geojson = {"type": "FeatureCollection", "features": feats_out, "model_version": MODEL_VERSION}
    OUT_GEO.write_text(json.dumps(geojson))

    meds = {}
    for key in ("space_heating", "dhw", "cooling"):
        vals = [fo["properties"][f"{key}_kbtu_ft2_yr"]
                for fo in feats_out
                if fo["properties"].get(f"{key}_kbtu_ft2_yr") is not None]
        vals.sort()
        meds[key] = {
            "n": len(vals),
            "median": round(statistics.median(vals), 2) if vals else None,
            "p10": round(vals[len(vals) // 10], 2) if vals else None,
            "p90": round(vals[(len(vals) * 9) // 10], 2) if vals else None,
            "max": round(vals[-1], 2) if vals else None,
        }

    # Conservation check: per-property gas = sum of allocated end-uses is guaranteed
    # algebraically for T1 (shares sum to 1 × η). Record observed gas sum for tier T1
    # in the manifest for reproducible aggregate comparison (not cross-tier).
    gas_sum_t1 = 0.0
    alloc_sum_t1 = 0.0
    for fo in feats_out:
        p = fo["properties"]
        if p["evidence_tier"] == "T1_observed_full":
            g = num(p.get("natural_gas_use_kbtu"))
            if g and g > 0:
                gas_sum_t1 += g
                alloc_sum_t1 += (p["space_heating_kbtu"] or 0) + (p["dhw_kbtu"] or 0)

    manifest = {
        "model_version": MODEL_VERSION,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "inputs": {
            "footprints_geojson": str(IN_GEOJSON.name),
            "footprints_sha256": sha256_file(IN_GEOJSON),
            "ll84_snapshot": "data/snapshots/ll84_2024_midtown_core.raw.jsonl",
            "mappluto_sqlite": str(IN_SQLITE.relative_to(REPO)) if IN_SQLITE.exists() else None,
            "params_sha256": sha256_file(REPO / "scripts" / "annual_demand_params.json"),
        },
        "output": {
            "file": OUT_GEO.name,
            "features": len(feats_out),
            "sha256": sha256_file(OUT_GEO),
            "bytes": OUT_GEO.stat().st_size,
        },
        "counts_by_tier": {k: tier_count[k] for k in sorted(tier_count)},
        "aggregate_conservation_check": {
            "note": "T1 gas rows: Σ allocated (space_heating+DHW) = Σ observed_gas × (share_h+share_d=1.0) × η=0.80, i.e. exactly 0.80× observed, by algebraic construction. The 20% difference is real fuel-to-delivered-heat efficiency loss (stack/excess-air), intentionally NOT stored as an end-use bucket. Recorded as a reproducibility receipt and sanity check that shares+η were applied as parameterized; not a statistical finding.",
            "observed_gas_kbtu_t1": round(gas_sum_t1, 1),
            "allocated_heat_plus_dhw_kbtu_t1": round(alloc_sum_t1, 1),
            "implied_eta": round(alloc_sum_t1 / gas_sum_t1, 4) if gas_sum_t1 else None,
            "as_expected": abs((alloc_sum_t1 / gas_sum_t1) - ETA) < 0.001 if gas_sum_t1 else False,
        },
        "null_counts": {"heating": null_heating, "cooling": null_cooling},
        "intensity_distributions_kbtu_ft2_yr": meds,
        "aggregate_kbtu_by_tier": {k: round(v, 1) for k, v in sorted(agg_heating.items())},
        "honest_limits": PARAMS["limits_and_caveats"],
        "shares_parameterized_replacement_note": PARAMS["notes"],
    }
    OUT_MANIFEST.write_text(json.dumps(manifest, indent=2))
    print(json.dumps({
        "features": len(feats_out),
        "tiers": dict(manifest["counts_by_tier"]),
        "nulls": manifest["null_counts"],
        "medians": meds,
        "conservation_implied_eta": manifest["aggregate_conservation_check"]["implied_eta"],
        "conservation_as_expected": manifest["aggregate_conservation_check"]["as_expected"],
    }, indent=2))

# ------------------------------------------------------------------------------
# DEV-160: post-merge — write footprints WITH demand fields for /api/footprints.
# The served snapshot (footprints_joined_demand.geojson) = footprints_joined
# features plus the modeled demand fields from annual_demand.geojson, keyed by
# pid. Missing pid match → fields absent (not zero). Manifest records the merge.
MERGED_OUT = REPO / "data" / "snapshots" / "footprints_joined_demand.geojson"
DONOR_FIELDS = (
    "space_heating_kbtu", "dhw_kbtu", "cooling_kbtu",
    "space_heating_kbtu_ft2_yr", "dhw_kbtu_ft2_yr", "cooling_kbtu_ft2_yr",
    "evidence_tier", "archetype", "method_heating", "method_cooling",
    "model_version",
)


def merge_demand_into_footprints() -> dict:
    """Join demand donor fields onto footprints by pid; write merged snapshot."""
    fp = json.loads(IN_GEOJSON.read_text())
    dem = json.loads(OUT_GEO.read_text())
    by_pid = {
        f["properties"]["pid"]: f["properties"]
        for f in dem.get("features", [])
        if isinstance(f.get("properties", {}).get("pid"), str)
    }
    matched = 0
    merged_feats = []
    for f in fp.get("features", []):
        props = f.get("properties", {})
        donor = by_pid.get(props.get("pid"))
        if donor is None:
            props["net_thermal_kbtu_ft2_yr"] = None
            for key in DONOR_FIELDS:
                props[key] = None
        else:
            matched += 1
            for key in DONOR_FIELDS:
                props[key] = donor.get(key)
            # Net annual thermal demand = delivered heating + DHW − cooling
            # (kBtu/ft²·yr; >0 net heating demand, <0 net cooling demand).
            h, dh, c = (
                donor.get("space_heating_kbtu_ft2_yr"),
                donor.get("dhw_kbtu_ft2_yr"),
                donor.get("cooling_kbtu_ft2_yr"),
            )
            props["net_thermal_kbtu_ft2_yr"] = (
                round(h + dh - c, 2) if (h is not None and dh is not None and c is not None) else None
            )
        merged_feats.append({"type": "Feature", "geometry": f["geometry"], "properties": props})
    out = {"type": "FeatureCollection", "features": merged_feats, "model_version": MODEL_VERSION}
    MERGED_OUT.write_text(json.dumps(out))
    merged_manifest = {
        "base": IN_GEOJSON.name,
        "demand_source": str(OUT_GEO.relative_to(REPO)),
        "demand_source_sha256": sha256_file(OUT_GEO),
        "donor_fields": list(DONOR_FIELDS),
        "features": len(merged_feats),
        "matched_demand_pid": matched,
        "model_version": MODEL_VERSION,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sha256": sha256_file(MERGED_OUT),
        "bytes": MERGED_OUT.stat().st_size,
        "note": "Demand fields are MODELED/ESTIMATED (archetype shares constrained by LL84, eta=0.8 gas, COP cooling) — NOT measured. evidence_tier donor field labels each row's input quality.",
    }
    (REPO / "data" / "snapshots" / "footprints_joined_demand.manifest.json").write_text(
        json.dumps(merged_manifest, indent=2)
    )
    return merged_manifest


if __name__ == "__main__":
    main()
    m = merge_demand_into_footprints()
    net_vals = sorted(
        f["properties"]["net_thermal_kbtu_ft2_yr"]
        for f in json.loads(MERGED_OUT.read_text())["features"]
        if f["properties"].get("net_thermal_kbtu_ft2_yr") is not None
    )

    def _pct(q: float) -> float:
        i = q * (len(net_vals) - 1)
        lo = int(i)
        return net_vals[lo] + (net_vals[min(lo + 1, len(net_vals) - 1)] - net_vals[lo]) * (i - lo)

    print(json.dumps({
        "net_thermal_percentiles_kbtu_ft2_yr": {
            f"p{int(q*100)}": round(_pct(q), 1) for q in (0.05, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99)
        },
    }, indent=2))
    print(json.dumps({"merged_snapshot": {
        "features": m["features"], "matched_demand_pid": m["matched_demand_pid"],
        "sha256": m["sha256"], "bytes": m["bytes"],
    }}, indent=2))
