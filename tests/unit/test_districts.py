"""Unit tests for the predefined district study layers (BIDs + campuses).

The point of these tests is the SEMANTICS as much as the plumbing: these are
study-area boundaries, and the payloads must say so. A BID is an administrative
district; a campus is a derived ownership cluster. Neither is a thermal district
and neither proves shared heating/cooling infrastructure. If someone later
"cleans up" those disclaimers, these tests fail.
"""
import json
from pathlib import Path

import pytest

from nyc_decarbonization.api import districts as D


def _poly(x, y, d=0.001):
    return {
        "type": "Polygon",
        "coordinates": [[[x, y], [x + d, y], [x + d, y + d], [x, y + d], [x, y]]],
    }


@pytest.fixture()
def mini(tmp_path, monkeypatch):
    """Isolate the module from real data files.

    The paths are redirected to tmp_path BEFORE anything is written or deleted.
    Writing first (or unlinking) against the module's real constants would
    clobber production data — a test must never touch `data/citywide/`.
    """
    monkeypatch.setattr(D, "DISTRICT_DIR", tmp_path)
    monkeypatch.setattr(D, "BIDS", tmp_path / "bids.geojson")
    monkeypatch.setattr(D, "CAMPUSES", tmp_path / "campuses.geojson")
    monkeypatch.setattr(D, "OWNER_CLUSTERS", tmp_path / "owner_clusters.geojson")

    bids = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": _poly(-73.99, 40.75),
                "properties": {
                    "bid_name": "Test BID",
                    "borough": "Manhattan",
                    "website": "https://example.org",
                    "year_found": 1995,
                    "kind": "bid",
                },
            }
        ],
    }
    campuses = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": _poly(-73.98, 40.76),
                "properties": {
                    "campus_name": "Agency cluster (5 lots)",
                    "owner_key": "DCAS",
                    "lot_count": 5,
                    "bldg_area_sqft": 250000,
                    "kind": "campus",
                },
            }
        ],
    }
    owner = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": _poly(-73.97, 40.77),
                "properties": {
                    "campus_name": "Vornado Realty Trust (4 lots)",
                    "owner_key": "vornado",
                    "owner_kind": "private",
                    "lot_count": 4,
                    "bldg_area_sqft": 900000,
                    "kind": "campus",
                },
            }
        ],
    }
    D.BIDS.write_text(json.dumps(bids), encoding="utf-8")
    D.CAMPUSES.write_text(json.dumps(campuses), encoding="utf-8")
    D.OWNER_CLUSTERS.write_text(json.dumps(owner), encoding="utf-8")

    D.reset_cache()
    yield
    D.reset_cache()


def test_fixture_never_touches_production_paths(mini):
    """Regression guard: an earlier version of this fixture wrote to the module's
    real data paths and deleted the served BID/campus GeoJSON files."""
    real = Path(D.__file__).resolve().parents[3] / "data" / "citywide"
    assert "/tmp" in str(D.BIDS) or "pytest" in str(D.BIDS), (
        "BIDS must point into tmp_path during tests, never at data/citywide/"
    )
    assert D.BIDS != real / "bids.geojson"
    assert D.CAMPUSES != real / "campuses.geojson"
    assert D.OWNER_CLUSTERS != real / "owner_clusters.geojson"


def test_loads_all_three_families(mini):
    fc, meta = D.load_districts()
    assert meta["bid_count"] == 1
    # agency campuses + owner clusters both present
    assert meta["campus_count"] == 2
    assert meta["total"] == 3
    kinds = sorted(f["properties"]["kind"] for f in fc["features"])
    assert kinds == ["bid", "campus", "campus"]


def test_every_feature_carries_a_study_area_disclaimer(mini):
    """No consumer should have to infer that these are not thermal districts."""
    fc, _ = D.load_districts()
    for f in fc["features"]:
        d = f["properties"].get("disclaimer") or ""
        assert d, "every district feature must carry a disclaimer"
        assert "not a thermal district" in d.lower() or "not evidence" in d.lower()


def test_bid_is_marked_administrative(mini):
    fc, _ = D.load_districts()
    bid = next(f for f in fc["features"] if f["properties"]["kind"] == "bid")
    assert bid["properties"]["boundary_type"] == "administrative"


def test_campus_is_marked_ownership_cluster(mini):
    fc, _ = D.load_districts()
    cams = [f for f in fc["features"] if f["properties"]["kind"] == "campus"]
    assert cams
    for c in cams:
        assert c["properties"]["boundary_type"] == "ownership-cluster"


def test_district_ids_are_prefixed_and_unique(mini):
    fc, _ = D.load_districts()
    ids = [f["properties"]["district_id"] for f in fc["features"]]
    assert len(ids) == len(set(ids)), "district_id must be unique"
    assert any(i.startswith("bid:") for i in ids)
    assert any(i.startswith("campus:") for i in ids)


def test_provenance_distinguishes_colp_from_ownername(mini):
    """COLP is an official agency record; ownername is an editorial family rule.
    Losing that distinction would overstate the authority of private clusters."""
    fc, _ = D.load_districts()
    cams = [f["properties"] for f in fc["features"] if f["properties"]["kind"] == "campus"]
    prov = {c["provenance"] for c in cams}
    assert prov == {"colp_agency", "pluto_ownername"}


def test_bbox_of_multipolygon(mini):
    geom = {
        "type": "MultiPolygon",
        "coordinates": [[_poly(0, 0)["coordinates"]], [_poly(2, 3)["coordinates"]]],
    }
    bb = D.geom_bbox(geom)
    assert bb == (0.0, 0.0, 2.001, 3.001)


def test_bbox_handles_empty_geometry():
    assert D.geom_bbox({}) is None
    assert D.geom_bbox({"type": "Polygon", "coordinates": []}) is None


def test_missing_owner_clusters_does_not_break_load(mini, tmp_path):
    """A partially built dataset must still serve what exists."""
    D.OWNER_CLUSTERS.unlink()
    D.reset_cache()
    fc, meta = D.load_districts()
    assert meta["campus_owner_count"] == 0
    assert "missing_owner_clusters" in meta
    assert any(f["properties"]["kind"] == "bid" for f in fc["features"])


def test_all_files_missing_raises(mini):
    D.BIDS.unlink()
    D.CAMPUSES.unlink()
    D.OWNER_CLUSTERS.unlink()
    D.reset_cache()
    with pytest.raises(FileNotFoundError):
        D.load_districts()
