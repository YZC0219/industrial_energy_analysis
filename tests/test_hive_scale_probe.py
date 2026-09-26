"""Safety and expected-grain checks for the isolated scale materializer."""
from decimal import Decimal

import pytest

from tools.materialize_hive_scale_probe import expected_counts, validate_database_name
from tools.verify_hive_scale_probe import KEYS, expected_layer_cost


def test_probe_database_name_is_strictly_isolated():
    assert validate_database_name("energy_scale_probe_20260927") == (
        "energy_scale_probe_20260927"
    )
    for value in ("energy_dwd", "energy_scale_probe", "energy_scale_probe_20260927;DROP",
                  "energy_scale_probe_20260927.other", "energy_scale_probe_2026-09-27"):
        with pytest.raises(ValueError):
            validate_database_name(value)


def test_layer_grains_scale_with_plant_replicas():
    assert expected_counts(10) == {
        "ods": 190060, "dwd": 190060, "dws": 58480, "ads": 7310,
    }
    assert expected_counts(100) == {
        "ods": 1900600, "dwd": 1900600, "dws": 584800, "ads": 73100,
    }
    with pytest.raises(ValueError):
        expected_counts(0)


def test_quality_contract_checks_layer_keys_and_exact_cost():
    assert KEYS["dwd"] == ("plant_id", "record_date", "workshop_code", "energy_code")
    assert KEYS["dws"] == ("plant_id", "record_date", "workshop_code")
    assert KEYS["ads"] == ("plant_id", "record_date")
    assert expected_layer_cost(100) == Decimal("21536400257.00")
    with pytest.raises(ValueError):
        expected_layer_cost(0)
