"""Read-only lookups into the citywide MODELLED annual-demand store.

Kept deliberately separate from the observed LL84 surface: this store holds
modeled end-use demand (space heating / DHW / cooling), which is not a
measurement. Callers must keep the two apart in whatever they present, so this
module never merges them — the API layer exposes them as distinct `observed` and
`modeled` blocks.

`evidence_tier` is carried through untouched. T4 rows exist for lots with no
LL84 record and have NULL end-uses: they are present so coverage is honest, NOT
because demand was modelled for them.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

DB_PATH = Path(
    "/mnt/e/OC_Projects/projects/signalnyc/data/citywide/"
    "annual_demand_citywide/annual_demand_citywide.sqlite"
)

# Columns surfaced for one building. Explicit rather than SELECT * so a schema
# addition is a deliberate choice about what leaves the server.
DETAIL_COLUMNS = [
    "bbl", "borough", "block", "lot", "land_use", "bldg_area_sqft",
    "space_heating_kbtu", "dhw_kbtu", "cooling_kbtu",
    "space_heating_kbtu_ft2_yr", "dhw_kbtu_ft2_yr", "cooling_kbtu_ft2_yr",
    "evidence_tier", "archetype",
    "method_heating", "method_cooling", "model_version",
    "ll84_gfa_sqft", "ll84_site_eui_kbtu_ft", "ll84_natural_gas_kbtu",
    "ll84_electricity_kbtu",
    "campus_apportioned", "campus_group_n", "campus_apportion_weight",
]


class DemandStore:
    """Thread-safe read-only access to modelled annual demand (one row per BBL)."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            if not self.db_path.exists():
                raise FileNotFoundError(f"citywide demand db missing: {self.db_path}")
            self._conn = sqlite3.connect(
                f"file:{self.db_path}?mode=ro", uri=True, timeout=30,
                check_same_thread=False,
            )
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def by_bbl(self, bbl: str) -> dict | None:
        cols = ", ".join(DETAIL_COLUMNS)
        with self._lock:
            db = self._connect()
            r = db.execute(
                f"SELECT {cols} FROM annual_demand WHERE bbl=? LIMIT 1", (bbl,)
            ).fetchone()
        if r is None:
            return None
        out = {k: r[k] for k in DETAIL_COLUMNS}
        # A T4 row is present for coverage but has no end-use numbers. Say so
        # explicitly instead of letting nulls read as "zero demand".
        out["has_end_uses"] = out.get("space_heating_kbtu") is not None
        if not out["has_end_uses"]:
            out["note"] = (
                "No LL84 record for this lot, so no end-use demand was modelled. "
                "Present for coverage; nulls are 'not modelled', never zero."
            )
        return out

    def coverage(self) -> dict:
        with self._lock:
            db = self._connect()
            total = db.execute("SELECT COUNT(*) FROM annual_demand").fetchone()[0]
            tiers = {
                t: n
                for t, n in db.execute(
                    "SELECT evidence_tier, COUNT(*) FROM annual_demand GROUP BY 1"
                )
            }
            with_end_use = db.execute(
                "SELECT COUNT(*) FROM annual_demand WHERE space_heating_kbtu IS NOT NULL"
            ).fetchone()[0]
        return {
            "source": "citywide modelled annual demand (BBL-keyed)",
            "rows_total": total,
            "rows_with_end_uses": with_end_use,
            "counts_by_tier": tiers,
            "note": (
                "Modelled, not measured. T4_FOOTPRINT_NUMERIC_ONLY rows have no "
                "LL84 record and carry NULL end-uses."
            ),
        }
