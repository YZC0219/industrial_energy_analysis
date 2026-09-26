"""Pure contract checks for the read-only Spark scale experiment."""
import json
from decimal import Decimal
from pathlib import Path

import pytest

from tools.benchmark_spark_scale import parse_scales, validate_summary

ROOT = Path(__file__).resolve().parents[1]


def test_scale_plan_requires_distinct_positive_scales_and_a_baseline():
    assert parse_scales("1,10,100") == (1, 10, 100)
    for invalid in ("", "0,1", "1,1", "10,100", "1,-10"):
        with pytest.raises(ValueError):
            parse_scales(invalid)


def test_scale_validation_checks_input_and_output_grain_and_exact_cost():
    baseline = {"fact_rows": 19006, "daily_rows": 5848,
                "total_cost": Decimal("123.45")}
    valid = {"fact_rows": 190060, "daily_rows": 58480,
             "total_cost": Decimal("1234.50")}
    validate_summary(valid, baseline, 10)
    for key in valid:
        wrong = dict(valid)
        wrong[key] -= 1
        with pytest.raises(ValueError, match="mismatch"):
            validate_summary(wrong, baseline, 10)


def test_checked_in_vm_scale_evidence_is_consistent_and_scoped():
    records = [json.loads(line) for line in (
        ROOT / "output/spark_scale_benchmark_20260926.jsonl"
    ).read_text(encoding="utf-8").splitlines()]
    assert len(records) == 9
    assert {(item["scale"], item["trial"]) for item in records} == {
        (scale, trial) for scale in (1, 10, 100) for trial in (1, 2, 3)
    }
    for item in records:
        scale = item["scale"]
        assert item["validated"] is True
        assert item["master"] == "local[2]"
        assert item["driver_memory"] == "3g"
        assert item["shuffle_partitions"] == "8"
        assert item["source_has_is_deleted"] is False  # Historical VM table schema.
        assert item["fact_rows"] == 19006 * scale
        assert item["daily_rows"] == 5848 * scale
        assert Decimal(item["total_cost"]) == Decimal("215364002.57") * scale
        assert item["source_total_cost"] == "215364002.57"
        assert item["elapsed_seconds"] > 0
        assert "no Hive writes" in item["scope"]
