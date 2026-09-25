#!/usr/bin/env python3
"""Fetch NYC Business Improvement District (BID) boundaries.

Source: Socrata `7jdm-inj8` (Business Improvement Districts), 76 MultiPolygon
features covering all five boroughs. These become predefined district-study
boundaries in the app.

The Socrata field names are opaque, so the mapping was decoded from samples:
    f_all_bi_1  -> borough
    f_all_bi_2  -> BID name
    f_all_bi_4  -> website
    year_found  -> year established

Output is a small GeoJSON FeatureCollection (a few hundred KB) which the API
serves whole — no bbox endpoint needed at this size.
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "citywide" / "bids.geojson"
MANIFEST = REPO / "data" / "citywide" / "bids.manifest.json"

RESOURCE = "https://data.cityofnewyork.us/resource/7jdm-inj8.json"
FIELDS = ["f_all_bi_1", "f_all_bi_2", "f_all_bi_4", "year_found", "the_geom"]


def fetch(offset: int, limit: int = 200) -> list[dict]:
    params = {
        "$select": ",".join(FIELDS),
        "$limit": str(limit),
        "$offset": str(offset),
    }
    url = f"{RESOURCE}?{urllib.parse.urlencode(params)}"
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            if attempt == 3:
                raise
            print(f"  retry {attempt + 1} after {e}", flush=True)
            time.sleep(2 * (attempt + 1))
    return []


def main() -> int:
    t0 = time.time()
    rows: list[dict] = []
    offset = 0
    while True:
        page = fetch(offset)
        if not page:
            break
        rows.extend(page)
        print(f"  … {len(rows)} BID rows", flush=True)
        if len(page) < 200:
            break
        offset += len(page)

    feats = []
    skipped = 0
    for r in rows:
        geom = r.get("the_geom")
        if not geom or geom.get("type") not in ("Polygon", "MultiPolygon"):
            skipped += 1
            continue
        name = (r.get("f_all_bi_2") or "").strip()
        if not name:
            skipped += 1
            continue
        feats.append(
            {
                "type": "Feature",
                "geometry": geom,
                # Keep the raw field names out of the client contract: expose
                # readable keys so the frontend never depends on Socrata's
                # opaque column ordering.
                "properties": {
                    "bid_name": name,
                    "borough": (r.get("f_all_bi_1") or "").strip() or None,
                    "website": (r.get("f_all_bi_4") or "").strip() or None,
                    "year_found": int(r["year_found"]) if str(r.get("year_found", "")).isdigit() else None,
                    "kind": "bid",
                },
            }
        )

    feats.sort(key=lambda f: f["properties"]["bid_name"].lower())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {"type": "FeatureCollection", "features": feats}
    OUT.write_text(json.dumps(payload), encoding="utf-8")

    sha = hashlib.sha256(OUT.read_bytes()).hexdigest()
    by_boro: dict[str, int] = {}
    for f in feats:
        b = f["properties"]["borough"] or "unknown"
        by_boro[b] = by_boro.get(b, 0) + 1

    MANIFEST.write_text(
        json.dumps(
            {
                "source": "NYC Open Data Socrata 7jdm-inj8 (Business Improvement Districts)",
                "resource_url": RESOURCE,
                "field_map": {
                    "f_all_bi_1": "borough",
                    "f_all_bi_2": "bid_name",
                    "f_all_bi_4": "website",
                    "year_found": "year_found",
                    "the_geom": "geometry",
                },
                "bid_count": len(feats),
                "skipped": skipped,
                "by_borough": by_boro,
                "geojson_sha256": sha,
                "bytes": OUT.stat().st_size,
                "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "note": (
                    "BID boundaries are administrative districts. They are NOT "
                    "thermal districts: a BID boundary says nothing about shared "
                    "heating/cooling infrastructure. Present as study-area "
                    "boundaries only."
                ),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"\nwrote {OUT} ({OUT.stat().st_size/1024:.0f} KB)")
    print(f"  BIDs: {len(feats)}  skipped: {skipped}")
    print(f"  by borough: {by_boro}")
    print(f"  sha256: {sha}")
    print(f"  {time.time()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
