#!/usr/bin/env python3
"""Building-footprint join: footprints (5zhs-2jue) x LL84 energy -> GeoJSON layer.

Fetches all building-footprint polygons within the pilot bbox, joins them to
the LL84 CY2024 snapshot by canonical BBL (fallback: BIN), and writes
data/snapshots/footprints_joined.geojson for the /api/footprints map layer.

Modes:
  --offline (default): reuse data/snapshots/footprints_midtown_core.raw.json;
      fetch fresh footprints if the raw snapshot or manifest is missing.
  --refetch: force a fresh bounded fetch of footprints.

Join semantics (documented, honest):
- A footprint maps to at most ONE property (first matching property ID).
  For campus (multi-BIN) properties the polygon inherits the PROPERTY-level
  energy values — they are NOT per-building truth (LL84's grain). Callers
  must keep the existing campus disclosure when displaying these.
- Properties without any footprint match stay point-only on the map.
- Raw footprint snapshot is immutable once written; the joined GeoJSON is
  derived and always regenerated.

Run:  .venv/bin/python3 scripts/build_footprints.py [--refetch]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAP_DIR = os.path.join(REPO, "data", "snapshots")
LL84_SLICE = "ll84_2024_midtown_core"
FP_SLICE = "footprints_midtown_core"
OUT_GEOJSON = os.path.join(SNAP_DIR, "footprints_joined.geojson")

FP_FID = "5zhs-2jue"
FP_RESOURCE = f"https://data.cityofnewyork.us/resource/{FP_FID}.json"
FP_FIELDS = [
    "the_geom", "bin", "doitt_id", "shape_area", "base_bbl",
    "mappluto_bbl", "height_roof", "construction_year", "name", "feature_code",
]
# Same bbox as the LL84 pilot slice (see build_pilot.py WHERE).
FP_WHERE = "within_box(the_geom, 40.765, -73.988, 40.748, -73.970)"

# Energy attributes copied onto each polygon feature (raw LL84 keys).
ENERGY_NUMERIC_FIELDS = [
    "site_eui_kbtu_ft",
    "weather_normalized_site_eui",
    "total_location_based_ghg",
    "direct_ghg_emissions_intensity",
    "property_gfa_self_reported",
    "natural_gas_use_kbtu",
    "electricity_use_grid_purchase",
]
ENERGY_TEXT_FIELDS = ["address_1"]


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _to_num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s or s.lower() in {"n/a", "na", "null", "none", "not available"}:
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _fetch_footprints() -> tuple[list[dict], dict]:
    """Bounded paginated fetch of footprint polygons in the pilot bbox."""
    rows_all: list[dict] = []
    offset, limit = 0, 1000
    while True:
        params = {
            "$select": ",".join(FP_FIELDS),
            "$where": FP_WHERE,
            "$limit": limit,
            "$offset": offset,
        }
        url = FP_RESOURCE + "?" + urllib.parse.urlencode(params)
        try:
            chunk = json.load(urllib.request.urlopen(url, timeout=120))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode()[:300]
            except Exception:
                pass
            raise SystemExit(f"footprint fetch failed at offset {offset}: {e} {body}")
        if not chunk:
            break
        rows_all.extend(chunk)
        offset += limit
        if len(chunk) < limit:
            break
    meta = {
        "captured_utc": _now_utc(),
        "dataset": f"{FP_FID} (BUILDING footprints)",
        "resource": FP_RESOURCE,
        "query_where": FP_WHERE,
        "fields": FP_FIELDS,
        "rows": len(rows_all),
    }
    return rows_all, meta


def load_or_fetch_footprints(refetch: bool) -> tuple[list[dict], dict]:
    raw_path = os.path.join(SNAP_DIR, f"{FP_SLICE}.raw.json")
    manifest_path = os.path.join(SNAP_DIR, f"{FP_SLICE}.manifest.json")
    if not refetch and os.path.exists(raw_path) and os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
        actual = _sha256_file(raw_path)
        if actual != manifest["sha256"]:
            raise SystemExit(
                f"sha256 mismatch for {raw_path}: file={actual} manifest={manifest['sha256']}"
            )
        with open(raw_path, encoding="utf-8") as f:
            payload = json.load(f)
        return payload["rows"], manifest
    rows, meta = _fetch_footprints()
    payload = {"meta": meta, "rows": rows}
    tmp = raw_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    os.replace(tmp, raw_path)
    sha = _sha256_file(raw_path)
    manifest = {**meta, "sha256": sha, "raw_file": raw_path}
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    return rows, manifest


def load_ll84_rows() -> list[dict]:
    raw = os.path.join(SNAP_DIR, f"{LL84_SLICE}.raw.jsonl")
    return [json.loads(line) for line in open(raw, encoding="utf-8")]


def build_joined_geojson(ll84_rows: list[dict], footprints: list[dict]) -> tuple[dict, dict]:
    """Join footprints to LL84 rows by BBL (fallback BIN); one pid per polygon."""
    bbl_to: dict[str, str] = {}
    bin_to: dict[str, str] = {}
    props: dict[str, dict] = {}
    for r in ll84_rows:
        pid = str(r.get("property_id") or "")
        props[pid] = r
        raw_bbl = str(r.get("nyc_borough_block_and_lot") or "")
        raw_bin = str(r.get("nyc_building_identification") or "")
        for t in sorted(set(re.findall(r"\d{10}", raw_bbl))):
            bbl_to.setdefault(t, pid)  # first row wins; campuses keep parent grain
        for t in sorted(set(re.findall(r"\d{7}", raw_bin))):
            bin_to.setdefault(t, pid)

    joined, unmatched, no_geom = [], 0, 0
    for fp in footprints:
        geom = fp.get("the_geom")
        if not geom or geom.get("type") not in ("Polygon", "MultiPolygon"):
            no_geom += 1
            continue
        bbl = str(fp.get("mappluto_bbl") or fp.get("base_bbl") or "").strip()
        bin_ = str(fp.get("bin") or "").strip()
        pid = bbl_to.get(bbl) or bin_to.get(bin_)
        if not pid:
            unmatched += 1
            continue
        src = props[pid]
        out_props: dict = {
            "bin": bin_,
            "bbl": bbl,
            "pid": pid,
            "height_roof": _to_num(fp.get("height_roof")),
        }
        for f in ENERGY_NUMERIC_FIELDS:
            out_props[f] = _to_num(src.get(f))
        for f in ENERGY_TEXT_FIELDS:
            out_props[f] = src.get(f)
        joined.append({"type": "Feature", "properties": out_props, "geometry": geom})

    fc = {"type": "FeatureCollection", "features": joined}
    stats = {
        "footprints_total": len(footprints),
        "joined_features": len(joined),
        "unmatched_no_energy_data": unmatched,
        "skipped_no_geometry": no_geom,
        "join_note": (
            "BBL join first (mappluto_bbl, fallback base_bbl), BIN fallback. "
            "Polygon carries the PROPERTY-level energy row; for campus (multi-BIN) "
            "properties this is property-level, NOT per-building truth."
        ),
    }
    return fc, stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Join building footprints to LL84 energy data.")
    ap.add_argument("--refetch", action="store_true",
                    help="force fresh bounded fetch of building footprints")
    args = ap.parse_args(argv)

    footprints, fp_manifest = load_or_fetch_footprints(refetch=args.refetch)
    ll84_rows = load_ll84_rows()
    fc, stats = build_joined_geojson(ll84_rows, footprints)

    tmp = OUT_GEOJSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(fc, f)
    os.replace(tmp, OUT_GEOJSON)
    sha = _sha256_file(OUT_GEOJSON)

    sidecar = OUT_GEOJSON.replace(".geojson", ".manifest.json")
    with open(sidecar, "w", encoding="utf-8") as f:
        json.dump({
            "generated_utc": _now_utc(),
            "geojson_file": OUT_GEOJSON,
            "sha256": sha,
            "features": len(fc["features"]),
            "stats": stats,
            "footprint_snapshot_manifest": fp_manifest,
            "ll84_snapshot": LL84_SLICE,
        }, f, indent=2)

    with_eui = sum(1 for x in fc["features"] if x["properties"].get("site_eui_kbtu_ft") is not None)
    print(f"footprints: {stats['footprints_total']} -> joined {stats['joined_features']} "
          f"({with_eui} with EUI, {stats['unmatched_no_energy_data']} unmatched, "
          f"{stats['skipped_no_geometry']} no-geom) -> {OUT_GEOJSON}")
    print(f"sha256: {sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())