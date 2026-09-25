"""无数据库的 MySQL 装载辅助函数测试。"""
from import_mysql import (
    _date_range_is_covered,
    _file_max_date,
    _file_min_date,
    build_upsert_sql,
    ensure_soft_delete_column,
    split_sql,
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


def test_split_sql_keeps_semicolons_inside_literals_and_drops_comments():
    statements = split_sql(
        "-- header with a ;\n"
        "CREATE TABLE `a;b` (note VARCHAR(20) COMMENT 'soft; delete');\n"
        "/* block ; comment */\n"
        "INSERT INTO `a;b` VALUES ('it''s; fine'); # trailing ; comment\n"
    )
    assert len(statements) == 2
    assert "COMMENT 'soft; delete'" in statements[0]
    assert "'it''s; fine'" in statements[1]
    assert "comment" not in statements[1]


def test_create_table_script_keeps_tombstone_comment_intact():
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "sql" / "create_table.sql"
    statements = split_sql(script.read_text(encoding="utf-8"))
    fact = [stmt for stmt in statements if "CREATE TABLE" in stmt
            and "fact_energy_consumption" in stmt]
    assert len(fact) == 1
    assert "COMMENT '软删除标记; 增量 CDC tombstone'" in fact[0]
