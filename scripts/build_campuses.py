#!/usr/bin/env python3
"""Derive campus boundaries as contiguous clusters of commonly-owned lots.

WHY DERIVED, NOT LOOKED UP
--------------------------
There is no authoritative polygon dataset for "campuses" (Rockefeller Center,
Penn District, City Hall, etc.). What exists is *ownership attribution at the
parcel level*:

  * COLP (`fn4k-qyk2`)  — 17.3k city-owned/leased parcels with an agency name
  * MapPLUTO lots       — 856,687 lots with full polygons

So a campus here is DEFINED as: lots sharing an owner/agency label that are
spatially contiguous (touching or within a small snap distance), dissolved
into a single polygon. That definition is explicit, reproducible, and stated
on every output feature — it is a proxy for "operated as one portfolio", not
an official boundary and not proof of a shared plant.

ALGORITHM
---------
1. Group lots by owner key (agency for COLP; normalized ownername for PLUTO).
2. Within a group, union-find lots whose polygons are within SNAP_DEG.
3. Keep clusters above MIN_LOTS / MIN_AREA (below that it is not a campus).
4. Dissolve each cluster (shapely unary_union) and record provenance:
   lot count, total building area, the owner key, and the snap distance.

Contiguity uses a STRtree spatial index over the group's own lots, so a large
agency does not do an O(n^2) pairwise scan.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOTS = REPO / "data" / "citywide" / "mappluto_lots.sqlite"
COLP = REPO / "data" / "citywide" / "colp.raw.jsonl"
OUT = REPO / "data" / "citywide" / "campuses.geojson"
MANIFEST = REPO / "data" / "citywide" / "campuses.manifest.json"

# Two lots count as contiguous if their polygons are within this many degrees
# (~30 m at NYC latitude). Larger values merge genuinely separate sites across
# streets; smaller values split campuses at plazas and alleys.
SNAP_DEG = 0.0003
MIN_LOTS = 4          # a campus is more than a couple of lots
MIN_BLDG_SQFT = 50_000  # and has meaningful building area

# Agency codes worth presenting as campuses. Includes state/federal-adjacent
# entities that appear in COLP. NYPD/FIRE/SANIT are included but tend to form
# small clusters, which MIN_LOTS filters out.
AGENCY_LABELS = {
    "DCAS": "NYC DCAS (citywide municipal)",
    "EDUC": "NYC DOE schools",
    "NYCHA": "NYC Housing Authority",
    "PARKS": "NYC Parks",
    "DEP": "NYC DEP",
    "DOT": "NYC DOT",
    "HPD": "NYC HPD",
    "EDC": "NYC Economic Development Corp",
    "DSBS": "NYC Design & Construction / public buildings",
    "NYPD": "NYPD",
    "FIRE": "FDNY",
    "SANIT": "NYC Sanitation",
    "MTA": "MTA",
    "NYCTA": "NYC Transit Authority",
    "HEALTH": "NYC Health + Hospitals",
    "CUNY": "CUNY",
    "DOHMH": "NYC Health",
    "DSS": "NYC Social Services",
    "DHS": "NYC Homeless Services",
    "PROB": "NYC Probation",
    "COURT": "NYC Courts",
    "CORP": "NYC Corrections",
}


def norm_owner(raw: str | None) -> str | None:
    """Normalize an owner name for grouping."""
    if not raw:
        return None
    s = " ".join(str(raw).upper().split())
    if not s or s in ("N/A", "NONE", "UNKNOWN", "NOT AVAILABLE"):
        return None
    return s


def load_colp() -> dict[str, str]:
    """BBL -> agency, plus parcel/campus names when present."""
    bbl_to_agency: dict[str, str] = {}
    if not COLP.exists():
        print(f"  WARNING: {COLP.name} missing — campus build limited to PLUTO owner names")
        return bbl_to_agency
    with COLP.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            r = json.loads(line)
            bbl = r.get("bbl")
            agency = (r.get("agency") or "").strip().upper()
            if bbl and agency:
                bbl_to_agency[bbl] = agency
    return bbl_to_agency


def main() -> int:
    try:
        from shapely.geometry import shape
        from shapely.ops import unary_union

        try:
            from shapely import STRtree  # shapely 2.x
        except ImportError:
            from shapely.strtree import STRtree  # shapely 1.8
    except ImportError as e:
        print(f"ERROR: shapely required: {e}", file=sys.stderr)
        return 1

    t0 = time.time()
    if not LOTS.exists():
        print(f"ERROR: {LOTS} missing", file=sys.stderr)
        return 1

    print("loading COLP ownership…", flush=True)
    bbl_to_agency = load_colp()
    print(f"  COLP parcels: {len(bbl_to_agency):,}", flush=True)

    print("loading lots from MapPLUTO…", flush=True)
    conn = sqlite3.connect(f"file:{LOTS}?mode=ro", uri=True)
    lots: list[tuple[str, float, object]] = []  # (bbl, bldg_area, geom)
    for bbl, bldg_area, geom_txt in conn.execute(
        "SELECT bbl, COALESCE(bldg_area,0), geom FROM lots WHERE geom IS NOT NULL"
    ):
        try:
            g = shape(json.loads(geom_txt))
        except Exception:
            continue
        if g.is_empty:
            continue
        lots.append((bbl, float(bldg_area), g))
    conn.close()
    print(f"  lots with geometry: {len(lots):,}", flush=True)

    # Group by owner key. COLP agency wins; otherwise fall back to nothing
    # (private campuses come from a PLUTO owner pass, added separately).
    groups: dict[str, list[tuple[str, float, object]]] = {}
    for bbl, area, g in lots:
        agency = bbl_to_agency.get(bbl)
        if not agency:
            continue
        groups.setdefault(agency, []).append((bbl, area, g))
    print(f"  agency groups: {len(groups)}", flush=True)

    feats: list[dict] = []
    for agency, members in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if len(members) < MIN_LOTS:
            continue
        geoms = [m[2] for m in members]
        tree = STRtree(geoms)
        # Union-find over proximity.
        parent = list(range(len(members)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        for i, g in enumerate(geoms):
            # buffer+query returns candidates within SNAP_DEG
            for j in tree.query(g.buffer(SNAP_DEG)):
                j = int(j)
                if j != i and g.distance(geoms[j]) <= SNAP_DEG:
                    union(i, j)

        clusters: dict[int, list[int]] = {}
        for i in range(len(members)):
            clusters.setdefault(find(i), []).append(i)

        for root, idxs in clusters.items():
            if len(idxs) < MIN_LOTS:
                continue
            tot_area = sum(members[i][1] for i in idxs)
            if tot_area < MIN_BLDG_SQFT:
                continue
            try:
                dissolved = unary_union([members[i][2] for i in idxs])
            except Exception:
                continue
            if dissolved.is_empty:
                continue

            # Name the cluster from its largest lot's BBL area (deterministic
            # and stable across runs) — COLP parcel names are inconsistent.
            label = AGENCY_LABELS.get(agency, agency)
            feats.append({
                "type": "Feature",
                "geometry": json.loads(json.dumps(dissolved.__geo_interface__)),
                "properties": {
                    "campus_name": f"{label} cluster ({len(idxs)} lots)",
                    "owner_key": agency,
                    "owner_label": label,
                    "lot_count": len(idxs),
                    "bldg_area_sqft": round(tot_area),
                    "derivation": f"contiguous lots, owner={agency}, snap={SNAP_DEG}deg",
                    "kind": "campus",
                    "owner_kind": "public",
                },
            })

    feats.sort(key=lambda f: -f["properties"]["bldg_area_sqft"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")

    import hashlib
    sha = hashlib.sha256(OUT.read_bytes()).hexdigest()
    MANIFEST.write_text(json.dumps({
        "source": "Derived from COLP (fn4k-qyk2) ownership + MapPLUTO lot polygons",
        "definition": (
            "A campus is a set of lots sharing an owner/agency label whose "
            "polygons are within SNAP_DEG of each other, dissolved into one "
            "polygon. This is a PROXY for a commonly-operated portfolio — not an "
            "official boundary, and not evidence of a shared plant or meter."
        ),
        "params": {"snap_deg": SNAP_DEG, "min_lots": MIN_LOTS, "min_bldg_sqft": MIN_BLDG_SQFT},
        "campus_count": len(feats),
        "geojson_sha256": sha,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "limitation": (
            "Private campuses (Rockefeller Center, Penn/Vornado) need a PLUTO "
            "ownername pass; this build covers city-agency ownership only."
        ),
    }, indent=2), encoding="utf-8")

    print(f"\nwrote {OUT} ({OUT.stat().st_size/1024:.0f} KB)")
    print(f"  campuses: {len(feats)}")
    for f in feats[:12]:
        p = f["properties"]
        print(f"    {p['owner_key']:8s} {p['lot_count']:5d} lots  {p['bldg_area_sqft']:>12,} ft²")
    print(f"  sha256: {sha}")
    print(f"  {time.time()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
