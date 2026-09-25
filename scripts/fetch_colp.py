#!/usr/bin/env python3
"""Fetch City Owned and Leased Property (COLP) — agency-attributed parcels.

Source: Socrata `fn4k-qyk2`, ~17.3k rows. BBLs arrive as FLOATS (e.g.
"3085910175.0"), so they must be normalized to the 10-char zero-padded form
used everywhere else or every join silently misses. Non-numeric / null BBLs are
dropped rather than coerced.

This is the input to campus derivation: agency + BBL is the ownership signal.
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "citywide" / "colp.raw.jsonl"
MANIFEST = REPO / "data" / "citywide" / "colp.manifest.json"

RESOURCE = "https://data.cityofnewyork.us/resource/fn4k-qyk2.json"
FIELDS = [
    "bbl", "borough", "tax_block", "tax_lot", "address", "parcel_name",
    "agency", "use_type", "excatdesc", "category_code", "leased_properties",
    "latitude", "longitude",
]
PAGE = 2000


def fetch(offset: int) -> list[dict]:
    params = {
        "$select": ",".join(FIELDS),
        "$limit": str(PAGE),
        "$offset": str(offset),
        "$order": "bbl",
    }
    url = f"{RESOURCE}?{urllib.parse.urlencode(params)}"
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            if attempt == 3:
                raise
            print(f"  retry {attempt+1} after {e}", flush=True)
            time.sleep(2 * (attempt + 1))
    return []


def norm_bbl(raw) -> str | None:
    """COLP BBLs come as '3085910175.0'. Normalize to 10-char digit string."""
    if raw in (None, ""):
        return None
    s = str(raw).strip()
    if s.endswith(".0"):
        s = s[:-2]
    if not s.isdigit():
        return None
    s = s.zfill(10)
    return s if len(s) == 10 else None


def main() -> int:
    t0 = time.time()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    offset = 0
    bad_bbl = 0
    while True:
        page = fetch(offset)
        if not page:
            break
        for r in page:
            b = norm_bbl(r.get("bbl"))
            if b is None:
                bad_bbl += 1
                continue
            r["bbl"] = b
            rows.append(r)
        print(f"  … {len(rows)} rows", flush=True)
        if len(page) < PAGE:
            break
        offset += len(page)

    with OUT.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")

    agencies: dict[str, int] = {}
    for r in rows:
        a = (r.get("agency") or "UNKNOWN").strip() or "UNKNOWN"
        agencies[a] = agencies.get(a, 0) + 1
    top = dict(sorted(agencies.items(), key=lambda kv: -kv[1])[:15])

    sha = hashlib.sha256(OUT.read_bytes()).hexdigest()
    MANIFEST.write_text(json.dumps({
        "source": "NYC Open Data Socrata fn4k-qyk2 (City Owned and Leased Property)",
        "resource_url": RESOURCE,
        "rows": len(rows),
        "dropped_bad_bbl": bad_bbl,
        "distinct_agencies": len(agencies),
        "top_agencies": top,
        "raw_sha256": sha,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "notes": [
            "BBL arrives as a float in the source and is normalized to 10 digits.",
            "Covers city-owned AND leased property; `leased_properties` distinguishes.",
            "Geometry in COLP is points only — campus polygons are dissolved from "
            "MapPLUTO lot polygons, not from COLP.",
        ],
    }, indent=2), encoding="utf-8")

    print(f"\nwrote {OUT} ({OUT.stat().st_size/1e6:.1f} MB)")
    print(f"  rows: {len(rows):,}  dropped_bad_bbl: {bad_bbl}")
    print(f"  distinct agencies: {len(agencies)}")
    print(f"  top: {top}")
    print(f"  sha256: {sha}")
    print(f"  {time.time()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
