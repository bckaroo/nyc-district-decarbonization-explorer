"""Tests for ingest.socrata / ingest.quality / audit-corrected accounting.

Network access is NOT required for these: socrata.soda is monkeypatched
(source-shaped examples with synthetic child/orphan values; not validation data).
"""
import json
import urllib.parse
import pytest

from signalnyc.ingest import quality, snapshots, socrata
from signalnyc.data.observations import campus_report_boundary


# Source-shaped standalone/parent examples; child and orphan are synthetic.
ROW_STANDALONE = {
    "property_id": "53687992", "parent_property_id": "Not Applicable: Standalone Property",
    "nyc_borough_block_and_lot": "1013380030", "nyc_building_identification": "1090642",
    "property_gfa_self_reported": "205573", "site_eui_kbtu_ft": "120.8",
    "total_location_based_ghg": "2062.89", "direct_ghg_emissions_metric": "682.03",
    "natural_gas_use_kbtu": "12840703.7", "electricity_use_grid_purchase": "11998336.5",
    "energy_star_score": None, "report_year": "2024",
}
ROW_PARENT = {
    "property_id": "66074492", "parent_property_id": "66074492",
    "nyc_borough_block_and_lot": "1015377501;1015367501",
    "nyc_building_identification": "1086170;1086171;1086172",
    "property_gfa_self_reported": "1619551", "site_eui_kbtu_ft": "51.5",
    "total_location_based_ghg": "8145.82", "direct_ghg_emissions_metric": "1247.41",
    "report_year": "2024",
}
ROW_CHILD = {
    "property_id": "66074493", "parent_property_id": "66074492",
    "nyc_borough_block_and_lot": "1015367501", "nyc_building_identification": "1086171",
    "property_gfa_self_reported": "300000", "site_eui_kbtu_ft": "60.0",
    "total_location_based_ghg": "500.0", "report_year": "2024",
}
ROW_ORPHAN = {
    "property_id": "ORPH", "parent_property_id": "NOPARENT-999",
    "nyc_borough_block_and_lot": "1001000016", "total_location_based_ghg": "999.0",
    "report_year": "2024",
}


class TestDistinctCounts:
    def test_rows_never_reported_as_properties(self):
        rows = [ROW_STANDALONE, ROW_PARENT, ROW_CHILD, dict(ROW_STANDALONE)]  # dup id
        c = quality.distinct_counts(rows)
        assert c["row_count"] == 4
        assert c["distinct_property_id"] == 3
        assert c["distinct_bbl"] == 3  # parent multi-BBL ∪ child BBL (child's is shared)
        assert c["rows_with_multiple_bbl"] == 1
        assert c["distinct_bin"] == 4  # child BIN already belongs to parent


class TestCampusAccounting:
    def test_resolved_campus_parent_covers_child_no_double_count(self):
        r = quality.campus_accounting([ROW_STANDALONE, ROW_PARENT, ROW_CHILD],
            coverage_evidence={"66074492": "synthetic test: reviewed inclusive boundary"})
        assert r["aggregate_status"] == "ok"
        assert r["aggregate_top_level_emissions_mtons"] == pytest.approx(2062.89 + 8145.82)
        assert r["campus_child_count"] == 1

    def test_orphan_child_withholds_aggregate(self):
        r = quality.campus_accounting([ROW_STANDALONE, ROW_ORPHAN])
        assert r["aggregate_status"] == "WITHHELD_UNRESOLVED"
        assert r["aggregate_top_level_emissions_mtons"] is None
        assert "ORPH" in r["unresolved_campuses"]

    def test_standalone_marker_not_treated_as_child(self):
        r = quality.campus_accounting([ROW_STANDALONE])
        assert r["standalone_count"] == 1 and r["campus_child_count"] == 0
        assert r["aggregate_status"] == "ok"


class TestClimateBoundaryCompat:
    def test_blank_parent_form_still_counts_child(self):
        """Old synthetic form: child with parent_property_id set, parent blank row."""
        rows = [
            {"property_id": "CAMPUS", "parent_property_id": "", "total_ghg": 1000.0},
            {"property_id": "C1", "parent_property_id": "CAMPUS", "total_ghg": 400.0},
        ]
        agg = campus_report_boundary(rows, coverage_evidence={"CAMPUS": "synthetic inclusive boundary"})
        assert agg["aggregate_status"] == "ok"
        assert agg["aggregate_ghg"] == 1000.0  # parent only; child not added
        assert agg["child_property_count"] == 1

    def test_orphan_withholds(self):
        rows = [
            {"property_id": "A", "parent_property_id": "", "total_ghg": 1.0},
            {"property_id": "X", "parent_property_id": "GHOST", "total_ghg": 5.0},
        ]
        agg = campus_report_boundary(rows)
        assert agg["aggregate_status"] == "WITHHELD_UNRESOLVED"
        assert agg["aggregate_ghg"] is None


class TestCompleteness:
    def test_known_nulls_counted(self):
        rows = [dict(ROW_STANDALONE), dict(ROW_PARENT)]  # parent ESS null
        c = quality.completeness(rows)
        assert c["row_count"] == 2
        assert c["site_eui_kbtu_ft"]["present"] == 2
        assert c["energy_star_score"]["present"] == 0
        assert c["total_location_based_ghg"]["pct"] == 100.0


class TestSampleIdentityCases:
    def test_buckets_present(self):
        s = quality.sample_identity_cases([ROW_STANDALONE, ROW_PARENT, ROW_CHILD, ROW_ORPHAN])
        kinds = {x["case"] for x in s}
        assert "single_bin" in kinds and "multi_bbl" in kinds
        for x in s:
            assert x["raw_bbl_field"]  # raw preserved, not canonicalized away


class TestFetchAllReconciliation:
    def test_count_mismatch_raises(self, monkeypatch):
        def fake_soda(url, timeout=120):
            if "count" in urllib.parse.unquote(url):
                return [{"n": "3"}], {"status": 200, "headers": {}}
            return [dict(ROW_STANDALONE)], {"status": 200, "headers": {}}

        monkeypatch.setattr(socrata, "soda", fake_soda)
        with pytest.raises(socrata.CountMismatch):
            socrata.fetch_all("5zyy-y8am", where="report_year='2024'", page_size=10)

    def test_matching_pages_pass_and_log_urls(self, monkeypatch):
        offsets = []
        def fake_soda(url, timeout=120):
            params = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            if params.get("$select") == ["count(*) as n"]:
                return [{"n": "2"}], {"status": 200, "headers": {}}
            offset = int(params.get("$offset", ["0"])[0])
            offsets.append(offset)
            return [ROW_STANDALONE, ROW_PARENT][offset:offset + 1], {
                "status": 200, "headers": {}}
        monkeypatch.setattr(socrata, "soda", fake_soda)
        rows, meta = socrata.fetch_all("5zyy-y8am", where="report_year='2024'", page_size=1)
        assert rows == [ROW_STANDALONE, ROW_PARENT]
        assert offsets[:2] == [0, 1]
        assert meta["fetched_rows"] == meta["count"] == 2
        assert "count_query" in meta["urls"]

    def test_parenthesized_count_query_sanity(self):
        # BASE format works for a fid
        assert "5zyy-y8am" in socrata.BASE.format(fid="5zyy-y8am")


class TestSnapshotManifest:
    def test_snapshot_writes_hash_and_manifest(self, tmp_path, monkeypatch):
        def fake_fetch_all(fid, where=None, fields=None, page_size=10000, timeout=120):
            return [dict(ROW_STANDALONE), dict(ROW_PARENT)], {
                "urls": {"count_query": "u"}, "fetch_log": {"pages": 1, "rows": 2, "requests": []},
            }

        monkeypatch.setattr(snapshots, "fetch_all", fake_fetch_all)
        m = snapshots.snapshot_dataset(str(tmp_path), "5zyy-y8am", "report_year='2024'", "ll84_2024")
        assert m["rows"] == 2 and len(m["sha256"]) == 64
        m2 = json.load(open(tmp_path / "ll84_2024.manifest.json"))
        assert m2["captured_utc"].endswith("Z")
        # immutable snapshotting: identical rerun keeps files but writes same content
        m3 = snapshots.snapshot_dataset(str(tmp_path), "5zyy-y8am", "report_year='2024'", "ll84_2024")
        assert m3["sha256"] == m["sha256"]
