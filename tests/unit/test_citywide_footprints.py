"""Unit tests for citywide_footprints.FootprintStore — synthetic mini DB, no
network. Guards the properties that matter for a city-scale map layer:
bbox correctness, NULL-vs-zero handling, and the honest LL84 join semantics.
"""
import json
import sqlite3

import pytest

from nyc_decarbonization.api.citywide_footprints import (
    ENERGY_COLUMNS,
    MODELED_COLUMNS,
    FootprintStore,
)


def _mk_db(path, rows):
    """Build a tiny footprints DB with the real schema."""
    conn = sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE footprints (
        fid INTEGER PRIMARY KEY,
        bin TEXT, bbl TEXT, doitt_id TEXT,
        shape_area REAL, height_roof REAL, construction_year INTEGER,
        feature_code TEXT, name TEXT,
        min_x REAL, min_y REAL, max_x REAL, max_y REAL,
        cx REAL, cy REAL, geom TEXT,
        has_ll84 INTEGER DEFAULT 0,
        site_eui_kbtu_ft REAL, weather_normalized_site_eui REAL,
        total_location_based_ghg REAL, direct_ghg_emissions_intensity REAL,
        property_gfa_self_reported REAL, electricity_use_grid_purchase REAL,
        natural_gas_use_kbtu REAL, district_steam_use_kbtu REAL,
        district_hot_water_use_kbtu REAL, district_chilled_water_use REAL,
        fuel_oil_1_use_kbtu REAL, fuel_oil_2_use_kbtu REAL, fuel_oil_4_use_kbtu REAL,
        fuel_oil_5_6_use_kbtu REAL, diesel_2_use_kbtu REAL, propane_use_kbtu REAL,
        ll84_property_id TEXT,
        space_heating_kbtu REAL, dhw_kbtu REAL, cooling_kbtu REAL,
        space_heating_kbtu_ft2_yr REAL, dhw_kbtu_ft2_yr REAL, cooling_kbtu_ft2_yr REAL,
        net_thermal_kbtu_ft2_yr REAL, evidence_tier TEXT, archetype TEXT
    );
    """)
    cols = (
        "bin", "bbl", "min_x", "min_y", "max_x", "max_y", "cx", "cy", "geom",
        "has_ll84", "site_eui_kbtu_ft", "district_steam_use_kbtu",
        "net_thermal_kbtu_ft2_yr", "evidence_tier", "archetype",
    )
    for r in rows:
        conn.execute(
            f"INSERT INTO footprints ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
            tuple(r.get(c) for c in cols),
        )
    conn.commit()
    conn.close()


def _square(lon, lat, d=0.001):
    return {
        "type": "Polygon",
        "coordinates": [[[lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat]]],
    }


@pytest.fixture()
def store(tmp_path):
    db = tmp_path / "fp.sqlite"
    _mk_db(db, [
        # in midtown, has LL84 + modeled net
        {"bin": "1001", "bbl": "1000010001",
         "min_x": -73.98, "min_y": 40.75, "max_x": -73.979, "max_y": 40.751,
         "cx": -73.9795, "cy": 40.7505, "geom": json.dumps(_square(-73.98, 40.75)),
         "has_ll84": 1, "site_eui_kbtu_ft": 88.0, "district_steam_use_kbtu": 1500.0,
         "net_thermal_kbtu_ft2_yr": -12.5, "evidence_tier": "T1_observed_full",
         "archetype": "office"},
        # nearby, no LL84, no modeled demand -> NULLs must survive as NULL
        {"bin": "1002", "bbl": "1000010002",
         "min_x": -73.978, "min_y": 40.751, "max_x": -73.977, "max_y": 40.752,
         "cx": -73.9775, "cy": 40.7515, "geom": json.dumps(_square(-73.978, 40.751)),
         "has_ll84": 0, "site_eui_kbtu_ft": None, "district_steam_use_kbtu": None,
         "net_thermal_kbtu_ft2_yr": None, "evidence_tier": None, "archetype": "other"},
        # far away (Brooklyn-ish) — must NOT appear in a midtown bbox
        {"bin": "2001", "bbl": "3000010001",
         "min_x": -73.95, "min_y": 40.65, "max_x": -73.949, "max_y": 40.651,
         "cx": -73.9495, "cy": 40.6505, "geom": json.dumps(_square(-73.95, 40.65)),
         "has_ll84": 1, "site_eui_kbtu_ft": 55.0, "district_steam_use_kbtu": None,
         "net_thermal_kbtu_ft2_yr": 9.0, "evidence_tier": "T1_observed_full",
         "archetype": "multifamily"},
    ])
    return FootprintStore(db_path=db)


def test_bbox_returns_only_features_in_view(store):
    feats, matched, truncated = store.query_bbox(-73.985, 40.745, -73.975, 40.755, 100)
    assert matched == 2, "two midtown footprints are in view; the Brooklyn one is not"
    assert not truncated
    bins = sorted(f["properties"]["bin"] for f in feats)
    assert bins == ["1001", "1002"]


def test_bbox_excludes_distant_feature(store):
    feats, matched, _ = store.query_bbox(-73.955, 40.645, -73.945, 40.655, 100)
    assert matched == 1
    assert feats[0]["properties"]["bin"] == "2001"


def test_truncation_flag_reported(store):
    feats, matched, truncated = store.query_bbox(-73.99, 40.74, -73.97, 40.76, 1)
    assert matched == 2
    assert truncated is True, "matched > returned must set truncated"
    assert len(feats) == 1


def test_null_demand_stays_null_not_zero(store):
    """A footprint with no modeled demand must report NULL, never 0 — 0 would
    read as 'zero cooling demand' on a diverging ramped layer."""
    feats, _, _ = store.query_bbox(-73.979, 40.7505, -73.9769, 40.7521, 100)
    no_ll = next(f for f in feats if f["properties"]["bin"] == "1002")
    assert no_ll["properties"]["net_thermal_kbtu_ft2_yr"] is None
    assert no_ll["properties"]["site_eui_kbtu_ft"] is None
    assert no_ll["properties"]["evidence_tier"] is None


def test_steam_is_carried_through(store):
    """District steam is a heating fuel: it must reach the client, or
    steam-served buildings read as cooling-only (the all-blue bug)."""
    feats, _, _ = store.query_bbox(-73.985, 40.745, -73.975, 40.755, 100)
    with_steam = next(f for f in feats if f["properties"]["bin"] == "1001")
    assert with_steam["properties"]["district_steam_use_kbtu"] == 1500.0


def test_has_ll84_is_boolean_and_coverage_counts(store):
    feats, _, _ = store.query_bbox(-73.99, 40.74, -73.97, 40.76, 100)
    assert all(isinstance(f["properties"]["has_ll84"], bool) for f in feats)
    cov = store.coverage()
    assert cov["footprints_total"] == 3
    assert cov["footprints_with_ll84"] == 2
    assert cov["footprints_with_modeled_net_thermal"] == 2
    # the note must state the LL84 threshold semantics explicitly
    assert "not required to report" in cov["note"]


def test_geometry_is_parsed_not_stringified(store):
    feats, _, _ = store.query_bbox(-73.985, 40.745, -73.975, 40.755, 100)
    for f in feats:
        assert isinstance(f["geometry"], dict)
        assert f["geometry"]["type"] in ("Polygon", "MultiPolygon")


def test_by_bin_lookup(store):
    feat = store.by_bin("2001")
    assert feat is not None
    assert feat["properties"]["net_thermal_kbtu_ft2_yr"] == 9.0
    assert store.by_bin("999999") is None


def test_energy_and_modeled_columns_exposed(store):
    """Every energy + modeled column must be present in the payload so the
    frontend symbology never reads a key that isn't served."""
    feats, _, _ = store.query_bbox(-73.985, 40.745, -73.975, 40.755, 100)
    props = feats[0]["properties"]
    for k in ENERGY_COLUMNS + MODELED_COLUMNS:
        assert k in props, f"missing served column: {k}"
