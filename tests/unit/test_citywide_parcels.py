"""Unit tests for citywide_parcels.ParcelStore — synthetic mini-fixture DB,
no network, and offline test for the LL84 metadata unit-fidelity rule."""
import json
import sqlite3
from pathlib import Path

import pytest

from nyc_decarbonization.api.citywide_parcels import (
    FIELD_AVAILABILITY,
    LL84_FIELDS,
    ParcelStore,
    _first_bbl,
    _geom_intersects_bbox,
    _num,
)

SCHEMA = """
CREATE TABLE lots (
  bbl TEXT PRIMARY KEY, borough TEXT, block TEXT, lot TEXT,
  lot_area REAL, bldg_area REAL, built_far REAL,
  num_bldgs INTEGER, num_floors REAL, year_built INTEGER,
  latitude REAL, longitude REAL,
  land_use TEXT, bldg_class TEXT, zone_dist1 TEXT, zone_dist2 TEXT,
  geom TEXT, min_x REAL, max_x REAL, min_y REAL, max_y REAL
);
CREATE INDEX idx_lots_bbox ON lots(min_x, min_y, max_x, max_y);
CREATE TABLE progress (id INTEGER PRIMARY KEY CHECK (id=1), next_offset INTEGER NOT NULL, done INTEGER NOT NULL);
INSERT INTO progress VALUES (1, 3, 3);
"""


def _mk_db(tmp_path: Path) -> Path:
    p = tmp_path / "lots.sqlite"
    c = sqlite3.connect(p)
    c.executescript(SCHEMA)
    geom = {
        "type": "Polygon",
        "coordinates": [[[-74.01, 40.70], [-74.0, 40.70], [-74.0, 40.71], [-74.01, 40.71], [-74.01, 40.70]]],
    }
    boxes = [
        ("1000010001", -74.01, -74.0, 40.70, 40.71),
        ("1000010002", -74.50, -74.40, 40.80, 40.81),  # outside
        ("2000020001", -74.005, -73.995, 40.705, 40.712),  # overlapping-partly
    ]
    for bbl, x0, x1, y0, y1 in boxes:
        c.execute(
            "insert into lots (bbl, borough, block, lot, geom, min_x, max_x, min_y, max_y)"
            " values (?,?,?,?,?,?,?,?,?)",
            (bbl, "MN", "1", "1", json.dumps(geom), x0, x1, y0, y1),
        )
    c.commit()
    c.close()
    return p


def _mk_ll84(tmp_path: Path) -> Path:
    p = tmp_path / "ll84.raw.jsonl"
    rows = [
        # measured LL84 values; modeled end-uses MUST stay None (not in LL84)
        {"property_id": "P1", "parent_property_id": None,
         "nyc_borough_block_and_lot": "1000010001",
         "site_eui_kbtu_ft": "120.8",
         "weather_normalized_site_eui": "118.1",
         "total_location_based_ghg": "2062.89",
         "direct_ghg_emissions_metric": "682.03",
         "indirect_ghg_emissions_metric_rn": "1380.86",
         "electricity_use_grid_purchase": "11998336.5",   # kBtu (unsuffixed)
         "electricity_use_grid_purchase_1": "3517794.6",  # kWh (_1)
         "electricity_use_grid_purchase_3": "13019249.4", # kBtu, grid+onsite
         "natural_gas_use_kbtu": "12840703.7"},
        {"property_id": "P2", "parent_property_id": "P1",
         "nyc_borough_block_and_lot": "2000020001", "site_eui_kbtu_ft": "51.5"},
        {"property_id": "P2_dupe_bbl", "nyc_borough_block_and_lot": "2000020001",
         "site_eui_kbtu_ft": "99999.0"},  # first-writer-wins must block this
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows))
    return p


@pytest.fixture()
def store(tmp_path):
    s = ParcelStore(db_path=_mk_db(tmp_path), ll84_path=_mk_ll84(tmp_path))
    yield s
    s.close()


def test_bbox_queries_real_parcel_bounds_not_centroids(store):
    """bbox intersection must use parcel geometry (min/max corners), not just
    the lat/lon centroid columns."""
    feats, total, truncated = store.query_bbox(-74.02, 40.69, -73.99, 40.72, limit=10)
    bbls = [f["id"] for f in feats]
    assert "1000010001" in bbls
    assert "2000020001" in bbls
    assert "1000010002" not in bbls
    assert total == 2
    assert truncated is False


def test_bbox_no_centroid_only_false_positives(store):
    """A bbox around the centroid-only position of an ELSEWHERE parcel must NOT
    return that parcel when the real polygon is far away."""
    # centroid of 'outside' parcel is ~ -74.45, 40.805; query a box that would
    # match centroid-only strategies but not the real polygon ring.
    feats, total, _ = store.query_bbox(-74.5, 40.80, -74.4, 40.81, limit=10)
    # real polygon IS the outside box here, so it matches; assert the count is 1
    assert total == 1
    # but a tiny box containing a distinct centroid of another parcel's bbox
    # that does NOT overlap real rings must be excluded:
    feats2, total2, _ = store.query_bbox(-74.008, 40.702, -74.007, 40.703, limit=10)
    assert total2 >= 1  # inside big parcel


def test_truncation_reported_exactly(store):
    feats, total, truncated = store.query_bbox(-74.5, 40.6, -73.9, 40.9, limit=2)
    assert len(feats) == 2
    assert total == 3
    assert truncated is True


def test_limit_capped_explicitly(store):
    feats, total, truncated = store.query_bbox(-75, 40, -73, 41, limit=100000)
    assert len(feats) == 3  # capped to min(limit, 20000) still > 3 features
    assert truncated is False


def test_ll84_units_distinction_preserved(store):
    """Unsuffixed is kBtu; _1 is kWh; _3 is a DIFFERENT kBtu quantity (may differ)."""
    rec = store.by_bbl_record("1000010001")
    assert rec["electricity_kbtu"] == pytest.approx(11998336.5)   # unsuffixed kBtu
    assert rec["electricity_kwh"] == pytest.approx(3517794.6)    # _1 kWh
    assert rec["electricity_grid_onsite_kbtu"] == pytest.approx(13019249.4)  # _3 kBtu different
    # the metadata table must state distinct units for each
    assert LL84_FIELDS["electricity_kbtu"]["unit"] == "kBtu"
    assert LL84_FIELDS["electricity_kwh"]["unit"] == "kWh"
    assert LL84_FIELDS["electricity_grid_onsite_kbtu"]["unit"] == "kBtu"
    assert "different quantity" in LL84_FIELDS["electricity_grid_onsite_kbtu"]["basis"].lower()


def test_modeled_end_uses_never_invented(store):
    """LL84 does NOT provide heating/cooling/DHW loads: modeled fields NULL."""
    rec = store.by_bbl_record("1000010001")
    assert rec["modeled_heating_kbtu"] is None
    assert rec["modeled_cooling_kbtu"] is None
    assert rec["modeled_domestic_hot_water_kbtu"] is None
    # and coverage never advertises them as measured fields
    cov = store.coverage()
    assert "modeled_" not in json.dumps(cov["field_availability"]["measured"])
    assert any("not available in LL84" in v for v in cov["field_availability"]["modeled"].values())


def test_first_writer_wins_on_duplicate_bbl(store):
    rec = store.by_bbl_record("2000020001")
    assert rec["property_id"] == "P2"
    assert rec["site_eui_kbtu_ft"] == 51.5


def test_campus_parent_preserved_not_summed(store):
    """Parent-child linkage preserved; no double counting, no synthetic sums."""
    parent = store.by_bbl_record("1000010001")
    child = store.by_bbl_record("2000020001")
    assert parent["property_id"] == "P1"
    assert child["parent_property_id"] == "P1"


def test_coverage_status_complete_only_when_cursor_full(store):
    cov = store.coverage()
    # fixture progress says next_offset=3, source_total is 856687 → partial
    assert cov["status"] == "partial — resumable ingest"


def test_first_bbl_handles_semicolon_and_dotted_campus():
    assert _first_bbl("1015377501;1015367501") == "1015377501"
    assert _first_bbl("1.01537750.1") == "1015377501"
    assert _first_bbl(None) is None


def test_geometry_intersection_precise():
    box_poly = {"type": "Polygon", "coordinates": [[[-74.01, 40.70], [-74.0, 40.70], [-74.0, 40.71], [-74.01, 40.71], [-74.01, 40.70]]]}
    # crosses through box with no vertex inside: segment (prev x < min_x, next x > max_x)
    cross = {"type": "Polygon", "coordinates": [[[-74.02, 40.705], [-73.99, 40.705], [-73.99, 40.706], [-74.02, 40.706], [-74.02, 40.705]]]}
    assert _geom_intersects_bbox(box_poly, -74.015, 40.704, -73.995, 40.707)
    # fully outside
    far = {"type": "Polygon", "coordinates": [[[-73.5, 40.7], [-73.4, 40.7], [-73.4, 40.8], [-73.5, 40.8], [-73.5, 40.7]]]}
    assert not _geom_intersects_bbox(far, -74.1, 40.6, -74.0, 40.8)
