"""Schema variants for existing migrated and fresh Hive merge stages."""
import json
from pathlib import Path

import pytest

from tools.verify_hive_soft_delete_runtime import classify_stage_columns

ROOT = Path(__file__).resolve().parents[1]


def test_stage_schema_accepts_both_orders_protected_by_named_insert():
    assert classify_stage_columns(["etl_time", "target_dt", "is_deleted", "run_dt"]) == (
        "legacy_append_is_deleted"
    )
    assert classify_stage_columns(["etl_time", "is_deleted", "target_dt", "run_dt"]) == (
        "fresh_ddl_is_deleted_first"
    )


@pytest.mark.parametrize("columns", [
    ["target_dt"], ["target_dt", "is_deleted", "is_deleted"],
    ["is_deleted", "other", "target_dt"],
])
def test_stage_schema_rejects_unexpected_orders(columns):
    with pytest.raises(ValueError):
        classify_stage_columns(columns)


def test_checked_in_vm_migration_evidence_preserves_old_facts():
    report = json.loads((ROOT / "output/hive_soft_delete_runtime_20260927.json")
                        .read_text(encoding="utf-8"))
    assert report["success"] is True
    assert report["stage_schema_variant"] == "legacy_append_is_deleted"
    assert report["dwd"] == {
        "rows": 19006, "cost": "215364002.57", "deleted_sum": 0,
    }
    assert report["stage"] == {
        "rows": 19032, "target_dt_nonnull": 19032, "deleted_sum": 0,
    }
    assert all(details["is_deleted_type"] == "tinyint"
               for details in report["tables"].values())
    assert report["tables"]["energy_dwd.dwd_energy_consumption_merge_stage"][
        "columns"][-4:] == ["target_dt", "is_deleted", "run_dt", "batch_id"]
    assert "no partition writes" in report["scope"]
