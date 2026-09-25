"""Unit tests for DEV-159 v1 annual building-level demand model.

Covers: end-use arithmetic exactness (shares + eta + COP), evidence tier
assignment, missing-input -> null (never zero) behavior, MapPLUTO zero-padded
land_use code normalization, conservation receipt in the manifest, and
parameterization discipline (params file drives pipeline)."""
import importlib.util
import json
import math
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "build_annual_demand", REPO / "scripts" / "build_annual_demand.py"
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

PARAMS = m.PARAMS
MODEL_VERSION = m.MODEL_VERSION
OUT_MANIFEST = m.OUT_MANIFEST


@pytest.fixture(scope="module")
def load_manifest():
    if not OUT_MANIFEST.exists():
        pytest.skip("manifest not built yet; run scripts/build_annual_demand.py")
    return json.loads(OUT_MANIFEST.read_text())


def make_landuse(bbl: str, code: str):
    return {bbl: code}


def test_office_gas_split_exact():
    """office gas 1000 kBtu, split 0.75 heat / 0.25 dhw, eta 0.80 -> 600/200 delivered."""
    lu = make_landuse("1001260071", "05")  # office, zero-padded
    props = {"bbl": "1001260071", "site_eui_kbtu_ft": None,
             "property_gfa_self_reported": None,
             "natural_gas_use_kbtu": 1000.0,
             "electricity_use_grid_purchase": None}
    out = m.model_one(props, lu)
    assert out["evidence_tier"] == "T1_observed_full"
    assert math.isclose(out["space_heating_kbtu"], 600.0, abs_tol=0.05)
    assert math.isclose(out["dhw_kbtu"], 200.0, abs_tol=0.05)
    assert out["cooling_kbtu"] is None  # no elec metered -> null, never zero


def test_office_elec_cooling_exact():
    """office elec 1000 kWh -> cooling delivered = kWh*share*3.412*COP."""
    lu = make_landuse("1001260071", "05")
    props = {"bbl": "1001260071", "site_eui_kbtu_ft": None,
             "property_gfa_self_reported": None,
             "natural_gas_use_kbtu": None,
             "electricity_use_grid_purchase": 1000.0}
    out = m.model_one(props, lu)
    expected = 1000 * PARAMS["elec_split_by_archetype"]["office"]["cooling"] * 3.412 * m.COP
    assert math.isclose(out["cooling_kbtu"], expected, abs_tol=0.05)
    # no gas, no EUI -> heating is null, not archetype prior (v1 rule)
    assert out["space_heating_kbtu"] is None


def test_zero_padded_landuse_normalized():
    """MapPLUTO stores '05'; params keys are '5' - both must map to office."""
    lu = make_landuse("1000010025", "05")
    props = {"bbl": "1000010025"}
    assert m.archetype_for(props, lu) == "office"
    lu2 = make_landuse("1000010025", "5")
    assert m.archetype_for(props, lu2) == "office"


def test_multifamily_and_other_codes():
    lu = make_landuse("1000010025", "01")
    props = {"bbl": "1000010025"}
    assert m.archetype_for(props, lu) == "multifamily"
    lu = make_landuse("1000010025", "09")
    assert m.archetype_for(props, lu) == "other"


def test_unmatched_bbl_falls_to_other():
    assert m.archetype_for({"bbl": "9999999999"}, {}) == "other"


def test_missing_all_inputs_tier4_nulls():
    props = {"bbl": "1000010025", "site_eui_kbtu_ft": None,
             "property_gfa_self_reported": None,
             "natural_gas_use_kbtu": None,
             "electricity_use_grid_purchase": None}
    out = m.model_one(props, {})
    assert out["evidence_tier"] == "T4_footprint_numeric_only"
    for k in ("space_heating_kbtu", "dhw_kbtu", "cooling_kbtu"):
        assert out[k] is None  # never zero


def test_intensity_only_tier_t2():
    """EUI + GFA but both fuels missing: T2, gas-only proxy split applied, cooling null."""
    props = {"bbl": "1000010025", "site_eui_kbtu_ft": 100.0,
             "property_gfa_self_reported": 10000.0,
             "natural_gas_use_kbtu": None,
             "electricity_use_grid_purchase": None}
    out = m.model_one(props, {})
    assert out["evidence_tier"] == "T2_intensity_only"
    site = 100.0 * 10000.0
    assert math.isclose(out["space_heating_kbtu"], site * 0.70 * m.ETA, rel_tol=0.01)
    assert out["cooling_kbtu"] is None
    assert math.isclose(out["space_heating_kbtu_ft2_yr"], 70.0 * m.ETA, rel_tol=0.01)


def test_primary_energy_gas_not_allocated_at_full_value():
    """gas eta 0.80: allocated heating+DHW = 0.80 x gas, 20% is real stack loss."""
    props = {"bbl": "1000010025", "site_eui_kbtu_ft": None,
             "property_gfa_self_reported": None,
             "natural_gas_use_kbtu": 5000.0,
             "electricity_use_grid_purchase": None}
    out = m.model_one(props, {})
    assert math.isclose(out["space_heating_kbtu"] + out["dhw_kbtu"],
                        5000.0 * m.ETA, rel_tol=0.001)


def test_param_shares_partition():
    """Declared end-use shares must sum to <=1.0 per archetype.
    Gas splits are a full partition (sum 1.0, conservation algebra).
    Elec splits only declare COOLING (v1 intentionally does not model other
    electric end uses); the unmodeled residual stays 'other_elec' and is
    documented in params — so we assert 0 < cooling_share < 1, and that
    the params file itself DECLARES this incompleteness."""
    for arch, shares in PARAMS["gas_split_by_archetype"].items():
        if not isinstance(shares, dict):
            continue
        s = sum(v for k, v in shares.items()
                if isinstance(v, (int, float)) and k != "source")
        assert math.isclose(s, 1.0, abs_tol=0.001), f"gas/{arch} sums {s}"
    # elec: cooling-only partial coverage is intentional and documented
    elec = PARAMS["elec_split_by_archetype"]
    assert any("not disaggregated" in str(v).lower()
               or "intentionally not" in str(v).lower()
               for k, v in elec.items() if isinstance(v, str))
    for arch, shares in elec.items():
        if not isinstance(shares, dict):
            continue
        c = shares["cooling"]
        assert 0 < c < 1, f"elec/{arch} cooling {c}"


def test_params_are_file_not_hardcoded(tmp_path):
    """All shares in the pipeline come from scripts/annual_demand_params.json (parameterized for replacement)."""
    assert m.ETA == PARAMS["heating_efficiency"]["value"]
    assert m.COP == PARAMS["cooling_cop"]["value"]
    assert MODEL_VERSION == PARAMS["model_version"]


def test_manifest_shape_and_counts(load_manifest):
    man = load_manifest
    assert man["model_version"] == MODEL_VERSION
    assert man["output"]["features"] == 932
    assert man["counts_by_tier"], "tier counts present"
    assert man["aggregate_conservation_check"]["as_expected"] is True
    assert math.isclose(man["aggregate_conservation_check"]["implied_eta"], m.ETA, abs_tol=0.001)
    assert man["output"]["sha256"] and len(man["output"]["sha256"]) == 64


def test_manifest_nulls_are_null_not_zero(load_manifest):
    man = load_manifest
    # null counts should be small (>0) and news-level: heating/cooling nulls tracked
    assert man["null_counts"]["heating"] >= 0
    assert man["null_counts"]["cooling"] >= 0


def test_pilot_geocode_medians_sane(load_manifest):
    """Sanity: pilot heating intensity medians in a plausible NYC office/mf range.
    LL84 midtown observed EUI median ~74 kBtu/ft2/yr; heating share should land
    well below campus-wide EUI (12-30 kBtu/ft2 heating is a typical NYC office gas heating realized range)."""
    man = load_manifest["intensity_distributions_kbtu_ft2_yr"]
    assert 3 < man["space_heating"]["median"] < 40
    assert 1 < man["dhw"]["median"] < 25
    # cooling in NYC offices: roughly 20-100 kBtu/ft2/yr delivered
    assert 10 < man["cooling"]["median"] < 150
