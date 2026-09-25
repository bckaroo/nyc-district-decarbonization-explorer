"""FastAPI application factory for the SignalNYC explorer."""

from __future__ import annotations

import os
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
    raw_path = Path(raw_path or os.environ.get("SIGNALNYC_SNAPSHOT", DEFAULT_RAW))
    manifest_path = Path(
        manifest_path
        or os.environ.get("SIGNALNYC_MANIFEST", str(_default_manifest_for(raw_path)))
    )
    web_dist = Path(web_dist or os.environ.get("SIGNALNYC_WEB_DIST", DEFAULT_WEB))

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
