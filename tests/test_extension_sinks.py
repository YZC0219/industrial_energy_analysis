"""Sink request boundaries and rejection of incomplete/stale runtime evidence."""
import pytest
from streaming.minute_store import insert_rows, table_name, clickhouse_query
from tools.summarize_extension_runtime import validate_sinks, validate_scheduler


def test_unsafe_database_and_nan_do_not_send_requests():
    with pytest.raises(ValueError, match="identifier"):
        table_name("energy_poc; DROP DATABASE other")
    with pytest.raises(ValueError):
        insert_rows([{"mean_kw": float("nan")}], "http://127.0.0.1:1")
    assert insert_rows([], "http://127.0.0.1:1") == 0
    with pytest.raises(ValueError, match="max_threads"):
        clickhouse_query("http://127.0.0.1:1", "SELECT 1", max_threads=0)


def test_sink_summary_requires_failures_and_replay_checks():
    report = {"success": True, "source_hashes": {"streaming/dispatch.py": "hash"}, "source_power_report_sha256": "power",
              "redis": {"alert_keys": 20, "ttl_min_seconds": 86400, "ttl_max_seconds": 86400},
              "webhook": {"alert_attempts": 40, "unique_alerts": 20, "failure_503_propagated": True,
                          "partial_redis_success_observed": True, "explicit_retry_recovered": True},
              "clickhouse": {"unique_minute_rows": 91, "physical_rows": 182, "identical_replay_final_reconciled": True, "missing_table_failed": True}}
    validate_sinks(report, report["source_hashes"], "power")
    with pytest.raises(ValueError, match="stale"):
        validate_sinks(report, {"streaming/dispatch.py": "new"}, "power")
    report["webhook"]["failure_503_propagated"] = False
    with pytest.raises(ValueError, match="failed"):
        validate_sinks(report, report["source_hashes"], "power")


def test_scheduler_summary_rejects_dag_test_and_one_run():
    with pytest.raises(ValueError, match="scheduler"):
        validate_scheduler({"success": True, "scope": "DAG.test"}, {}, "dag")
    report = {"success": True, "identical_replay_verified": True, "source_dag_sha256": "dag", "source_hashes": {}, "runs": []}
    with pytest.raises(ValueError, match="Two scheduler runs"):
        validate_scheduler(report, {}, "dag")
