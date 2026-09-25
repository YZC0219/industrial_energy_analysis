"""无数据库的 MySQL 装载辅助函数测试。"""
from import_mysql import (
    _date_range_is_covered,
    _file_max_date,
    _file_min_date,
    build_upsert_sql,
    ensure_soft_delete_column,
)
import pytest


def test_file_date_helpers_cover_both_calendar_boundaries(tmp_path):
    path = tmp_path / "dates.csv"
    path.write_text("record_date\n2023-12-30\n2026-01-02\n", encoding="utf-8")
    assert _file_min_date(str(path), "record_date") == "2023-12-30"
    assert _file_max_date(str(path), "record_date") == "2026-01-02"


def test_calendar_coverage_checks_both_lower_and_upper_bounds():
    assert _date_range_is_covered("2024-01-01", "2025-12-31", "2024-01-01", "2025-12-31")
    assert _date_range_is_covered("2024-01-01", "2025-12-31", "2024-06-01", "2025-06-01")
    assert not _date_range_is_covered("2024-01-01", "2025-12-31", "2023-12-31", "2024-01-02")
    assert not _date_range_is_covered("2024-01-01", "2025-12-31", "2025-12-30", "2026-01-01")


def test_fact_upsert_carries_soft_delete_state():
    sql = build_upsert_sql(
        "fact_energy_consumption",
        ["updated_at", "is_deleted"],
        "industrial_energy",
    )
    assert "`is_deleted` = IF(" in sql
    assert "new.`is_deleted`" in sql


def test_soft_delete_migration_rejects_unsafe_database_identifier():
    with pytest.raises(ValueError, match="simple SQL identifier"):
        ensure_soft_delete_column(None, "industrial_energy`; DROP DATABASE other; --")
