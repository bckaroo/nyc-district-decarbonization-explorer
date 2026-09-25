"""Synthetic regression fixtures: not measurements or real coverage evidence."""
import pytest
from nyc_decarbonization.data.observations import campus_report_boundary


def test_linkage_alone_does_not_prove_coverage():
    rows = [dict(property_id='P', parent_property_id='P', total_ghg=10),
            dict(property_id='C', parent_property_id='P', total_ghg=4)]
    result = campus_report_boundary(rows)
    assert result['aggregate_ghg'] is None
    assert result['child_property_count'] == 1
    assert result['aggregate_status'] == 'WITHHELD_UNRESOLVED'
    verified = campus_report_boundary(rows, coverage_evidence={'P': 'synthetic reviewed boundary'})
    assert verified['aggregate_ghg'] == 10


def test_duplicate_ids_rejected():
    with pytest.raises(ValueError, match='resolve revisions'):
        campus_report_boundary([dict(property_id='A'), dict(property_id='A')])


def test_mixed_years_rejected():
    with pytest.raises(ValueError, match='one reporting year'):
        campus_report_boundary([dict(property_id='A', report_year='2023'),
                                dict(property_id='B', report_year='2024')])


def test_missing_emissions_withhold_total():
    result = campus_report_boundary([dict(property_id='A', total_ghg=None)])
    assert result['aggregate_ghg'] is None
    assert result['aggregate_status'] == 'WITHHELD_MISSING'
