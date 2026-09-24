"""Config-driven quality checks must pass clean data and reject bad batches."""
import json

import pandas as pd

from tools.check_data_quality import main, validate_frames

def _frames():
    return (
        pd.DataFrame({
            "record_date": ["2025-01-01", "2025-01-01"],
            "workshop_code": ["W01", "W01"],
            "energy_code": ["E01", "E02"],
            "consumption": [10.0, 5.0], "unit_price": [0.6, 3.2],
            "cost": [6.0, 16.0], "record_status": ["正常", "正常"],
            "is_production_day": [1, 1],
        }),
        pd.DataFrame({
            "record_date": ["2025-01-01"], "workshop_code": ["W01"],
            "output_qty": [12.5],
        }),
    )


def test_configured_gx_and_cross_table_rules_pass_clean_frames():
    energy, production = _frames()
    report = validate_frames(energy, production)
    assert report["success"] is True
    assert report["failed"] == 0
    assert any(item["engine"] == "Great Expectations" for item in report["checks"])
    assert any(item["rule_id"] == "energy_workshop_day_exists_in_production"
               for item in report["checks"])


def test_gate_rejects_duplicate_key_negative_value_and_orphan_relation():
    energy, production = _frames()
    energy.loc[0, "workshop_code"] = "W99"
    energy.loc[1, "energy_code"] = "E01"
    energy.loc[0, "consumption"] = -1
    energy.loc[1, "workshop_code"] = "W99"
    report = validate_frames(energy, production)
    failed = {item["rule_id"] for item in report["checks"] if not item["success"]}
    assert report["success"] is False
    assert "energy.business_key_unique" in failed
    assert "energy.consumption_non_negative" in failed
    assert "energy_workshop_day_exists_in_production" in failed


def test_gate_fails_closed_when_contract_columns_are_missing():
    energy, production = _frames()
    energy = energy.drop(columns=["record_date"])
    report = validate_frames(energy, production)
    assert report["success"] is False
    assert any(item.get("rule_id") == "energy.required_columns"
               and "record_date" in item["missing_columns"] for item in report["checks"])


def test_gate_rejects_empty_batches():
    energy, production = _frames()
    report = validate_frames(energy.iloc[0:0], production)
    failed = {item["rule_id"] for item in report["checks"] if not item["success"]}
    assert report["success"] is False
    assert "energy.batch_not_empty" in failed


def test_cli_writes_failure_report_and_returns_nonzero(tmp_path):
    energy, production = _frames()
    energy.loc[0, "consumption"] = -1
    energy_path = tmp_path / "energy.csv"
    production_path = tmp_path / "production.csv"
    report_path = tmp_path / "quality.json"
    energy.to_csv(energy_path, index=False)
    production.to_csv(production_path, index=False)
    exit_code = main(["--energy", str(energy_path), "--production", str(production_path),
                      "--output", str(report_path)])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert report["success"] is False
