"""Guard the isolated execution boundary for production lakehouse SQL."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from tools.check_isolated_lakehouse_replay import isolated_sql, validate_database


DB = "energy_contract_probe_20260927"
REPORT = Path(__file__).resolve().parents[1] / "output" / "isolated_lakehouse_replay_20260927.json"


def test_probe_database_must_be_new_narrowly_named_namespace():
    assert validate_database(DB) == DB
    assert validate_database("energy_datax_probe_20260927") == "energy_datax_probe_20260927"
    for name in ("energy_dwd", "energy_contract_probe", "energy_contract_probe_2026-09-27",
                 "energy_contract_probe_20260927.other", "energy_contract_probe_20260927;DROP"):
        with pytest.raises(ValueError):
            validate_database(name)


def test_sql_rewrites_all_production_schemas_and_storage_locations():
    sql = "\n".join(
        f"CREATE TABLE {name}.example (id INT) LOCATION '/warehouse/{name}/example';"
        for name in ("energy_ods", "energy_dwd", "energy_dws", "energy_ads")
    )
    isolated = isolated_sql(sql, DB)
    assert isolated.count(f"{DB}.example") == 4
    assert isolated.count(f"hdfs://localhost:9000/warehouse/{DB}/example") == 4
    assert "energy_ods." not in isolated
    assert "energy_dwd." not in isolated
    assert "energy_dws." not in isolated
    assert "energy_ads." not in isolated


@pytest.mark.parametrize("sql", [
    "CREATE TABLE x LOCATION '/warehouse/energy_other/escape'",
    "CREATE TABLE x LOCATION 'file:/tmp/escape'",
])
def test_sql_refuses_unmapped_table_locations(sql):
    with pytest.raises(ValueError, match="non-isolated"):
        isolated_sql(sql, DB)


def test_committed_four_phase_replay_evidence():
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    assert report["success"] is True
    assert report["database"] == DB
    assert report["location"].endswith(f"/warehouse/{DB}")
    assert report["stage_column_order"] == "target_dt,is_deleted"
    assert report["master"] == "local[2]"
    phases = report["phases"]
    assert list(phases) == ["full", "corrected", "deleted", "stale_replay"]
    for name, expected_cost, deleted, e01_cost in (
        ("full", "30.00", 0, "10.00"),
        ("corrected", "37.00", 0, "17.00"),
        ("deleted", "20.00", 1, "0.00"),
        ("stale_replay", "20.00", 1, "0.00"),
    ):
        phase = phases[name]
        assert phase["success"] is True
        assert phase["business_date"] == "2024-03-15"
        assert phase["dwd_rows"] == phase["stage_rows"] == 2
        assert phase["e01_is_deleted"] == deleted
        assert Decimal(phase["e01_cost"]) == Decimal(e01_cost)
        for layer in ("dws_day_cost", "dws_month_cost", "ads_day_cost"):
            assert Decimal(phase[layer]) == Decimal(expected_cost)
    assert phases["corrected"]["e01_source_updated_at"] == "2026-09-27 11:00:00"
    assert phases["stale_replay"]["e01_source_updated_at"] == (
        phases["deleted"]["e01_source_updated_at"]
    )
