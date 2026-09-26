"""FastAPI application factory for the SignalNYC explorer."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .models import (
    Counters,
    ErrorResponse,
    PropertyDetail,
    PropertySummary,
    SearchResponse,
    SnapshotInfo,
)
from .store import _FIELD_KEYS, SnapshotStore

DEFAULT_RAW = Path(
    "/mnt/e/OC_Projects/projects/signalnyc/data/snapshots/ll84_2024_midtown_core.raw.jsonl"
)
DEFAULT_MANIFEST = Path(
    "/mnt/e/OC_Projects/projects/signalnyc/data/snapshots/ll84_2024_midtown_core.manifest.json"
)
DEFAULT_WEB = Path("/mnt/e/OC_Projects/projects/signalnyc/web/dist")

_SEM = {"kBtu": "kBtu", "kbtu": "kBtu"}


def _default_manifest_for(raw_path: Path) -> Path:
    """ll84_2024_midtown_core.raw.jsonl -> ll84_2024_midtown_core.manifest.json"""
    name = raw_path.name
    stem = name
    for suffix in (".raw.jsonl", ".jsonl", ".json"):
        if name.endswith(suffix):
            stem = name[: -len(suffix)]
            break
    return raw_path.parent / f"{stem}.manifest.json"


def create_app(
    raw_path: Path | None = None,
    manifest_path: Path | None = None,
    web_dist: Path | None = None,
) -> FastAPI:
    raw_path = Path(
        raw_path
        or os.environ.get("NYCDECO_SNAPSHOT")
        or os.environ.get("SIGNALNYC_SNAPSHOT")
        or DEFAULT_RAW
    )
    manifest_path = Path(
        manifest_path
        or os.environ.get("NYCDECO_MANIFEST")
        or os.environ.get("SIGNALNYC_MANIFEST")
        or str(_default_manifest_for(raw_path))
    )
    web_dist = Path(
        web_dist
        or os.environ.get("NYCDECO_WEB_DIST")
        or os.environ.get("SIGNALNYC_WEB_DIST")
        or DEFAULT_WEB
    )

    store = SnapshotStore(raw_path, manifest_path)
    row_by: dict[str, str] = {}
    for r in store.rows:
        row_by[r.property_id] = r.property_id

    def counters() -> Counters:
        rows = store.rows
        return Counters(
            total=len(rows),
            with_coordinates=sum(1 for r in rows if r.latitude is not None and r.longitude is not None),
            has_eui=sum(1 for r in rows if r.site_eui_kbtu_ft is not None),
            has_ghg=sum(1 for r in rows if r.total_ghg_tco2e is not None),
            has_gas=sum(1 for r in rows if r.natural_gas_kbtu is not None),
            has_electricity=sum(1 for r in rows if r.electricity_kbtu is not None),
            multi_bin_properties=sum(1 for r in rows if r.bin_count > 1),
        )

    def to_summary(r) -> PropertySummary:  # type: ignore[no-untyped-def]
        return PropertySummary(
            property_id=r.property_id,
            address_1=r.address_1,
            bbl=r.bbl,
            bin=r.bin,
            bin_count=r.bin_count,
            latitude=r.latitude,
            longitude=r.longitude,
            gfa_sqft=r.gfa_sqft,
            site_eui_kbtu_ft=r.site_eui_kbtu_ft,
            total_ghg_tco2e=r.total_ghg_tco2e,
            electricity_kbtu=r.electricity_kbtu,
            natural_gas_kbtu=r.natural_gas_kbtu,
            missing_fields=list(r.missing),
        )

    def to_detail(r) -> PropertyDetail:  # type: ignore[no-untyped-def]
        base = to_summary(r).model_dump()
        return PropertyDetail(
            **base,
            parent_property_id=r.parent_property_id,
            postal_code=r.postal_code,
            borough_block_lot_raw=r.borough_block_lot_raw,
            report_year=r.report_year,
            weather_normalized_site_eui=r.weather_normalized_site_eui,
            direct_ghg_tco2e=r.direct_ghg_tco2e,
        )

    app = FastAPI(
        title="SignalNYC Explorer API",
        version="0.1.0",
        description=(
            "Read-only explorer over one LL84 annual-2024 pilot snapshot. "
            "Disclaimers: observed annual data, preliminary explorer; property-level "
            "metrics; no LL97 compliance, savings, or scenario math."
        ),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.get(
        "/api/snapshot",
        response_model=SnapshotInfo,
        responses={500: {"model": ErrorResponse}},
    )
    def api_snapshot() -> SnapshotInfo:
        m = store.manifest
        return SnapshotInfo(
            snapshot_utc=store.snapshot_utc,
            sha256=store.sha256,
            rows=len(store.rows),
            slice_name=m.get("slice"),
            socrata_dataset=m.get("socrata_id"),
        )

    @app.get("/api/counters", response_model=Counters)
    def api_counters() -> Counters:
        return counters()

    @app.get(
        "/api/properties",
        response_model=SearchResponse,
        responses={400: {"model": ErrorResponse}},
    )
    def api_properties(
        q: str | None = Query(default=None, max_length=200),
        missing_field: str | None = Query(default=None),
        limit: int = Query(default=200, ge=1, le=400),
        offset: int = Query(default=0, ge=0),
    ) -> SearchResponse:
        try:
            rows, total = store.search(
                q=q, has_field=missing_field, limit=limit, offset=offset
            )
        except KeyError:
            raise HTTPException(
                status_code=400,
                detail=(
                    "missing_field must be one of "
                    + ", ".join(sorted(_FIELD_KEYS))
                ),
            )
        return SearchResponse(
            query=q,
            missing_field=missing_field,
            total=total,
            returned=len(rows),
            offset=offset,
            limit=limit,
            properties=[to_summary(r) for r in rows],
        )

    @app.get(
        "/api/properties/{property_id}",
        response_model=PropertyDetail,
        responses={404: {"model": ErrorResponse}},
    )
    def api_property_detail(property_id: str) -> PropertyDetail:
        # safe lookup: exact key match in a dict built from real IDs; no string
        # interpolation into storage, and path params are strict str already.
        if property_id not in row_by:
            raise HTTPException(status_code=404, detail="property_id not found in snapshot")
        return to_detail(store.by_id[property_id])

    # ---- citywide ParcelStore (MapPLUTO + citywide LL84 join) -----------------
    from .citywide_parcels import ParcelStore
    from .citywide_footprints import FootprintStore, PlutoStore
    from .citywide_demand import DemandStore

    _citywide_store: ParcelStore | None = None
    _footprint_store: FootprintStore | None = None
    _demand_store_ref: DemandStore | None = None
    _pluto_store: PlutoStore | None = None

    def _parcel_store() -> ParcelStore:
        nonlocal _citywide_store
        if _citywide_store is None:
            _citywide_store = ParcelStore()
        return _citywide_store

    def _fp_store() -> FootprintStore:
        nonlocal _footprint_store
        if _footprint_store is None:
            _footprint_store = FootprintStore()
        return _footprint_store

    def _pluto() -> PlutoStore:
        nonlocal _pluto_store
        if _pluto_store is None:
            _pluto_store = PlutoStore(
                "/mnt/e/OC_Projects/projects/signalnyc/data/citywide/mappluto_lots.sqlite"
            )
        return _pluto_store

    def _demand_store() -> DemandStore:
        nonlocal _demand_store_ref
        if _demand_store_ref is None:
            _demand_store_ref = DemandStore()
        return _demand_store_ref

    # ---- citywide footprints (all DOB buildings, not just the pilot) --------
    @app.get("/api/footprints/bbox", include_in_schema=True)
    def api_footprints_bbox(
        min_x: float = Query(..., ge=-180, le=180),
        min_y: float = Query(..., ge=-90, le=90),
        max_x: float = Query(..., ge=-180, le=180),
        max_y: float = Query(..., ge=-90, le=90),
        limit: int = Query(default=12000, ge=1, le=40000),
    ):
        if min_x >= max_x or min_y >= max_y:
            raise HTTPException(status_code=400, detail="bbox min must be < max")
        try:
            store = _fp_store()
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"citywide footprint store unavailable: {e}")
        try:
            feats, matched, truncated = store.query_bbox(min_x, min_y, max_x, max_y, limit)
        except FileNotFoundError as e:
            raise HTTPException(status_code=503, detail=str(e))
        return {
            "type": "FeatureCollection",
            "bbox": [min_x, min_y, max_x, max_y],
            "returned": len(feats),
            "matched": matched,
            "truncated": truncated,
            "features": feats,
        }

    @app.get("/api/districts", include_in_schema=True)
    def api_districts(kind: str | None = None):
        """Predefined study-area boundaries (BIDs, and campuses once derived).

        Served whole rather than by bbox: the whole BID layer is ~2.4 MB and a
        study selection needs the complete list of names, not just what is in
        view. `kind` filters to one boundary family.
        """
        from .districts import load_districts

        try:
            fc, meta = load_districts()
        except FileNotFoundError as e:
            raise HTTPException(status_code=503, detail=str(e))
        if kind:
            fc = {
                **fc,
                "features": [
                    f for f in fc["features"] if f["properties"].get("kind") == kind
                ],
            }
        return {**fc, "returned": len(fc["features"]), "meta": meta}

    @app.get("/api/districts_portfolio", include_in_schema=True)
    def api_districts_portfolio(kind: str | None = None):
        """Per-district portfolio aggregates for the districts table tab.

        One row per study district (BID or campus) with member counts and
        footprint/LL84 aggregates. Served from the precomputed summary build
        (scripts/build_districts_summary.py) — computing 273 point-in-polygon
        joins per request would be absurd. Membership convention and grain
        caveats ride along in the payload's `note`.
        """
        path = Path(
            "/mnt/e/OC_Projects/projects/signalnyc/data/citywide/districts_summary.json"
        )
        if not path.exists():
            raise HTTPException(
                status_code=503,
                detail="districts_summary.json not built — run scripts/build_districts_summary.py",
            )
        data = json.loads(path.read_text())
        rows = data["districts"]
        if kind:
            rows = [r for r in rows if r.get("kind") == kind]
        return {**data, "districts": rows, "returned": len(rows)}


    @app.get("/api/districts/{district_id}", include_in_schema=True)
    def api_district_detail(district_id: str):
        """One district plus the footprints inside it (for the study view)."""
        from .districts import geom_bbox, load_districts

        try:
            fc, _ = load_districts()
            store = _fp_store()
        except FileNotFoundError as e:
            raise HTTPException(status_code=503, detail=str(e))
        feat = next(
            (f for f in fc["features"] if f["properties"].get("district_id") == district_id),
            None,
        )
        if feat is None:
            raise HTTPException(status_code=404, detail=f"unknown district: {district_id}")
        bb = geom_bbox(feat["geometry"])
        if bb is None:
            raise HTTPException(status_code=500, detail="district geometry has no coordinates")
        feats, matched, truncated = store.query_bbox(bb[0], bb[1], bb[2], bb[3], 40000)
        return {
            "type": "FeatureCollection",
            "district": feat["properties"],
            "geometry": feat["geometry"],
            "footprints": feats,
            "footprints_in_bbox": matched,
            "truncated": truncated,
        }

    @app.get("/api/building/{bbl}", include_in_schema=True)
    def api_building_detail(bbl: str):
        """Everything known about ONE building, keyed by BBL.

        Map-first entry point: a click on a footprint yields a BBL, and the vast
        majority of the 1,083,047 citywide footprints have no row in the LL84
        slice that backs the property table. Without this endpoint a click on
        almost any building resolved to nothing, so the table could only be
        reached from a search that already knew the property.

        Returns footprint identity (BIN/name/height), the observed LL84 join
        when one exists, and the modeled demand when one exists — with explicit
        flags distinguishing "not modeled" from "zero", and LL84's
        "not required to report" from "reports nothing".
        """
        store = _fp_store()
        rec = store.by_bbl(bbl)
        if rec is None:
            raise HTTPException(status_code=404, detail=f"no footprint for BBL {bbl}")

        props = rec["properties"]
        bin_id = props.get("bin")

        # Observed LL84 (property-level) — may legitimately be absent.
        observed = None
        try:
            parcel = _parcel_store().by_bbl_record(bbl)
            if parcel:
                observed = parcel
        except FileNotFoundError:
            observed = None

        # Modeled annual demand (BBL-keyed) — separate store from observed.
        modeled = None
        try:
            demand = _demand_store().by_bbl(bbl)
            if demand:
                modeled = demand
        except FileNotFoundError:
            modeled = None

        # Building profile: parcel attributes (owner/value/vintage/zoning) —
        # map-grain, honest about absence (pluto: null on odd BBLs).
        pluto = None
        try:
            pluto = _pluto().by_bbl(bbl)
        except sqlite3.Error:
            pluto = None

        return {
            "bbl": bbl,
            "bin": bin_id,
            "geometry": rec.get("geometry"),
            "pluto": pluto,
            "footprint": {
                k: props.get(k)
                for k in (
                    "name", "height_roof", "construction_year",
                    "shape_area", "has_ll84",
                )
            },
            "observed": observed,
            "modeled": modeled,
            "evidence": {
                # Distinguish the two independent absences. Collapsing them is
                # how a building with no obligation to benchmark gets presented
                # as a building that reported nothing.
                "has_footprint": True,
                "has_ll84_join": bool(props.get("has_ll84")),
                "ll84_note": (
                    "LL84 benchmarks buildings above the LL97 size threshold; "
                    "absent means 'not required to report', not 'zero energy'."
                ),
                "has_modeled_demand": modeled is not None,
                "modeled_note": (
                    "Modeled annual end-use demand, not a measurement. Null means "
                    "not modeled, never zero."
                ),
            },
        }

    @app.get("/api/footprints/coverage", include_in_schema=True)
    def api_footprints_coverage():
        try:
            return _fp_store().coverage()
        except FileNotFoundError as e:
            raise HTTPException(status_code=503, detail=str(e))

    @app.get("/api/footprints/bin/{bin_id}", include_in_schema=True)
    def api_footprint_by_bin(bin_id: str):
        try:
            store = _fp_store()
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"citywide footprint store unavailable: {e}")
        try:
            feat = store.by_bin(bin_id)
        except FileNotFoundError as e:
            raise HTTPException(status_code=503, detail=str(e))
        if feat is None:
            raise HTTPException(status_code=404, detail=f"no footprint for BIN {bin_id}")
        return feat

    @app.get("/api/parcels", include_in_schema=True)
    def api_parcels(
        min_x: float = Query(..., ge=-180, le=180),
        min_y: float = Query(..., ge=-90, le=90),
        max_x: float = Query(..., ge=-180, le=180),
        max_y: float = Query(..., ge=-90, le=90),
        limit: int = Query(default=5000, ge=1, le=20000),
    ):
        if min_x >= max_x or min_y >= max_y:
            raise HTTPException(status_code=400, detail="bbox min must be < max")
        try:
            store = _parcel_store()
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"citywide parcel store unavailable: {e}")
        try:
            feats, total, truncated = store.query_bbox(min_x, min_y, max_x, max_y, limit)
        except FileNotFoundError as e:
            raise HTTPException(status_code=503, detail=str(e))
        return {
            "type": "FeatureCollection",
            "bbox": [min_x, min_y, max_x, max_y],
            "returned": len(feats),
            "matched": total,
            "truncated": truncated,
            "features": feats,
        }

    @app.get("/api/parcels/coverage", include_in_schema=True)
    def api_parcels_coverage():
        try:
            return _parcel_store().coverage()
        except FileNotFoundError as e:
            raise HTTPException(status_code=503, detail=str(e))

    @app.get("/api/parcels/{bbl}", include_in_schema=True)
    def api_parcel_detail(bbl: str):
        store = _parcel_store()
        try:
            rec = store.by_bbl_record(bbl)
        except FileNotFoundError as e:
            raise HTTPException(status_code=503, detail=str(e))
        if rec is None:
            raise HTTPException(status_code=404, detail=f"no LL84 record joined for BBL {bbl}")
        return rec

    # DEV-160: footprints served WITH modeled annual demand fields joined by pid
    # (space_heating/dhw/cooling + _ft2_yr + evidence_tier). Falls back to the
    # bare observed snapshot when the merged file hasn't been generated yet.
    _footprints_path = Path(
        os.environ.get("NYCDECO_FOOTPRINTS")
        or os.environ.get(
            "SIGNALNYC_FOOTPRINTS",
            str(Path(DEFAULT_RAW).parent / "footprints_joined_demand.geojson"),
        )
    )
    if not _footprints_path.exists():
        _footprints_path = Path(
            Path(DEFAULT_RAW).parent / "footprints_joined.geojson"
        )

    @app.get("/api/footprints", include_in_schema=True)
    def api_footprints() -> FileResponse:
        if not _footprints_path.exists():
            raise HTTPException(status_code=404, detail="footprint layer not available for this snapshot")
        return FileResponse(_footprints_path, media_type="application/geo+json")

    # ---- static frontend (prod build), mounted last --------------------------
    if web_dist.exists():
        app.mount("/assets", StaticFiles(directory=web_dist / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str) -> FileResponse:
            candidate = web_dist / full_path
            if full_path and candidate.is_file() and full_path.startswith(("index", "favicon", "vite", "manifest")):
                return FileResponse(candidate)
            return FileResponse(web_dist / "index.html")
    return app
