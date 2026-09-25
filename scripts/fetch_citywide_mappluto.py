#!/usr/bin/env python3
"""Citywide MapPLUTO ingest to SQLite: resumable offset-windowed ArcGIS sweep.

Downloads all 0.86M tax-lot polygons (WGS84) from the DCP MAPPLUTO
FeatureServer, with a SHA256-cursor manifest so interrupted runs resume at the
last completed offset window. Local store = data/citywide/mappluto_lots.sqlite
(WAL), one row per lot: geometry JSON + attribute subset + precomputed
lot_area_ft2.

Run: .venv/bin/python3 scripts/fetch_citywide_mappluto.py        # resume/continue
     .venv/bin/python3 scripts/fetch_citywide_mappluto.py --fresh  # wipe + start over
"""
from __future__ import annotations
import argparse, hashlib, json, os, sqlite3, sys, time, urllib.parse, urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(REPO, "data", "citywide")
DB = os.path.join(OUT_DIR, "mappluto_lots.sqlite")
MANIFEST = os.path.join(OUT_DIR, "mappluto_lots.manifest.json")
MAN_PATH = MANIFEST  # alias
FEATURE = "https://services5.arcgis.com/GfwWNkhOj9bNBqoJ/arcgis/rest/services/MAPPLUTO/FeatureServer/0/query"
MAX_REC = 2000          # server hard cap (probed 2026-09-25)
TOTAL = 856687          # count(1=1) probed; checked again per run
OUT_FIELDS = "BBL,Borough,Block,Lot,LotArea,BldgArea,BuiltFAR,NumBldgs,NumFloors,YearBuilt,Latitude,Longitude,ZoneDist1,ZoneDist2,LandUse,BldgClass"

SCHEMA = """
CREATE TABLE IF NOT EXISTS lots (
  bbl TEXT PRIMARY KEY,
  borough TEXT, block TEXT, lot TEXT,
  lot_area REAL, bldg_area REAL, built_far REAL,
  num_bldgs INTEGER, num_floors REAL, year_built INTEGER,
  latitude REAL, longitude REAL,
  land_use TEXT, bldg_class TEXT, zone_dist1 TEXT, zone_dist2 TEXT,
  geom TEXT
);
CREATE TABLE IF NOT EXISTS progress (
  id INTEGER PRIMARY KEY CHECK (id=1),
  next_offset INTEGER NOT NULL,
  done INTEGER NOT NULL
);
"""


def _query(offset: int) -> bytes:
    params = {
        "where": "1=1",
        "outFields": OUT_FIELDS,
        "returnGeometry": "true",
        "outSR": 4326,
        "resultOffset": offset,
        "resultRecordCount": MAX_REC,
        "f": "json",
    }
    url = FEATURE + "?" + urllib.parse.urlencode(params)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=180) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            wait = 2 ** attempt
            print(f"  fetch(offset={offset}) attempt {attempt+1} failed: {e}; retry in {wait}s", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"ArcGIS query failed after retries at offset {offset}")


def _bbox_of(rings) -> tuple[float, float, float, float] | None:
    xs, ys = [], []
    for r in rings:
        for x, y in r:
            xs.append(x); ys.append(y)
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _row_from_feature(f: dict) -> tuple | None:
    a = f.get("attributes") or {}
    g = f.get("geometry") or {}
    rings = g.get("rings")
    if not rings:
        return None
    geom_json = json.dumps({"type": "Polygon", "coordinates": rings} if len(rings) == 1
                           else {"type": "MultiPolygon", "coordinates": [rings]},
                           separators=(",", ":"))
    bbl = str(a.get("BBL") or "").strip()
    if not bbl:
        return None
    bbox = _bbox_of(rings)
    lat = a.get("Latitude")
    lon = a.get("Longitude")
    if (lat is None or lon is None) and bbox:
        lon, lat = (bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0
    return (bbl, a.get("Borough"), a.get("Block"), a.get("Lot"),
            a.get("LotArea"), a.get("BldgArea"), a.get("BuiltFAR"),
            a.get("NumBldgs"), a.get("NumFloors"), a.get("YearBuilt"),
            lat, lon, a.get("LandUse"), a.get("BldgClass"),
            a.get("ZoneDist1"), a.get("ZoneDist2"),
            geom_json)


def _progress(db, next_offset: int, done: int):
    db.execute("INSERT OR REPLACE INTO progress (id, next_offset, done) VALUES (1, ?, ?)", (next_offset, done))
    db.commit()


def _progress_get(db):
    row = db.execute("SELECT next_offset, done FROM progress WHERE id=1").fetchone()
    return (row[0], row[1]) if row else (0, 0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--max-offset", type=int, default=None, help="stop early once next_offset exceeds this (testing)")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    fresh = args.fresh
    if fresh and os.path.exists(DB):
        os.remove(DB)
        print("wiped", DB)
    db = sqlite3.connect(DB)
    db.executescript(SCHEMA)
    db.execute("PRAGMA journal_mode=WAL")
    next_offset, done = _progress_get(db)

    pages = 0
    while next_offset < TOTAL:
        if args.max_offset is not None and next_offset >= args.max_offset:
            break
        body = _query(next_offset)
        data = json.loads(body)
        feats = data.get("features") or []
        if not feats:
            break
        rows, inserts = [], 0
        for f in feats:
            r = _row_from_feature(f)
            if r is None:
                continue
            rows.append(r)
            inserts += 1
        db.executemany(
            "INSERT OR REPLACE INTO lots (bbl, borough, block, lot, lot_area, bldg_area, built_far,"
            " num_bldgs, num_floors, year_built, latitude, longitude, land_use, bldg_class, zone_dist1, zone_dist2, geom)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        next_offset += len(feats)
        done += inserts
        pages += 1
        _progress(db, next_offset, done)
        if pages % 25 == 0:
            print(f"  ... page {pages}, next_offset={next_offset:,}, done={done:,}", flush=True)

    final = db.execute("SELECT COUNT(*) FROM lots").fetchone()[0]
    print(f"lots inserted total: {final:,} (done={done:,}), next_offset={next_offset:,}")
    db.execute("PRAGMA wal_checkpoint(FULL)")
    db.commit(); db.close()

    m = {
        "captured_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": FEATURE,
        "total_source_count": TOTAL,
        "max_record_count": MAX_REC,
        "out_fields": OUT_FIELDS,
        "flushed_rows": final,
        "inserted_this_run": done,
        "next_offset_after_run": next_offset,
        "db": DB,
        "note": ("complete" if next_offset >= TOTAL else
                 f"incomplete — rerun to resume at next_offset={next_offset}"),
    }
    if os.path.exists(MANIFEST):
        prev = json.load(open(MANIFEST))
        prev.update(m)
        m = prev
    with open(MANIFEST, "w") as f:
        json.dump(m, f, indent=2)
    print(json.dumps({k: m[k] for k in ("flushed_rows", "next_offset_after_run", "note")}, indent=2))


if __name__ == "__main__":
    main()
