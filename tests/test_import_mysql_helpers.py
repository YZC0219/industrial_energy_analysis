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


def test_explicit_incremental_cold_start_needs_only_current_batch(monkeypatch, tmp_path):
    import import_mysql as module
    import sys
    (tmp_path / "dim_calendar.csv").write_text("calendar_date\n2026-10-02\n")
    for name in ("clean_batch_energy.csv", "clean_batch_production.csv"):
        (tmp_path / name).write_text("record_date\n2026-10-02\n")
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def execute(self, sql, *_): self.sql = sql
        def fetchone(self): return ("8.0",) if "VERSION()" in self.sql else (1,)
    class Connection:
        def cursor(self): return Cursor()
        def commit(self): pass
        def close(self): pass
    monkeypatch.setattr(module, "connect", lambda _: Connection())
    monkeypatch.setattr(module, "read_watermark", lambda *_: (None, None))
    monkeypatch.setattr(module, "ensure_soft_delete_column", lambda *_: None)
    loaded = []
    def load(_, table, name, *args, **kwargs):
        assert (tmp_path / name).is_file(), name
        loaded.append(name)
        return 1
    def advance(_, database, table, column, old, *args):
        assert old == "1970-01-01 00:00:00"
        return "2026-10-02 00:00:00"
    monkeypatch.setattr(module, "load_csv", load)
    monkeypatch.setattr(module, "advance_watermark", advance)
    monkeypatch.setattr(sys, "argv", ["import_mysql.py", "--incremental", "--batch-dir", str(tmp_path)])
    module.main()
    assert loaded == ["dim_calendar.csv", "clean_batch_energy.csv", "clean_batch_production.csv"]
