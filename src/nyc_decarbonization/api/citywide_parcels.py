"""Citywide MapPLUTO parcel store: bbox query, coverage, LL84 join.

Reads data/citywide/mappluto_lots.sqlite (bbox columns + indexes built by
scripts/fetch_citywide_mappluto.py and the bbox backfill round). Keeps source
units verbatim and never infers heating/cooling end-use loads: LL84 provides
measured whole-building totals only.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
from pathlib import Path

DB_PATH = Path("/mnt/e/OC_Projects/projects/signalnyc/data/citywide/mappluto_lots.sqlite")
LL84_PATH = Path(
    "/mnt/e/OC_Projects/projects/signalnyc/data/citywide/ll84_citywide_2024.raw.jsonl"
)

# LL84 fields the citywide layer joins, with EXACT source columns + units.
# Electricity variants (source-doc fact, preserved verbatim):
#   electricity_use_grid_purchase     -> kBtu  (unsuffixed)
#   electricity_use_grid_purchase_1   -> kWh
#   electricity_use_grid_purchase_3   -> kBtu, grid+onsite = DIFFERENT quantity
LL84_FIELDS = {
    "site_eui_kbtu_ft": {"unit": "kBtu/ft²", "basis": "measured annual whole-building"},
    "site_eui_normalized_kbtu_ft": {"unit": "kBtu/ft²", "basis": "weather-normalized annual"},
    "total_ghg_tco2e": {"unit": "tCO2e", "basis": "total location-based"},
    "direct_ghg_tco2e": {"unit": "tCO2e", "basis": "direct (on-site)"},
    "indirect_ghg_tco2e": {"unit": "tCO2e", "basis": "indirect (grid)"},
    "electricity_kbtu": {"unit": "kBtu", "source_field": "electricity_use_grid_purchase", "basis": "grid purchase"},
    "electricity_kwh": {"unit": "kWh", "source_field": "electricity_use_grid_purchase_1", "basis": "grid purchase"},
    "electricity_grid_onsite_kbtu": {"unit": "kBtu", "source_field": "electricity_use_grid_purchase_3", "basis": "grid + onsite total (different quantity from electricity_kbtu)"},
    "natural_gas_kbtu": {"unit": "kBtu", "source_field": "natural_gas_use_kbtu", "basis": "whole-building total, NOT heating-only"},
}
# Explicit availability metadata for the later map-symbology selector:
FIELD_AVAILABILITY = {
    "measured": sorted(
        k for k in LL84_FIELDS if k not in ("modeled_heating_kbtu", "modeled_cooling_kbtu", "modeled_domestic_hot_water_kbtu")
    ),
    "modeled": {
        "modeled_heating_kbtu": "not available in LL84 — building-level annual modeling follow-up pending",
        "modeled_cooling_kbtu": "not available in LL84 — building-level annual modeling follow-up pending",
        "modeled_domestic_hot_water_kbtu": "not available in LL84 — building-level annual modeling follow-up pending",
    },
}

_NUM_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*(?:[eE][-+]?\d+)?")


def _num(v, unit: str | None = None):
    """Parse a Socrata string/number, tolerant of commas. Returns float|None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = _NUM_RE.search(str(v).replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group())
    except ValueError:
        return None


def _first_bbl(raw) -> str | None:
    """Same shape as api.store._first_bbl: one BBL from the raw field."""
    if raw is None:
        return None
    text = str(raw).replace(";", " ")
    chosen: str | None = None
    for part in text.split():
        part = part.strip()
        if part.isdigit() and len(part) == 10 and part[0] in "12345":
            return part
        if part.count(".") == 2:
            cand = "".join(part.split("."))
            if cand.isdigit() and len(cand) == 10 and cand[0] in "12345":
                chosen = cand  # keep scanning for plain form first
    return chosen


def _rings_of(geom: dict) -> list[list[list[float]]]:
    cs = geom.get("coordinates") or []
    if not cs:
        return []
    if isinstance(cs[0][0][0], (int, float)):
        return cs  # Polygon
    rings: list[list[list[float]]] = []
    for poly in cs:
        if poly and isinstance(poly[0][0][0], (int, float)):
            rings.extend(poly)
        else:  # deeper nesting: recurse once more
            for p2 in poly:
                rings.extend(p2)
    return rings


def _geom_intersects_bbox(geom: dict, min_x: float, min_y: float, max_x: float, max_y: float) -> bool:
    """True if any ring has at least one point inside the bbox. Point-in-box per
    vertex is a cheap conservative-overlap test that never over-selects."""
    for ring in _rings_of(geom):
        for p in ring:
            if min_x <= p[0] <= max_x and min_y <= p[1] <= max_y:
                return True
        # also test segment crossings (edge may cross box with no vertex inside)
        for i in range(len(ring) - 1):
            x0, y0 = ring[i]
            x1, y1 = ring[i + 1]
            if max(x0, x1) < min_x or min(x0, x1) > max_x:
                continue
            if max(y0, y1) < min_y or min(y0, y1) > max_y:
                continue
            # segment's bbox overlaps target bbox: check actual intersection
            if _seg_intersects_box(x0, y0, x1, y1, min_x, min_y, max_x, max_y):
                return True
    return False


def _seg_intersects_box(x0, y0, x1, y1, min_x, min_y, max_x, max_y) -> bool:
    # Liang-Barsky clip test
    dx, dy = x1 - x0, y1 - y0
    p = [-dx, dx, -dy, dy]
    q = [x0 - min_x, max_x - x0, y0 - min_y, max_y - y0]
    t0, t1 = 0.0, 1.0
    for pi, qi in zip(p, q):
        if pi == 0:
            if qi < 0:
                return False
            continue
        t = qi / pi
        if pi < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return False
    return True


class ParcelStore:
    """Bbox + id lookups over citywide MapPLUTO lots; optional LL84 join."""

    def __init__(self, db_path: Path | None = None, ll84_path: Path | None = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.ll84_path = Path(ll84_path) if ll84_path else LL84_PATH
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._by_bbl: dict[str, dict] = {}
        self._ll84_loaded = False

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _connect(self) -> sqlite3.Connection:
        # FastAPI serves these endpoints from the anyio worker threadpool, so the
        # shared long-lived connection may be used from a different thread than
        # the one that created it: allow cross-thread use and serialize access
        # (the conn is read-only, mode=ro, so serialization is just for safety).
        if self._conn is None:
            if not self.db_path.exists():
                raise FileNotFoundError(f"citywide parcels db missing: {self.db_path}")
            self._conn = sqlite3.connect(
                f"file:{self.db_path}?mode=ro", uri=True, timeout=30, check_same_thread=False
            )
            self._conn.row_factory = sqlite3.Row
        return self._conn

    # -- coverage ---------------------------------------------------------------
    def coverage(self) -> dict:
        with self._lock:
            db = self._connect()
            ll84 = self._coverage_ll84_locked()
        row = {"next_offset": None, "done": None}
        with sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=30) as probe:
            probe.row_factory = sqlite3.Row
            r = probe.execute("SELECT next_offset, done FROM progress WHERE id=1").fetchone()
            if r:
                row = {"next_offset": r["next_offset"], "done": r["done"]}
        lots_n = 0
        with sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=30) as probe:
            lots_n = probe.execute("SELECT COUNT(*) FROM lots").fetchone()[0]
        ll84_rows = 0
        if self.ll84_path.exists():
            with self.ll84_path.open("rb") as f:
                ll84_rows = sum(1 for _ in f)
        return {
            "source": "MAPPLUTO FeatureServer/0 (NYC DCP)",
            "source_total": 856687,
            "lots_fetched": lots_n,
            "cursor_next_offset": row["next_offset"],
            "cursor_done": row["done"],
            "status": "complete" if lots_n >= 856687 else "partial — resumable ingest",
            "ll84_citywide": {
                "dataset": "5zyy-y8am (CY2024, citywide, all boroughs)",
                "raw_rows_on_disk": ll84_rows,
                "joined_distinct_bbl": ll84,
                "join_strategy": (
                    "first BBL from nyc_borough_block_and_lot; first-writer-wins per BBL; "
                    "parent_property_id preserved for campus dedup"
                ),
            },
            "field_availability": FIELD_AVAILABILITY,
            "modeled_end_uses": {
                "status": "not present in LL84 source; building-level annual modeling follow-up pending",
                "heating": "modeled_heating_kbtu=null until follow-up",
                "cooling": "modeled_cooling_kbtu=null until follow-up",
                "domestic_hot_water": "modeled_domestic_hot_water_kbtu=null until follow-up",
            },
        }

    def _coverage_ll84_locked(self) -> int:
        self._ensure_ll84()
        return len(self._by_bbl)

    def _ensure_ll84_count(self) -> int:
        self._ensure_ll84()
        return len(self._by_bbl)

    # -- LL84 join --------------------------------------------------------------
    def _ensure_ll84(self) -> None:
        if self._ll84_loaded:
            return
        if self.ll84_path.exists():
            with self.ll84_path.open() as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    raw = json.loads(line)
                    bbl = _first_bbl(raw.get("nyc_borough_block_and_lot"))
                    if not bbl:
                        continue
                    rec = {
                        "property_id": raw.get("property_id"),
                        "parent_property_id": raw.get("parent_property_id"),
                        "site_eui_kbtu_ft": _num(raw.get("site_eui_kbtu_ft")),
                        "site_eui_normalized_kbtu_ft": _num(raw.get("weather_normalized_site_eui")),
                        "total_ghg_tco2e": _num(raw.get("total_location_based_ghg")),
                        "direct_ghg_tco2e": _num(raw.get("direct_ghg_emissions_metric")),
                        "indirect_ghg_tco2e": _num(raw.get("indirect_ghg_emissions_metric_rn")),
                        "electricity_kbtu": _num(raw.get("electricity_use_grid_purchase")),
                        "electricity_kwh": _num(raw.get("electricity_use_grid_purchase_1")),
                        "electricity_grid_onsite_kbtu": _num(raw.get("electricity_use_grid_purchase_3")),
                        "natural_gas_kbtu": _num(raw.get("natural_gas_use_kbtu")),
                        "modeled_heating_kbtu": None,
                        "modeled_cooling_kbtu": None,
                        "modeled_domestic_hot_water_kbtu": None,
                    }
                    self._by_bbl.setdefault(bbl, rec)  # first-writer-wins
        self._ll84_loaded = True

    def by_bbl_record(self, bbl: str) -> dict | None:
        self._ensure_ll84()
        return self._by_bbl.get(bbl)

    # -- bbox parcel query ------------------------------------------------------
    def query_bbox(
        self,
        min_x: float,
        min_y: float,
        max_x: float,
        max_y: float,
        limit: int = 5000,
    ) -> tuple[list[dict], int, bool]:
        """True bbox filter using actual parcel bounds (not just centroids).

        Returns (features, total_matched, truncated). `total_matched` counts
        all overlaps even past the cap, so truncation is always accurate.
        """
        limit = max(1, min(int(limit), 20000))  # explicit cap
        feats: list[dict] = []
        total_traversed = 0
        with self._lock:
            db = self._connect()
            rows = db.execute(
                "SELECT bbl, borough, block, lot, lot_area, bldg_area, built_far,"
                " num_bldgs, num_floors, year_built, latitude, longitude,"
                " land_use, bldg_class, zone_dist1, zone_dist2, min_x, max_x, min_y, max_y, geom"
                " FROM lots INDEXED BY idx_lots_bbox"
                " WHERE min_x <= :max_x AND max_x >= :min_x AND min_y <= :max_y AND max_y >= :min_y"
                " ORDER BY min_x, min_y, max_x, max_y, bbl",
                {"min_x": min_x, "min_y": min_y, "max_x": max_x, "max_y": max_y},
            ).fetchall()
        self._ensure_ll84()
        for r in rows:
            total_traversed += 1
            if len(feats) >= limit:
                continue  # still count matches for an exact truncation flag
            geom = json.loads(r["geom"])
            if not _geom_intersects_bbox(geom, min_x, min_y, max_x, max_y):
                continue
            bbl = r["bbl"]
            rec = self._by_bbl.get(bbl)
            feats.append({
                "type": "Feature",
                "id": bbl,
                "geometry": geom,
                "properties": {
                    "bbl": bbl,
                    "borough": r["borough"],
                    "block": r["block"],
                    "lot": r["lot"],
                    "lot_area_sqft": r["lot_area"],
                    "bldg_area_sqft": r["bldg_area"],
                    "built_far": r["built_far"],
                    "num_bldgs": r["num_bldgs"],
                    "num_floors": r["num_floors"],
                    "year_built": r["year_built"],
                    "latitude": r["latitude"],
                    "longitude": r["longitude"],
                    "land_use": r["land_use"],
                    "bldg_class": r["bldg_class"],
                    "zone_dist1": r["zone_dist1"],
                    "zone_dist2": r["zone_dist2"],
                    "ll84": rec,
                },
            })
        truncated = total_traversed > limit
        return feats, total_traversed, truncated


def build_features_with_ll84(store: ParcelStore, min_x, min_y, max_x, max_y, limit=5000):
    """Convenience: query + attach LL84 recorded-field availability metadata."""
    feats, total, truncated = store.query_bbox(min_x, min_y, max_x, max_y, limit)
    for f in feats:
        rec = f["properties"].get("ll84")
        f["properties"]["ll84_availability"] = (
            [k for k, v in rec.items() if v is not None] if rec else []
        )
        f["properties"]["ll84_units"] = LL84_FIELDS
    return feats, total, truncated
