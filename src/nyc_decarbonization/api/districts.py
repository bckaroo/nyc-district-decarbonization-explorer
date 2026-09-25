"""Predefined district study areas: BID boundaries (and campus clusters).

Design note — what these are and are not
----------------------------------------
A BID boundary is an ADMINISTRATIVE district: a business-improvement taxing
and services area. It carries no implication of shared thermal plant.

A campus boundary is a CONTIGUITY-OF-OWNERSHIP cluster: neighbouring tax lots
under one owner/agency. That is a reasonable proxy for a shared-building
portfolio, and therefore for a plausible district-energy study area, but it is
still a proxy — it does not prove a common plant, common vintage, or a single
utility meter.

Both are presented as *study-area boundaries*, never as thermal districts or as
evidence of a district system. Every payload says so explicitly, and campus
records carry the derivation rule so the grouping can be reproduced or
challenged.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]  # api -> signalnyc -> src -> repo root
DISTRICT_DIR = REPO / "data" / "citywide"
BIDS = DISTRICT_DIR / "bids.geojson"
CAMPUSES = DISTRICT_DIR / "campuses.geojson"
OWNER_CLUSTERS = DISTRICT_DIR / "owner_clusters.geojson"

_lock = threading.Lock()
_cache: tuple[dict, dict] | None = None


def geom_bbox(geom: dict) -> tuple[float, float, float, float] | None:
    """Bounding box of a Polygon/MultiPolygon at any nesting depth."""
    if not geom:
        return None
    xs: list[float] = []
    ys: list[float] = []

    def walk(node):
        if isinstance(node, (list, tuple)):
            if len(node) >= 2 and isinstance(node[0], (int, float)):
                xs.append(float(node[0]))
                ys.append(float(node[1]))
            else:
                for child in node:
                    walk(child)

    walk(geom.get("coordinates"))
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _slug(text: str) -> str:
    out = []
    for ch in text.lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")


def _load_bids() -> tuple[list[dict], dict]:
    if not BIDS.exists():
        return [], {"bids": 0, "missing": str(BIDS)}
    fc = json.loads(BIDS.read_text(encoding="utf-8"))
    feats = []
    for f in fc.get("features", []):
        props = dict(f.get("properties") or {})
        name = props.get("bid_name") or "unnamed BID"
        props["district_id"] = f"bid:{_slug(name)}"
        props["kind"] = "bid"
        # State the semantics on every feature so no consumer has to infer them.
        props["boundary_type"] = "administrative"
        props["disclaimer"] = (
            "BID boundary = administrative district. Not a thermal district and "
            "not evidence of shared heating/cooling infrastructure."
        )
        feats.append({"type": "Feature", "geometry": f.get("geometry"), "properties": props})
    return feats, {"bids": len(feats), "source": str(BIDS.name)}


def _load_file(path: Path, name_field: str, default_kind: str) -> tuple[list[dict], dict]:
    """Load one district GeoJSON file, stamping the shared semantics on each feature."""
    if not path.exists():
        return [], {"missing": path.name}
    fc = json.loads(path.read_text(encoding="utf-8"))
    feats = []
    for f in fc.get("features", []):
        props = dict(f.get("properties") or {})
        name = props.get(name_field) or props.get("campus_name") or "unnamed"
        kind = props.get("kind") or default_kind
        props["district_id"] = f"{kind}:{_slug(str(name))}"
        props["kind"] = kind
        props.setdefault("boundary_type", "ownership-cluster")
        # State the semantic limit on every feature so no consumer has to infer
        # it — these are study areas, never thermal districts.
        props.setdefault(
            "disclaimer",
            "Study-area boundary only. Not a thermal district and not evidence "
            "of shared heating/cooling infrastructure.",
        )
        feats.append({"type": "Feature", "geometry": f.get("geometry"), "properties": props})
    return feats, {"features": len(feats), "source": path.name}


def _load_campuses() -> tuple[list[dict], dict]:
    """City-agency campuses (COLP-derived) and state/private/institution
    clusters (MapPLUTO ownername-derived). Same semantics, different source."""
    agency, ameta = _load_file(CAMPUSES, "campus_name", "campus")
    owner, ometa = _load_file(OWNER_CLUSTERS, "campus_name", "campus")
    for f in owner:
        # Distinguish provenance because the ownership signal differs: COLP is
        # an official agency record; ownername is an editorial family rule.
        f["properties"]["provenance"] = "pluto_ownername"
    for f in agency:
        f["properties"]["provenance"] = "colp_agency"
    meta = {
        "campus_agency_count": len(agency),
        "campus_owner_count": len(owner),
        **{f"campuses_{k}": v for k, v in ameta.items() if k == "source"},
        **{f"owner_clusters_{k}": v for k, v in ometa.items() if k == "source"},
    }
    if not agency:
        meta["missing_campuses"] = ameta.get("missing")
    if not owner:
        meta["missing_owner_clusters"] = ometa.get("missing")
    return agency + owner, meta


def load_districts() -> tuple[dict, dict]:
    """All predefined study boundaries as one FeatureCollection + metadata."""
    global _cache
    with _lock:
        if _cache is not None:
            return _cache

        bids, bmeta = _load_bids()
        campuses, cmeta = _load_campuses()
        feats = bids + campuses
        if not feats:
            raise FileNotFoundError(
                f"no district boundaries on disk (looked for {BIDS} and {CAMPUSES})"
            )

        meta = {
            "bid_count": len(bids),
            "campus_count": len(campuses),
            "total": len(feats),
            "kinds": {"bid": "administrative BID boundary", "campus": "ownership cluster"},
            "note": (
                "Study-area boundaries only. BIDs are administrative and campuses "
                "are derived ownership clusters; neither is a thermal district or "
                "evidence of shared heating/cooling infrastructure."
            ),
            **{k: v for k, v in bmeta.items() if k != "bids"},
            **cmeta,
        }
        _cache = ({"type": "FeatureCollection", "features": feats}, meta)
        return _cache


def reset_cache() -> None:
    """Drop the cached boundaries (used by tests and after a rebuild)."""
    global _cache
    with _lock:
        _cache = None


def district_footprints(store, district_id: str) -> dict | None:
    """Footprints inside one district's bbox.

    bbox is a deliberate approximation: a footprint straddling the boundary is
    included, and the client is told the count is bounding-box based rather
    than a true polygon containment test.
    """
    fc, _ = load_districts()
    feat = next(
        (f for f in fc["features"] if f["properties"].get("district_id") == district_id),
        None,
    )
    if feat is None:
        return None
    bb = geom_bbox(feat["geometry"])
    if bb is None:
        return None
    feats, matched, truncated = store.query_bbox(bb[0], bb[1], bb[2], bb[3], 40000)
    return {
        "district": feat["properties"],
        "geometry": feat["geometry"],
        "footprints": feats,
        "footprints_in_bbox": matched,
        "truncated": truncated,
        "basis": "bounding_box",
    }
