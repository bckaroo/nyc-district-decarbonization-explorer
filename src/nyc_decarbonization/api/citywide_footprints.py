"""Citywide footprint store: bbox + id lookups over ALL DOB building footprints.

Backs `/api/footprints/bbox` so the map can draw every building in the city,
not just the Midtown pilot slice. Geometry stays as compact GeoJSON text in
SQLite; bbox columns + indexes make a viewport query cheap.

Unit and honesty notes carried from the pilot pipeline:
  * LL84 values are PROPERTY-level. Joining them to a footprint does not make
    them per-building measurements; `has_ll84=0` means "not required to
    report", never "zero energy".
  * Electricity (unsuffixed) is kBtu; its `_1` sibling is kWh.
  * Modeled demand columns are modeled. NULL means not modeled — never 0.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

DB_PATH = Path(
    "/mnt/e/OC_Projects/projects/signalnyc/data/citywide/footprints_citywide.sqlite"
)

# Columns echoed to the client, with units, so the frontend never has to
# guess. Kept explicit rather than SELECT * so schema additions are deliberate.
ENERGY_COLUMNS = [
    "site_eui_kbtu_ft",
    "weather_normalized_site_eui",
    "total_location_based_ghg",
    "direct_ghg_emissions_intensity",
    "property_gfa_self_reported",
    "electricity_use_grid_purchase",
    "natural_gas_use_kbtu",
    "district_steam_use_kbtu",
    "district_hot_water_use_kbtu",
    "district_chilled_water_use",
    "fuel_oil_1_use_kbtu",
    "fuel_oil_2_use_kbtu",
    "fuel_oil_4_use_kbtu",
    "fuel_oil_5_6_use_kbtu",
    "diesel_2_use_kbtu",
    "propane_use_kbtu",
]
MODELED_COLUMNS = [
    "space_heating_kbtu",
    "dhw_kbtu",
    "cooling_kbtu",
    "space_heating_kbtu_ft2_yr",
    "dhw_kbtu_ft2_yr",
    "cooling_kbtu_ft2_yr",
    "net_thermal_kbtu_ft2_yr",
    "evidence_tier",
    "archetype",
]
GEO_COLUMNS = ["fid", "bin", "bbl", "height_roof", "construction_year", "name"]

# Upper bound on the `matched` count returned for a viewport. A citywide view
# can hold ~1M footprints; counting them all would scan the whole table for a
# number the UI shows as approximate. When the cap is hit, `truncated` is True.
MATCH_CAP = 20000


class FootprintStore:
    """Read-only bbox queries over citywide footprints (thread-safe)."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._rtree: bool | None = None

    def _connect(self) -> sqlite3.Connection:
        # FastAPI runs these on the anyio worker threads, so the shared
        # read-only connection must allow cross-thread use; the lock
        # serializes access.
        if self._conn is None:
            if not self.db_path.exists():
                raise FileNotFoundError(f"citywide footprints db missing: {self.db_path}")
            self._conn = sqlite3.connect(
                f"file:{self.db_path}?mode=ro",
                uri=True,
                timeout=30,
                check_same_thread=False,
            )
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def coverage(self) -> dict:
        """Coverage counts for the citywide footprint layer.

        The net-thermal count is NOT computed by scanning `footprints` here: the
        predicted column is not indexed, so `WHERE net_thermal_kbtu_ft2_yr IS NOT
        NULL` over 1,083,047 rows takes ~27s and made this endpoint time out
        (curl returned HTTP 000 after 60s while /api/footprints/bbox served in
        0.85s). The build already computed the authoritative counts, so prefer
        the manifest and fall back to the scan only if it is unavailable.
        """
        manifest = self.db_path.parent / "footprints_citywide.manifest.json"
        counts = None
        if manifest.exists():
            try:
                m = json.loads(manifest.read_text())
                md = m.get("modeled_demand") or {}
                join = m.get("join") or {}
                counts = {
                    "total": m.get("features_ingested"),
                    "with_net": md.get("footprints_with_net_thermal"),
                    "with_ll": join.get("total_with_ll84"),
                }
            except (OSError, ValueError):
                counts = None

        with self._lock:
            db = self._connect()
            total = counts["total"] if counts and counts["total"] else None
            if not total:
                total = db.execute("SELECT COUNT(*) FROM footprints").fetchone()[0]
            if counts and counts["with_net"] is not None:
                with_ll = counts["with_ll"]
                with_net = counts["with_net"]
            else:
                with_ll = db.execute(
                    "SELECT COUNT(*) FROM footprints WHERE has_ll84=1"
                ).fetchone()[0]
                with_net = db.execute(
                    "SELECT COUNT(*) FROM footprints WHERE net_thermal_kbtu_ft2_yr IS NOT NULL"
                ).fetchone()[0]
        return {
            "source": "NYC DOB BUILDING footprints (Socrata 5zhs-2jue), citywide",
            "footprints_total": total,
            "footprints_with_ll84": with_ll,
            "footprints_with_modeled_net_thermal": with_net,
            "counts_from_manifest": bool(counts),
            "note": (
                "LL84 covers only buildings above the benchmarking size threshold, "
                "so a low join rate is expected. has_ll84=0 means not required to "
                "report, not zero energy. Modeled columns are modeled, not measured."
            ),
        }

    def _has_rtree(self, db: sqlite3.Connection) -> bool:
        """Whether the spatial index exists (older DBs predate it)."""
        if self._rtree is None:
            row = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='fp_rtree'"
            ).fetchone()
            self._rtree = row is not None
        return self._rtree

    def query_bbox(
        self, min_x: float, min_y: float, max_x: float, max_y: float, limit: int
    ) -> tuple[list[dict], int, bool]:
        """Features whose bbox overlaps the viewport.

        Returns (features, matched, truncated). `matched` is capped at
        MATCH_CAP and `truncated` is True whenever the cap was hit, so a
        zoomed-out viewport neither scans the whole city nor claims an exact
        total it did not compute.

        The rtree virtual table indexes both axes, so this is a genuine 2-D
        range search. Without it SQLite can only seek one axis and has to
        fetch every row sharing that range to test the other in-row — which
        on the /mnt/e mount cost ~19 s per Midtown request.
        """
        cols = ", ".join(f"f.{c}" for c in GEO_COLUMNS + ENERGY_COLUMNS + MODELED_COLUMNS)
        args = (max_x, min_x, max_y, min_y)
        with self._lock:
            db = self._connect()
            if self._has_rtree(db):
                frm = (
                    "FROM footprints f JOIN fp_rtree r ON r.fid = f.fid "
                    "WHERE r.min_x <= ? AND r.max_x >= ? AND r.min_y <= ? AND r.max_y >= ?"
                )
            else:
                frm = (
                    "FROM footprints f "
                    "WHERE f.min_x <= ? AND f.max_x >= ? AND f.min_y <= ? AND f.max_y >= ?"
                )
            # Cap the count: an exact COUNT over a citywide viewport would scan
            # every matching row for a number the UI only displays approximately.
            matched = db.execute(
                f"SELECT COUNT(*) FROM (SELECT f.fid {frm} LIMIT {MATCH_CAP})", args
            ).fetchone()[0]
            rows = db.execute(
                f"SELECT {cols}, f.geom, f.has_ll84 {frm} LIMIT ?", (*args, limit)
            ).fetchall()
        feats = []
        for r in rows:
            props = {k: r[k] for k in GEO_COLUMNS}
            props["has_ll84"] = bool(r["has_ll84"])
            for k in ENERGY_COLUMNS + MODELED_COLUMNS:
                props[k] = r[k]
            feats.append(
                {
                    "type": "Feature",
                    "geometry": json.loads(r["geom"]),
                    "properties": props,
                }
            )
        return feats, matched, matched >= MATCH_CAP or matched > len(feats)

    def by_bin(self, bin_: str) -> dict | None:
        cols = ", ".join(GEO_COLUMNS + ENERGY_COLUMNS + MODELED_COLUMNS)
        with self._lock:
            db = self._connect()
            r = db.execute(
                f"SELECT {cols}, geom, has_ll84 FROM footprints WHERE bin=? LIMIT 1",
                (bin_,),
            ).fetchone()
        if r is None:
            return None
        props = {k: r[k] for k in GEO_COLUMNS}
        props["has_ll84"] = bool(r["has_ll84"])
        for k in ENERGY_COLUMNS + MODELED_COLUMNS:
            props[k] = r[k]
        return {
            "type": "Feature",
            "geometry": json.loads(r["geom"]),
            "properties": props,
        }

    def by_bbl(self, bbl: str) -> dict | None:
        """Lookup by BBL — the key a map click yields.

        Mirrors by_bin, including the `has_ll84` bool coercion so consumers never
        receive 0/1 where they expect a boolean. A BBL may map to more than one
        footprint (condo lots, multiple structures on one lot); the lowest fid is
        returned deterministically rather than an arbitrary row.
        """
        cols = ", ".join(GEO_COLUMNS + ENERGY_COLUMNS + MODELED_COLUMNS)
        with self._lock:
            db = self._connect()
            r = db.execute(
                f"SELECT {cols}, geom, has_ll84 FROM footprints WHERE bbl=? "
                "ORDER BY fid LIMIT 1",
                (bbl,),
            ).fetchone()
        if r is None:
            return None
        props = {k: r[k] for k in GEO_COLUMNS}
        props["has_ll84"] = bool(r["has_ll84"])
        for k in ENERGY_COLUMNS + MODELED_COLUMNS:
            props[k] = r[k]
        return {
            "type": "Feature",
            "geometry": json.loads(r["geom"]),
            "properties": props,
        }


class PlutoStore:
    """MapPLUTO attribute-only lookups keyed by BBL (building profile panel).

    Serves owner type/name, assessed value (land/total/exempt), landmark
    status, community district, ZIP, and self-reported address. Attribute-only
    by design: no geometry here, the footprint layer already dominates the
    click path. A missing BBL is a legitimate answer (condo lots, odd records)
    — the endpoint reports `pluto: null` rather than failing the profile.
    """

    COLUMNS = (
        "borough", "block", "lot", "lot_area", "bldg_area", "built_far",
        "num_bldgs", "num_floors", "year_built", "land_use", "bldg_class",
        "zone_dist1", "zone_dist2", "ownertype", "ownername",
        "assess_land", "assess_total", "exempt_total", "landmark",
        "condo_no", "cd", "zip_code", "address",
    )

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        return db

    def by_bbl(self, bbl: str) -> dict | None:
        with self._lock:
            db = self._connect()
            r = db.execute(
                f"SELECT {', '.join(self.COLUMNS)} FROM lots WHERE bbl=?",
                (bbl,),
            ).fetchone()
        if r is None:
            return None
        return {k: r[k] for k in self.COLUMNS}
