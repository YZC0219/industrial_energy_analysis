"""Validate the reproducible DataX probe contract without requiring a cluster."""

import json
from pathlib import Path

import pytest

from tools.verify_isolated_datax_sync import probe_names, source_ddl


REPORT = Path(__file__).resolve().parents[1] / "output" / "isolated_datax_sync_20260927.json"


def test_probe_names_are_narrow_and_pair_source_with_target():
    assert probe_names("20260927") == (
        "industrial_energy_datax_probe_20260927", "energy_datax_probe_20260927"
    )
    for invalid in ("2026-09-27", "20260927;DROP", "energy_ods", ""):
        with pytest.raises(ValueError):
            probe_names(invalid)


def test_mysql_fixture_has_timestamp_and_tombstone_columns():
    sql = source_ddl("industrial_energy_datax_probe_20260927")
    assert "updated_at DATETIME(6) NOT NULL" in sql
    assert "is_deleted TINYINT NOT NULL DEFAULT 0" in sql
    assert "2024-03-15" in sql
    assert "industrial_energy.fact_energy_consumption" not in sql
    with pytest.raises(ValueError):
        source_ddl("industrial_energy")


def test_committed_datax_runtime_evidence_has_full_delta_delete_and_retry():
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    assert report["success"] is True
    assert report["mysql_database"] == "industrial_energy_datax_probe_20260927"
    assert report["hive_database"] == "energy_datax_probe_20260927"
    assert report["hdfs_location"].endswith("/warehouse/energy_datax_probe_20260927")
    phases = report["phases"]
    assert list(phases) == ["full", "corrected", "deleted", "retry"]
    assert all(item["success"] is True for item in phases.values())
    assert phases["full"]["rows"] == 2
    assert phases["full"]["versions"]["1"]["cost"] == "10.00"
    assert phases["full"]["versions"]["2"]["cost"] == "20.00"
    assert phases["corrected"]["rows"] == 1
    assert phases["corrected"]["versions"]["1"] == {
        "cost": "17.00", "is_deleted": 0, "updated_at": "2026-09-27 11:00:00",
    }
    assert phases["deleted"]["rows"] == phases["retry"]["rows"] == 1
    assert phases["deleted"]["versions"] == phases["retry"]["versions"] == {
        "1": {"cost": "17.00", "is_deleted": 1,
              "updated_at": "2026-09-28 09:00:00"},
    }
