"""Synthetic regression: revision grouping must retain each energy year."""
from signalnyc.data.observations import revision_dedupe


def test_property_key_always_includes_year():
    rows = [dict(property_id='A', report_year='2023', row_id=1),
            dict(property_id='A', report_year='2024', row_id=2),
            dict(property_id='A', report_year='2024', row_id=3)]
    result = revision_dedupe(rows, 'report_year', keys=['property_id'])
    assert {(r['report_year'], r['row_id']) for r in result} == {('2023', 1), ('2024', 3)}
