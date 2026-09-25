"""The CDC preflight must stay read-only and never imply a CDC stream exists."""
import json
from pathlib import Path

from tools import check_mysql_cdc_readiness as readiness

ROOT = Path(__file__).resolve().parents[1]


def test_preflight_queries_are_read_only():
    assert readiness.VARIABLES_SQL.startswith("SHOW VARIABLES")
    assert readiness.BINLOG_STATUS_SQL.startswith("SHOW ")
    assert readiness.PRIMARY_KEY_SQL.startswith("SELECT ")
    assert readiness.COLUMNS_SQL.startswith("SELECT ")
    for sql in (readiness.VARIABLES_SQL, readiness.BINLOG_STATUS_SQL,
                readiness.PRIMARY_KEY_SQL, readiness.COLUMNS_SQL):
        assert ";" not in sql


def test_preflight_requires_fact_table_even_with_binlog_enabled(monkeypatch):
    def fake_query(sql):
        if sql == readiness.VARIABLES_SQL:
            return [["log_bin", "ON"], ["binlog_format", "ROW"],
                    ["binlog_row_image", "FULL"], ["server_id", "1"]]
        if sql == readiness.BINLOG_STATUS_SQL:
            return [["binlog.000001", "123"]]
        return []

    monkeypatch.setattr(readiness, "_query", fake_query)
    result = readiness.inspect()
    assert result["checks"]["binary_logging_enabled"] is True
    assert result["checks"]["fact_primary_key_id"] is False
    assert result["binlog_capture_prerequisites_met"] is False
    assert result["physical_delete_cdc_implemented"] is False


def test_preflight_reports_prerequisites_without_claiming_cdc(monkeypatch):
    def fake_query(sql):
        if sql == readiness.VARIABLES_SQL:
            return [["log_bin", "ON"], ["binlog_format", "ROW"],
                    ["binlog_row_image", "FULL"], ["server_id", "1"]]
        if sql == readiness.BINLOG_STATUS_SQL:
            return [["binlog.000001", "123"]]
        if sql == readiness.PRIMARY_KEY_SQL:
            return [["id"]]
        return [[name] for name in sorted(readiness.REQUIRED_COLUMNS)]

    monkeypatch.setattr(readiness, "_query", fake_query)
    result = readiness.inspect()
    assert result["binlog_capture_prerequisites_met"] is True
    assert result["physical_delete_cdc_implemented"] is False
    assert result["full_snapshot_and_stream_verified"] is False


def test_missing_server_id_fails_closed(monkeypatch):
    def fake_query(sql):
        if sql == readiness.VARIABLES_SQL:
            return [["log_bin", "ON"], ["binlog_format", "ROW"],
                    ["binlog_row_image", "FULL"]]
        if sql == readiness.BINLOG_STATUS_SQL:
            return [["binlog.000001", "123"]]
        if sql == readiness.PRIMARY_KEY_SQL:
            return [["id"]]
        return [[name] for name in sorted(readiness.REQUIRED_COLUMNS)]

    monkeypatch.setattr(readiness, "_query", fake_query)
    result = readiness.inspect()
    assert result["checks"]["nonzero_server_id"] is False
    assert result["binlog_capture_prerequisites_met"] is False


def test_checked_in_mysql_preflight_keeps_missing_fact_visible():
    result = json.loads((ROOT / "output/mysql_cdc_readiness_20260926.json")
                        .read_text(encoding="utf-8"))
    assert result["variables"]["log_bin"] == "ON"
    assert result["checks"]["fact_primary_key_id"] is False
    assert result["checks"]["required_fact_columns"] is False
    assert result["binlog_capture_prerequisites_met"] is False
    assert result["physical_delete_cdc_implemented"] is False
