#!/usr/bin/env python3
"""Add an R-tree spatial index to the citywide footprints DB (in place).

Why: the original index was `(min_x, max_x)`, so SQLite could only seek on the
x-axis and had to fetch every row sharing that x-range to test y in the row
itself. A Midtown viewport (~1.5k footprints) took 19 s — on the /mnt/e Windows
mount each of those ~600k row probes is a slow random read.

An rtree virtual table indexes BOTH axes, so the same query becomes a real
2-D range search. This script builds the rtree alongside the existing table
(no rebuild of the 1.08M-row dataset) and swaps the store over to it.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO / "data" / "citywide" / "footprints_citywide.sqlite"


def main() -> int:
    import sys

    # Optional path argument: build the index on a native-disk copy and copy it
    # back, since /mnt/e random writes are ~20x slower.
    DB = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB
    if not DB.exists():
        print(f"ERROR: {DB} not found", flush=True)
        return 1

    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA journal_mode=MEMORY")
    conn.execute("PRAGMA synchronous=OFF")

    print(f"building rtree index on {DB} …", flush=True)
    t0 = time.time()

    # fid is the INTEGER PRIMARY KEY of footprints, which rtree requires as its
    # first column (an integer rowid-keyed column).
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS fp_rtree "
        "USING rtree(fid, min_x, max_x, min_y, max_y)"
    )
    conn.execute("DELETE FROM fp_rtree")
    conn.execute(
        "INSERT INTO fp_rtree(fid, min_x, max_x, min_y, max_y) "
        "SELECT fid, min_x, max_x, min_y, max_y FROM footprints"
    )
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM fp_rtree").fetchone()[0]
    print(f"  indexed {n:,} footprints in {time.time()-t0:.1f}s", flush=True)

    print("checkpointing …", flush=True)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.commit()

    # Verify speedup on the same Midtown viewport that took 19 s.
    t = time.time()
    rows = conn.execute(
        "SELECT COUNT(*) FROM fp_rtree WHERE min_x<=? AND max_x>=? AND min_y<=? AND max_y>=?",
        (-73.97, -73.99, 40.762, 40.75),
    ).fetchone()[0]
    fast = time.time() - t
    print(f"  rtree bbox count = {rows:,} in {fast:.3f}s", flush=True)

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
