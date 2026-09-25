"""Unit tests: observation helpers — revision selection, null-vs-zero, boundaries."""
import pytest

from nyc_decarbonization.data.observations import (
    revision_dedupe,
    null_vs_zero,
    campus_report_boundary,
)


class TestRevisionDedupe:
    def test_latest_report_year_wins(self):
        rows = [
            {"report_year": "2022", "site_eui": 90.0, "row_id": 1},
            {"report_year": "2023", "site_eui": 91.0, "row_id": 2},
        ]
        assert revision_dedupe(rows, "report_year")["row_id"] == 2

    def test_never_cross_year_double_count(self):
        """Same property in 2 years yields ONE observation per (property, year)."""
        rows = [
            {"report_year": "2023", "property_id": "P1", "row_id": 1},
            {"report_year": "2023", "property_id": "P1", "row_id": 2},  # duplicate/deleted-reissued
        ]
        deduped = revision_dedupe(rows, "report_year", keys=("report_year", "property_id"))
        assert len(deduped) == 1

    def test_stable_tiebreak(self):
        rows = [
            {"report_year": "2023", "property_id": "P1", "row_id": 7},
            {"report_year": "2023", "property_id": "P1", "row_id": 3},
        ]
        # deterministic: highest row_id wins regardless of input order
        got = revision_dedupe(rows, "report_year", keys=("report_year", "property_id"))
        assert isinstance(got, list) and got[0]["row_id"] == 7
        got2 = revision_dedupe(list(reversed(rows)), "report_year",
                               keys=("report_year", "property_id"))
        assert got2[0]["row_id"] == 7

    def test_child_rows_distinct_from_parent(self):
        rows = [
            {"report_year": "2023", "property_id": "PARENT", "parent_property_id": "", "row_id": 1},
            {"report_year": "2023", "property_id": "CHILD_A", "parent_property_id": "PARENT", "row_id": 2},
            {"report_year": "2023", "property_id": "CHILD_B", "parent_property_id": "PARENT", "row_id": 3},
        ]
        deduped = revision_dedupe(rows, "report_year", keys=("report_year", "property_id"))
        assert {r["property_id"] for r in deduped} == {"PARENT", "CHILD_A", "CHILD_B"}


class TestNullVsZero:
    def test_missing_key_is_none(self):
        assert null_vs_zero({}) is None

    def test_empty_string_is_none(self):
        assert null_vs_zero({"v": ""}) is None

    def test_zero_is_zero(self):
        assert null_vs_zero({"v": "0"}) == 0.0
        assert null_vs_zero({"v": 0}) == 0.0

    def test_number_parses(self):
        assert null_vs_zero({"v": "93.4"}) == 93.4

    def test_garbage_is_none(self):
        assert null_vs_zero({"v": "N/A"}) is None


class TestCampusBoundary:
    def test_child_sums_never_added_to_parent_total(self):
        """Campus parent row already aggregates children: only parent total counts."""
        rows = [
            {"property_id": "CAMPUS", "parent_property_id": "", "total_ghg": 1000.0},
            {"property_id": "C1", "parent_property_id": "CAMPUS", "total_ghg": 400.0},
            {"property_id": "C2", "parent_property_id": "CAMPUS", "total_ghg": 600.0},
        ]
        agg = campus_report_boundary(rows)
        assert agg["reporting_property_count"] == 3  # all report properties
        assert agg["total_ghg_top_level"] == 1000.0  # parent only; children excluded
        assert agg["child_property_count"] == 2

    def test_no_parent_rows(self):
        rows = [{"property_id": "A", "parent_property_id": "", "total_ghg": 5.0},
                {"property_id": "B", "parent_property_id": "", "total_ghg": 7.0}]
        agg = campus_report_boundary(rows)
        assert agg["total_ghg_top_level"] == 12.0
        assert agg["reporting_property_count"] == 2

    def test_null_ghg_is_null_not_zero(self):
        rows = [{"property_id": "A", "parent_property_id": "", "total_ghg": None},
                {"property_id": "B", "parent_property_id": "", "total_ghg": 3.0}]
        agg = campus_report_boundary(rows)
        assert agg["total_ghg_top_level"] == 3.0
        assert agg["null_ghg_count"] == 1
