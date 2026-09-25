"""Snapshot store: loads and indexes the raw pilot JSONL once at startup.

Grain note: one LL84 row = one *reporting property* (may be a campus covering
multiple buildings/BINs). No aggregation across footprints happens here.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from .numbers import parse_coord, parse_number, parse_text


@dataclass(frozen=True)
class PropertyRecord:
    property_id: str
    parent_property_id: str | None
    address_1: str | None
    postal_code: str | None
    borough_block_lot_raw: str | None
    bbl: str | None            # canonical 10-digit, first parseable BBL
    bin: str | None            # first parseable 7-digit BIN
    bin_count: int             # # BINs found (multi-BIN = campus w/ several bldgs)
    report_year: str | None
    gfa_sqft: float | None
    site_eui_kbtu_ft: float | None
    weather_normalized_site_eui: float | None
    total_ghg_tco2e: float | None
    direct_ghg_tco2e: float | None
    electricity_kbtu: float | None
    natural_gas_kbtu: float | None
    latitude: float | None
    longitude: float | None
    search_blob: str = field(default="")
    missing: tuple[str, ...] = field(default=())


def _first_bin(raw: str | None) -> tuple[str | None, int]:
    if raw is None:
        return None, 0
    first: str | None = None
    count = 0
    for part in str(raw).split(";"):
        part = part.strip()
        if part.isdigit() and len(part) == 7 and part != "0000000" and int(part) > 0:
            if count == 0:
                first = part
            count += 1
    return (first, count)


def _first_bbl(raw: str | None) -> str | None:
    if raw is None:
        return None
    for part in str(raw).replace(";", " ").split():
        part = part.strip()
        if part.isdigit() and len(part) == 10 and part[0] in "12345":
            return part
        if part.count(".") == 2:
            cand = "".join(part.split("."))
            if cand.isdigit() and len(cand) == 10 and cand[0] in "12345":
                return cand
    return None


class SnapshotStore:
    def __init__(self, raw_path: Path, manifest_path: Path | None = None) -> None:
        manifest: dict = {}
        if manifest_path and manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
        self.manifest = manifest
        self.rows: list[PropertyRecord] = []
        self.sha256 = manifest.get("sha256") or self._file_sha(raw_path)
        self.snapshot_utc = manifest.get("captured_utc")
        self._load(raw_path)

    @staticmethod
    def _file_sha(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    def _load(self, raw_path: Path) -> None:
        with raw_path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                raw = json.loads(line)
                row = self._transform(raw)
                self.rows.append(row)

        self.rows.sort(key=lambda r: r.address_1 or "")
        self.by_id: dict[str, PropertyRecord] = {r.property_id: r for r in self.rows}
        self.by_bin: dict[str, str] = {}
        self.by_bbl: dict[str, str] = {}
        for r in self.rows:
            if r.bin:
                self.by_bin[r.bin] = r.property_id
            if r.bbl:
                # campus rows may double-map a BBL; first writer wins (stable order)
                self.by_bbl.setdefault(r.bbl, r.property_id)

    @staticmethod
    def _transform(raw: dict) -> PropertyRecord:
        fid = parse_text(raw.get("property_id")) or ""
        lat = parse_coord(raw.get("latitude"))
        lon = parse_coord(raw.get("longitude"))
        bbl_raw = parse_text(raw.get("nyc_borough_block_and_lot"))
        bin_raw = parse_text(raw.get("nyc_building_identification"))
        bin_, bin_count = _first_bin(bin_raw)
        bbl = _first_bbl(bbl_raw)

        missing: list[str] = []
        check = {
            "site_eui_kbtu_ft": parse_number(raw.get("site_eui_kbtu_ft")),
            "total_location_based_ghg": parse_number(raw.get("total_location_based_ghg")),
            "natural_gas_use_kbtu": parse_number(raw.get("natural_gas_use_kbtu")),
            "electricity_use_grid_purchase": parse_number(raw.get("electricity_use_grid_purchase")),
        }
        for k, v in check.items():
            if v is None:
                missing.append(k)

        blob = " ".join(
            x for x in (
                fid,
                bbl_raw or "",
                bin_raw if bin_raw and bin_raw != bin_ else "",
                parse_text(raw.get("address_1")) or "",
                bin_ or "",
                bbl or "",
            )
            if x
        ).lower()

        return PropertyRecord(
            property_id=fid,
            parent_property_id=parse_text(raw.get("parent_property_id")),
            address_1=parse_text(raw.get("address_1")),
            postal_code=parse_text(raw.get("postal_code")),
            borough_block_lot_raw=bbl_raw,
            bbl=bbl,
            bin=bin_,
            bin_count=bin_count,
            report_year=parse_text(raw.get("report_year")),
            gfa_sqft=parse_number(raw.get("property_gfa_self_reported")),
            site_eui_kbtu_ft=check["site_eui_kbtu_ft"],
            weather_normalized_site_eui=parse_number(raw.get("weather_normalized_site_eui")),
            total_ghg_tco2e=check["total_location_based_ghg"],
            direct_ghg_tco2e=parse_number(raw.get("direct_ghg_emissions_metric")),
            electricity_kbtu=check["electricity_use_grid_purchase"],
            natural_gas_kbtu=check["natural_gas_use_kbtu"],
            latitude=lat,
            longitude=lon,
            search_blob=blob,
            missing=tuple(missing),
        )

    # ---- queries (all bounded) ------------------------------------------------

    def search(
        self,
        q: str | None = None,
        has_field: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[list[PropertyRecord], int]:
        limit = min(limit, 400)
        offset = max(offset, 0)
        needle = (q or "").strip().lower()
        out = [r for r in self.rows if not needle or needle in r.search_blob]
        if has_field:
            key = has_field.strip()
            if key not in _FIELD_KEYS:
                raise KeyError(key)
            out = [r for r in out if r.property_id and key in r.missing]
        total = len(out)
        return out[offset:offset + limit], total


_FIELD_KEYS = {
    "site_eui_kbtu_ft",
    "total_location_based_ghg",
    "natural_gas_use_kbtu",
    "electricity_use_grid_purchase",
}
