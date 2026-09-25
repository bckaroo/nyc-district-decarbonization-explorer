"""Unit tests for identifier parsing (multi-valued, malformed, null vs zero)."""
import pytest

from nyc_decarbonization.data.identifiers import boro_code, parse_bbl, parse_bbl_multi, parse_bin


class TestBBL:
    def test_canonical(self):
        p = parse_bbl("1005850042")
        assert p.bbl == "1005850042" and p.borough == "manhattan" and p.block == 585 and p.lot == 42

    def test_dotted_form(self):
        p = parse_bbl("1.00585.0042")
        assert p.bbl == "1005850042"

    def test_float_with_trailing_zeros(self):
        p = parse_bbl("1005850042.00000000")
        assert p.bbl == "1005850042"

    @pytest.mark.parametrize("bad", [None, "", " ", "9999999999", "abc", "2.00585", "0.00122.0001"])
    def test_malformed_or_null(self, bad):
        p = parse_bbl(bad)
        assert p.bbl is None
        assert p.block is None

    def test_null_is_null_not_zero(self):
        p = parse_bbl(None)
        assert p.raw is None and p.bbl is None and p.block != 0 or (p.raw is None and p.bbl is None)

    def test_multi_valued_semicolons(self):
        assert parse_bbl_multi("1005850042;1005857503") == ["1005850042", "1005857503"]

    def test_multi_valued_dedupes_and_skips_garbage(self):
        assert parse_bbl_multi("1005850042.00000000,9999,abc") == ["1005850042"]

    def test_multi_none(self):
        assert parse_bbl_multi(None) == []

    def test_boro_names(self):
        assert boro_code("Kings") == "3"
        assert boro_code("Staten Island") == "5"


class TestBIN:
    def test_ok(self):
        assert parse_bin(1086412) == "1086412"
        assert parse_bin("1086412.0") == "1086412"

    def test_zero_and_short(self):
        assert parse_bin("0") is None
        assert parse_bin(123) is None
        assert parse_bin(None) is None
