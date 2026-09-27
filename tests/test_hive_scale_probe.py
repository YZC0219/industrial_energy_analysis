"""Safety and expected-grain checks for the isolated scale materializer."""
import json
from decimal import Decimal
from pathlib import Path

import pytest

from tools.materialize_hive_scale_probe import expected_counts, validate_database_name
from tools.verify_hive_scale_probe import KEYS, expected_layer_cost


EVIDENCE = Path(__file__).resolve().parents[1] / "output"


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


@pytest.mark.parametrize("scale", [10, 100])
def test_committed_materialization_evidence_matches_layer_contract(scale):
    report = json.loads((EVIDENCE / f"hive_scale_probe_{scale}x_20260927.json").read_text())
    assert report["success"] is True
    assert report["scale"] == scale
    assert report["source_rows"] == 19006
    assert Decimal(report["source_cost"]) == Decimal("215364002.57")
    assert report["master"] == "local[2]"
    assert set(report["layers"]) == set(expected_counts(scale))
    for layer, count in expected_counts(scale).items():
        result = report["layers"][layer]
        assert result["rows"] == count
        assert Decimal(result["cost"]) == expected_layer_cost(scale)
        assert result["write_seconds"] > 0


def test_committed_quality_evidence_checks_all_scales_and_layers():
    report = json.loads((EVIDENCE / "hive_scale_quality_20260927.json").read_text())
    assert report["success"] is True
    assert report["database"] == "energy_scale_probe_20260927"
    assert set(report["scales"]) == {"10", "100"}
    for scale in (10, 100):
        result = report["scales"][str(scale)]
        assert result["success"] is True
        assert set(result["layers"]) == set(expected_counts(scale))
        for layer, count in expected_counts(scale).items():
            check = result["layers"][layer]
            assert check["rows"] == count
            assert check["plant_count"] == scale
            assert Decimal(check["cost"]) == expected_layer_cost(scale)
            assert check["duplicate_groups_found"] is False
            assert check["null_key_rows_found"] is False
        assert result["layers"]["dws"]["fact_count_sum"] == result["layers"]["dwd"]["rows"]
        assert result["layers"]["ads"]["workshop_count_sum"] == result["layers"]["dws"]["rows"]
        coal = Decimal(result["layers"]["dwd"]["std_coal_kgce"])
        assert Decimal(result["layers"]["dws"]["std_coal_kgce"]) == coal
        assert Decimal(result["layers"]["ads"]["std_coal_kgce"]) == coal
