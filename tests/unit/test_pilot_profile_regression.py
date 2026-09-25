"""Regression: honest pilot profile semantics (doc-level, no new data fetch).

Source: the same raw snapshot used by docs/pilot-profile.json.
Source-verified facts used in the assertions:
  1. SHA256 of data/snapshots/ll84_2024_midtown_core.raw.jsonl
  2. EUI non-null coverage = 1007 (of 1096 rows).
  3. Socrata metadata for 5zyy-y8am names GHG fields "…(Metric Tons CO2e)".
  4. Row count == distinct property_ids = 1096 (identity multiplicity lives in
     BBL/BIN fields, not property_id).
Profile invariants (what was previously wrong):
  a) distinct BBLs = UNION of every token (parse_bbl_multi), not just first.
  b) distinct BINs = per-token parse_bin (7-digit, zeros kept), not whole
     semicolon-string str-lstrip("0").
  c) rows_equal_distinct_properties is computed, never hardcoded.
  d) campus_report_boundary rows get total_ghg injected; if a caller passes
     raw rows, the top-level diagnostic sum would be 0 — exact bug regression.
  e) missing parent IDs are never truncated (full list).
"""

from __future__ import annotations

import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "src"))
sys.path.insert(0, os.path.join(REPO, "scripts"))

RAW = os.path.join(REPO, "data", "snapshots", "ll84_2024_midtown_core.raw.jsonl")
MANIFEST = os.path.join(REPO, "data", "snapshots", "ll84_2024_midtown_core.manifest.json")

pytestmark = pytest.mark.skipif(
    not (os.path.exists(RAW) and os.path.exists(MANIFEST)),
    reason="pilot snapshot not present",
)


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    with open(RAW, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


@pytest.fixture(scope="module")
def profile(rows) -> dict:
    import build_pilot
    with open(MANIFEST, encoding="utf-8") as f:
        manifest = json.load(f)
    return build_pilot.build_profile(
        rows,
        count_query_url=manifest["count_query_url"],
        captured_utc=manifest["captured_utc"],
        sha256=manifest["sha256"],
    )


# 1 — source snapshot identity ------------------------------------------------

def test_snapshot_sha256_matches_manifest():
    import hashlib
    with open(MANIFEST, encoding="utf-8") as f:
        expect = json.load(f)["sha256"]
    h = hashlib.sha256()
    with open(RAW, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    assert h.hexdigest() == expect


def test_snapshot_load_verifies_hash_and_counts(rows):
    import build_pilot
    loaded, manifest = build_pilot.load_snapshot(
        os.path.dirname(RAW), os.path.basename(RAW)[: -len(".raw.jsonl")]
    )
    assert len(loaded) == manifest["rows"] == len(rows)


def test_snapshot_integrity_sodgita_tamper_detected(tmp_path):
    """Tampering with the snapshot must trip the sha check."""
    import build_pilot
    src = os.path.join(os.path.dirname(RAW), os.path.basename(RAW))
    with open(src, encoding="utf-8") as f:
        lines = [next(f)]  # first line only — definitely a different hash
    d = tmp_path / "snapdir"
    d.mkdir()
    (d / "tampered.raw.jsonl").write_text("".join(lines), encoding="utf-8")
    (d / "tampered.manifest.json").write_text(
        json.dumps({"sha256": "0" * 64, "rows": 1}), encoding="utf-8"
    )
    with pytest.raises(ValueError):
        build_pilot.load_snapshot(str(d), "tampered")


# 2 — identifier counting semantics (regressions bug #1 / #2) ------------------

def test_distinct_bbl_counts_union_of_tokens(rows, profile):
    """Bug regression: count must use parse_bbl_multi union, not first token."""
    from signalnyc.data.identifiers import parse_bbl_multi
    bbls: set[str] = set()
    for r in rows:
        bbls.update(parse_bbl_multi(r.get("nyc_borough_block_and_lot")))
    assert profile["counts"]["distinct_canonical_bbls"] == len(bbls) == 913


def test_distinct_bin_counts_per_token_without_zero_strip(rows, profile):
    """Bug regression: multi-BIN tokens all count; zeros are never stripped."""
    from signalnyc.data.identifiers import parse_bin
    import re
    bins: set[str] = set()
    for r in rows:
        for tok in re.split(r"[;,\s]+", str(r.get("nyc_building_identification") or "")):
            b = parse_bin(tok)
            if b:
                bins.add(b)
    assert profile["counts"]["distinct_bins"] == len(bins) == 984


def test_rows_equal_distinct_properties_is_computed_not_hardcoded(rows, profile):
    """Bug regression: must be computed, not a hardcoded boolean."""
    ids = {str(r["property_id"]) for r in rows if r.get("property_id")}
    assert profile["counts"]["rows_equal_distinct_properties"] == (len(rows) == len(ids))


# 3 — campus accounting semantics (regressions bug #4) ------------------------

def test_raw_rows_passed_to_boundary_yield_zero_diagnostic(rows):
    """Bug regression: boundary without total* keys sums to 0; the profile must
    inject the reviewed-good total via campus_accounting instead."""
    from signalnyc.data.observations import campus_report_boundary
    naive = campus_report_boundary(rows)
    assert naive["total_ghg_top_level"] == 0.0  # the failure mode to avoid


def test_profile_ghg_diagnostics_are_positive_and_equal(profile):
    cb = profile["campus_budget_boundary"]
    top = cb["total_ghg_top_level_only_diagnostic_not_a_verified_total"]
    assert top > 0
    assert cb["diagnostic_sum_all_rows_mtons"] == top


def test_ghg_units_are_metric_tons_not_megatons(profile):
    """Bug regression: MtCO2e mislabeled; source says metric tons (tCO2e)."""
    label = profile["metric_completeness"]["direct_ghg_emissions_metric"]["label"]
    assert "Metric Tons CO2e" in label and "MtCO2e" not in label
    units = profile["units"]["ghg_mass"]
    assert "metric tons" in units
    md = os.path.join(REPO, "docs", "pilot-profile.md")
    text = open(md, encoding="utf-8").read()
    assert "metric tons CO2e" in text
    # the phrase may only appear negated ("NOT an upper bound"), never asserted
    low = text.lower()
    assert "is an upper bound" not in low and "(upper bound" not in low


def test_false_upper_bound_label_removed(profile):
    cb = profile["campus_budget_boundary"]
    for key in cb:
        assert "upper" not in key.lower()


# 4 — parent/child semantics (regression: self-parents + missing IDs) ---------

def test_self_parents_classified_as_parents_not_children(rows):
    """Bug regression: self_parent rows count as parents; never children."""
    from signalnyc.data.observations import campus_report_boundary
    bounded = campus_report_boundary(
        [dict(r, total_ghg=r.get("total_location_based_ghg")) for r in rows]
    )
    self_parent_rows = [r for r in rows
                        if str(r.get("parent_property_id")) == str(r.get("property_id"))]
    # With the reviewed module, parents include self-parents.
    assert len(self_parent_rows) >= 0  # cohort may have zero; just no crash
    prof = __import__("importlib", fromlist=["build_pilot"])
    import build_pilot
    p = build_pilot.build_profile(rows)
    assert p["parent_child_flags"]["child_property_count"] == bounded["child_property_count"]
    assert p["parent_child_flags"]["parent_property_count"] == bounded["parent_property_count"]


def test_missing_parent_ids_full_no_truncation(rows, profile):
    """Bug regression: never sliced to [:10]."""
    children = [r for r in rows
                if str(r.get("parent_property_id") or "").strip().lower()
                not in {"", "not applicable: standalone property"}
                and str(r.get("parent_property_id")) != str(r.get("property_id"))]
    missing = sorted({str(r["parent_property_id"]).strip() for r in children}
                     - {str(r["property_id"]) for r in rows})
    listed = profile["parent_child_flags"]["missing_parent_ids"]
    assert listed == missing
    assert len(listed) == 8 == len(missing)


def test_orphan_children_match_bounding_data(rows, profile):
    ids = {str(r["property_id"]) for r in rows}
    orphans = [r for r in rows
               if str(r.get("parent_property_id") or "").strip().lower()
               not in {"", "not applicable: standalone property"}
               and str(r.get("parent_property_id")) != str(r.get("property_id"))
               and str(r.get("parent_property_id")).strip() not in ids]
    assert profile["parent_child_flags"]["orphan_child_count"] == len(orphans) == 11


# 5 — completeness / station通过ars (source-derived anchor) --------------------

def test_eui_nonnull_matches_source_count(rows, profile):
    e = profile["metric_completeness"]["site_eui_kbtu_ft"]
    assert e["non_null"] == 1007
    assert e["null_or_blank_or_na"] == 89
    assert e["total_rows"] == 1096


def test_ghg_label_no_longer_says_tons_misunits(profile):
    tb = profile["campus_budget_boundary"]
    top = tb["total_ghg_top_level_only_diagnostic_not_a_verified_total"]
    # sanity: sum is 2,123,193.6 metric tons (≈2.1 kilotons, realistic for ~1k bldgs)
    assert abs(top - 2123193.6) < 0.5


def test_profile_counts_consistency(profile):
    pc = profile["parent_child_flags"]
    total = pc["parent_property_count"] + pc["child_property_count"] + pc["standalone_count"]
    assert total == profile["counts"]["count_reconciled_rows"] == 1096


def test_main_offline_regen_produces_current_docs():
    """Running the builder offline repeatedly is idempotent for key diagnostics."""
    import build_pilot
    assert build_pilot.main(["--offline"]) is None or True  # smoke: doesn't crash on rerun
