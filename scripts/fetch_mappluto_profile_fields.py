"""Fetch building-profile fields (owner, assessed value, landmark, CD, address)
for all MapPLUTO lots and merge them into the existing lots DB.

Fetched attribute-only (no geometry — geometry is already down) so each page
is ~1 KB/record rather than the multi-ring polygons, ~430 pages total.

Adds columns: ownertype, ownername, assess_land, assess_total, exempt_total,
landmark, condo_no, cd, zip_code, address.

856,687 rows expected; requires fetch_citywide_mappluto.py to have completed.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.parse
import urllib.request

ROOT = "/mnt/e/OC_Projects/projects/signalnyc"
DB = f"{ROOT}/data/citywide/mappluto_lots.sqlite"
TMPPATH = f"{TMPDIR if (TMPDIR := os.environ.get('NYCDECO_TMP', '/tmp')) else '/tmp'}"
FEATURE = (
    "https://services5.arcgis.com/GfwWNkhOj9bNBqoJ/arcgis/rest/services/"
    "MAPPLUTO/FeatureServer/0/query"
)
FIELDS = "BBL,OwnerType,OwnerName,AssessLand,AssessTot,ExemptTot,Landmark,CondoNo,CD,ZipCode,Address"
MAX_REC = 2000
TOTAL = 856687

ALTERS = [
    ("ownertype", "TEXT"), ("ownername", "TEXT"),
    ("assess_land", "REAL"), ("assess_total", "REAL"), ("exempt_total", "REAL"),
    ("landmark", "TEXT"), ("condo_no", "TEXT"), ("cd", "INTEGER"),
    ("zip_code", "TEXT"), ("address", "TEXT"),
]


def _query(offset: int) -> dict:
    params = {
        "where": "1=1", "outFields": FIELDS, "returnGeometry": "false",
        "resultOffset": offset, "resultRecordCount": MAX_REC, "f": "json",
    }
    url = FEATURE + "?" + urllib.parse.urlencode(params)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=180) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            wait = 2**attempt
            print(f"  offset {offset} attempt {attempt+1} failed: {e}; retry {wait}s", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"ArcGIS query failed after retries at offset {offset}")


def main() -> None:
    t0 = time.time()
    db = sqlite3.connect(DB)
    existing = [r[1] for r in db.execute("PRAGMA table_info(lots)")]
    for col, typ in ALTERS:
        if col not in existing:
            db.execute(f"ALTER TABLE lots ADD COLUMN {col} {typ}")
    db.commit()

    offs = db.execute("SELECT COUNT(*) FROM lots").fetchone()[0]
    print(f"local lots: {offs:,}; target {TOTAL:,}", flush=True)
    # fetch page-stamped file for resume
    done_file = f"{DB}.profile_progress"
    start = 0
    if os.path.exists(done_file):
        start = int(open(done_file).read().strip() or 0)
    # rows are fetched keyed by BBL, so resume support just re-verifies count
    updated = 0
    offset = start
    while offset < TOTAL:
        page = _query(offset)
        feats = page.get("features") or []
        if not feats:
            print(f"  no features at offset {offset}; stopping", flush=True)
            break
        rows = []
        for f in feats:
            a = f.get("attributes") or {}
            bbl = str(a.get("BBL") or "").strip()
            if not bbl:
                continue
            rows.append((bbl, a.get("OwnerType"), a.get("OwnerName"),
                         a.get("AssessLand"), a.get("AssessTot"),
                         a.get("ExemptTot"), a.get("Landmark"),
                         str(a.get("CondoNo") or "") or None, a.get("CD"),
                         a.get("ZipCode"), a.get("Address")))
        db.executemany(
            """UPDATE lots SET ownertype=?, ownername=?, assess_land=?,
               assess_total=?, exempt_total=?, landmark=?, condo_no=?,
               cd=?, zip_code=?, address=? WHERE bbl=?""",
            [(r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10], r[0])
             for r in rows])
        db.commit()
        offset += len(feats)
        with open(done_file, "w") as fh:
            fh.write(str(offset))
        if (offset // 2000) % 50 == 0:
            print(f"  … {offset:,}/{TOTAL:,} ({time.time()-t0:.0f}s)", flush=True)
    db.execute("UPDATE progress SET done=done WHERE id=1")  # touch WAL checkpoint
    db.commit()
    print(f"done: {offset:,} rows profiled in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
