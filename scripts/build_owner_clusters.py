#!/usr/bin/env python3
"""Add STATE and PRIVATE owner clusters (NYS agencies, Penn Central, etc.).

The MapPLUTO lot DB does not carry owner names, so rather than refetch all
857k lots with geometry we query Socrata for just the owner-attributed BBLs
(a few thousand rows) and join them to the lot polygons we already have.

Owner names in PLUTO are FRAGMENTED by entity: Vornado and Rockefeller appear
as dozens of single-lot LLCs ("VORNADO ELEVEN PENN PLAZA OWNER LLC",
"ROCKEFELLER CTR NORTH INC"). So grouping is by a NORMALIZED OWNER FAMILY
(OWNER_PATTERNS below), which is an explicit editorial decision — every output
feature records the normalizer that produced it so the grouping is auditable.

This is a proxy for a commonly-operated portfolio, NOT an official campus
boundary and not evidence of a shared plant.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOTS = REPO / "data" / "citywide" / "mappluto_lots.sqlite"
OUT = REPO / "data" / "citywide" / "owner_clusters.geojson"
MANIFEST = REPO / "data" / "citywide" / "owner_clusters.manifest.json"

RESOURCE = "https://data.cityofnewyork.us/resource/64uk-42ks.json"
SNAP_DEG = 0.0003
MIN_LOTS = 3
MIN_BLDG_SQFT = 100_000

# Owner family -> (label, kind, SQL LIKE patterns matched against ownername)
# Kept as a small explicit table rather than fuzzy matching, so the rule set is
# reviewable and stable. NOTE: private owners are fragmented across many
# single-lot LLCs ("VORNADO ELEVEN PENN PLAZA OWNER LLC", "ROCKEFELLER CTR
# NORTH INC"), so those patterns MUST be %-wrapped substrings — an exact
# equality pattern silently matches nothing and looks like "no such owner".
OWNER_PATTERNS: list[tuple[str, str, str, list[str]]] = [
    ("nys_dot", "NYS Department of Transportation", "state", ["NYS DEPARTMENT OF TRANSPORTATION"]),
    ("nys_dec", "NYS Dept of Environmental Conservation", "state", ["NYS DEPARTMENT OF ENVIRONMENTAL CONSERVATION"]),
    ("nys_ogs", "NYS Office of General Services", "state", ["NYS OFFICE OF GENERAL SERVICES"]),
    ("nys_dasny", "Dormitory Authority of the State of NY", "state", ["DORMITORY AUTHORITY%STATE OF NEW YORK"]),
    ("nys_state", "State of New York", "state", ["STATE OF NEW YORK", "PEOPLE OF THE STATE OF NEW YORK"]),
    ("nys_parks", "NYS Parks/Dec Lands & Forests", "state", ["NYS DIV OF LANDS AND FORESTS"]),
    ("mta_lirr", "MTA - LIRR", "state", ["MTA - LIRR", "MTA-LIRR"]),
    ("mta_metro_north", "MTA - Metro-North", "state", ["%METRO-NORTH%", "%METRO NORTH%"]),
    ("penn_central", "Penn Central (Penn Station district)", "private", ["%PENN CENTRAL%"]),
    ("rockefeller", "Rockefeller Center / University", "private", ["%ROCKEFELLER%"]),
    ("vornado", "Vornado Realty Trust", "private", ["%VORNADO%"]),
    ("brookfield", "Brookfield Properties", "private", ["%BROOKFIELD%"]),
    ("tishman_speyer", "Tishman Speyer", "private", ["%TISHMAN SPEYER%"]),
    ("related", "Related Companies", "private", ["%RELATED COMPANIES%", "%RELATED %COMPANIES%"]),
    ("slgreen", "SL Green Realty", "private", ["%SL GREEN%"]),
    ("rbc", "RXR / Rudin / Boston Properties", "private", ["%RXR%", "%RUDIN%", "%BOSTON PROPERTIES%"]),
    ("columbia", "Columbia University", "institution", ["%TRUSTEES OF COLUMBIA UNIVERSITY%", "%COLUMBIA UNIVERSITY%"]),
    ("nyu", "New York University", "institution", ["%NEW YORK UNIVERSITY%"]),
    ("cornell", "Cornell University (incl. Cornell Tech)", "institution", ["%CORNELL UNIVERSITY%"]),
    ("fordham", "Fordham University", "institution", ["%FORDHAM UNIVERSITY%"]),
    ("nysp_hospitals", "NYC Health + Hospitals / state hospitals", "state", ["%HEALTH AND HOSPITALS%"]),
]


def norm_bbl(raw) -> str | None:
    """Normalize a BBL from any surface to the canonical 10-digit string.

    PLUTO/COLP both hand these back as FLOATS as text — PLUTO as
    '1008070001.00000000', COLP as '3085910175.0' — while the local MapPLUTO
    store holds '1008070001'. Comparing the raw forms matches nothing at all,
    which is exactly how a join silently returns zero rows.
    """
    if raw in (None, ""):
        return None
    s = str(raw).strip()
    if "." in s:
        s = s.split(".", 1)[0]
    if not s.isdigit():
        return None
    s = s.zfill(10)
    return s if len(s) == 10 else None


def fetch_owner(patterns: list[str]) -> list[tuple[str, str]]:
    """(bbl, ownername) rows whose ownername matches any pattern."""
    clauses = " OR ".join(
        f"upper(ownername) like '{p.replace(chr(39), chr(39)*2)}'" for p in patterns
    )
    params = {
        "$query": (
            f"SELECT bbl, ownername WHERE {clauses} LIMIT 20000"
        )
    }
    url = f"{RESOURCE}?{urllib.parse.urlencode(params)}"
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=180) as r:
                rows = json.loads(r.read().decode("utf-8"))
            out = []
            for x in rows:
                b = norm_bbl(x.get("bbl"))
                if b:
                    out.append((b, x.get("ownername") or ""))
            return out
        except Exception as e:
            if attempt == 3:
                print(f"    FAILED: {e}", flush=True)
                return []
            time.sleep(2 * (attempt + 1))
    return []


def main() -> int:
    from shapely.geometry import shape
    from shapely.ops import unary_union

    try:
        from shapely import STRtree
    except ImportError:
        from shapely.strtree import STRtree

    t0 = time.time()
    wanted: dict[str, tuple[str, str, str]] = {}  # bbl -> (family, label, kind)
    for fam, label, kind, patterns in OWNER_PATTERNS:
        rows = fetch_owner(patterns)
        for bbl, _owner in rows:
            wanted.setdefault(bbl, (fam, label, kind))
        print(f"  {fam:18s} {len(rows):5d} lots", flush=True)

    print(f"\ndistinct target BBLs: {len(wanted):,}", flush=True)

    conn = sqlite3.connect(f"file:{LOTS}?mode=ro", uri=True)
    groups: dict[str, list] = {}
    found = 0
    for bbl, bldg_area, geom_txt in conn.execute(
        "SELECT bbl, COALESCE(bldg_area,0), geom FROM lots WHERE geom IS NOT NULL"
    ):
        if bbl not in wanted:
            continue
        try:
            g = shape(json.loads(geom_txt))
        except Exception:
            continue
        if g.is_empty:
            continue
        fam, label, kind = wanted[bbl]
        groups.setdefault(f"{fam}|{label}|{kind}", []).append((bbl, float(bldg_area), g))
        found += 1
    conn.close()
    print(f"joined to lot geometry: {found:,}", flush=True)
    if found == 0 and wanted:
        print(
            "ERROR: 0 of "
            f"{len(wanted):,} target BBLs joined to lot geometry — this means the "
            "BBL key formats differ, not that the owners are absent. Refusing to "
            "write an empty cluster file.",
            flush=True,
        )
        return 1

    feats: list[dict] = []
    for key, members in groups.items():
        fam, label, kind = key.split("|")
        if len(members) < MIN_LOTS:
            continue
        geoms = [m[2] for m in members]
        tree = STRtree(geoms)
        parent = list(range(len(members)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i, g in enumerate(geoms):
            for j in tree.query(g.buffer(SNAP_DEG)):
                j = int(j)
                if j != i and g.distance(geoms[j]) <= SNAP_DEG:
                    ri, rj = find(i), find(j)
                    if ri != rj:
                        parent[rj] = ri

        clusters: dict[int, list[int]] = {}
        for i in range(len(members)):
            clusters.setdefault(find(i), []).append(i)

        for _root, idxs in clusters.items():
            if len(idxs) < MIN_LOTS:
                continue
            area = sum(members[i][1] for i in idxs)
            if area < MIN_BLDG_SQFT:
                continue
            try:
                dissolved = unary_union([members[i][2] for i in idxs])
            except Exception:
                continue
            if dissolved.is_empty:
                continue
            feats.append({
                "type": "Feature",
                "geometry": json.loads(json.dumps(dissolved.__geo_interface__)),
                "properties": {
                    "campus_name": f"{label} ({len(idxs)} lots)",
                    "owner_key": fam,
                    "owner_label": label,
                    "owner_kind": kind,
                    "lot_count": len(idxs),
                    "bldg_area_sqft": round(area),
                    "derivation": f"contiguous lots, owner family '{fam}', snap={SNAP_DEG}deg",
                    "kind": "campus",
                },
            })

    feats.sort(key=lambda f: -f["properties"]["bldg_area_sqft"])
    OUT.write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")

    sha = hashlib.sha256(OUT.read_bytes()).hexdigest()
    MANIFEST.write_text(json.dumps({
        "source": "MapPLUTO ownername (Socrata 64uk-42ks) + local MapPLUTO lot polygons",
        "definition": (
            "Campus = contiguous lots sharing a NORMALIZED OWNER FAMILY. Owner "
            "names are fragmented across single-lot LLCs, so families are matched "
            "by explicit pattern table (see owner_patterns). Proxy only."
        ),
        "params": {"snap_deg": SNAP_DEG, "min_lots": MIN_LOTS, "min_bldg_sqft": MIN_BLDG_SQFT},
        "owner_patterns": {fam: pats for fam, _l, _k, pats in OWNER_PATTERNS},
        "target_bbls": len(wanted),
        "joined_bbls": found,
        "campus_count": len(feats),
        "geojson_sha256": sha,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }, indent=2), encoding="utf-8")

    print(f"\nwrote {OUT} ({OUT.stat().st_size/1024:.0f} KB)")
    print(f"  clusters: {len(feats)}")
    for f in feats[:15]:
        p = f["properties"]
        print(f"    {p['owner_key']:16s} {p['owner_kind']:11s} lots={p['lot_count']:3d} "
              f"{p['bldg_area_sqft']:>12,} ft²  {p['owner_label'][:38]}")
    print(f"  sha256: {sha}")
    print(f"  {time.time()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
