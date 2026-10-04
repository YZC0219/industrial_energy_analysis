"""Validate saved runtime evidence against current code before publishing a summary."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def validate(power, iceberg, airflow, sql_hash, iceberg_hash):
    if not (power.get("success") and power.get("sql_sha256") == sql_hash
            and power.get("expected_windows") == power.get("observed_windows") == 20
            and power.get("mean_slope_count_reconciled") and power.get("cross_workshop_isolation")):
        raise ValueError("Missing, failed or stale Kafka/Flink evidence")
    if not (iceberg.get("status") == "passed" and iceberg.get("source_sha256") == iceberg_hash
            and iceberg.get("current_consumption") == 120 and iceberg.get("historical_consumption") == 100
            and iceberg.get("replay_count") == 1 and iceberg.get("snapshots")):
        raise ValueError("Missing, failed or stale Iceberg evidence")
    cases = {case["mode"]: case for case in airflow.get("cases", [])}
    good, bad = cases.get("good", {}), cases.get("missing_energy", {})
    if not (airflow.get("success") and good.get("task_states") == {"coverage_gate": "success", "downstream_report": "success"}
            and good.get("downstream_file_exists") is True
            and bad.get("task_states") == {"coverage_gate": "failed", "downstream_report": "upstream_failed"}
            and bad.get("downstream_file_exists") is False):
        raise ValueError("Airflow pass/fail gate evidence is incomplete")


def validate_sinks(report, source_hashes, power_report_hash):
    redis, webhook, clickhouse = [report.get(key, {}) for key in ("redis", "webhook", "clickhouse")]
    if not (report.get("success") and report.get("source_hashes") == source_hashes
            and report.get("source_power_report_sha256") == power_report_hash
            and redis.get("alert_keys") == 20 and 86300 <= redis.get("ttl_min_seconds", 0) <= redis.get("ttl_max_seconds", 0) <= 86400
            and webhook.get("alert_attempts") == 40 and webhook.get("unique_alerts") == 20
            and all(webhook.get(key) is True for key in ("failure_503_propagated", "partial_redis_success_observed", "explicit_retry_recovered"))
            and clickhouse.get("unique_minute_rows") == 91 and clickhouse.get("physical_rows", 0) >= 91
            and clickhouse.get("identical_replay_final_reconciled") is True and clickhouse.get("missing_table_failed") is True):
        raise ValueError("Missing, failed or stale Redis/Webhook/ClickHouse evidence")


def validate_scheduler(report, source_hashes, dag_hash):
    if not (report.get("success") and report.get("identical_replay_verified")
            and report.get("source_dag_sha256") == dag_hash and report.get("source_hashes") == source_hashes):
        raise ValueError("Missing, failed or stale full scheduler evidence")
    runs = report.get("runs", [])
    if [run.get("phase") for run in runs] != ["initial", "identical_replay"]:
        raise ValueError("Two scheduler runs are required")
    for run in runs:
        tasks = {task["task_id"]: task for task in run.get("tasks", [])}
        if not (run.get("state") == "success" and len(tasks) == 19
                and all(task.get("state") == "success" for task in tasks.values())
                and len(run.get("query_sha256", {})) == 29 and run.get("quality_before_load") is True):
            raise ValueError("Incomplete scheduler task/query evidence")
        try:
            if not (tasks["quality_batch"]["end_date"] <= tasks["quality_energy_coverage"]["start_date"]
                    and tasks["quality_energy_coverage"]["end_date"] <= tasks["load_warehouse"]["start_date"]):
                raise ValueError("Scheduled load precedes quality gate completion")
        except (KeyError, TypeError) as exc:
            raise ValueError("Missing scheduler gate timestamps") from exc
    if runs[0].get("warehouse") != runs[1].get("warehouse") or runs[0]["query_sha256"] != runs[1]["query_sha256"]:
        raise ValueError("Scheduler replay changed the warehouse or query results")


def validate_kafka_outbox(report, source_hashes, alerts_hash):
    if not (report.get("success") and report.get("source_hashes") == source_hashes
            and report.get("source_alerts_sha256") == alerts_hash
            and report.get("records_published") == 22 and report.get("unique_durable_alerts") == 20
            and report.get("offset_before_restart") is None and report.get("offset_after_replay") == 21
            and report.get("poison_offset") == 21 and report.get("webhook_unique_received") == 20
            and report.get("final_states") == {"sent": 20}
            and all(report.get(key) is True for key in ("crash_after_persist_before_commit_recovered",
                    "duplicate_replay_deduplicated", "poison_restarts_blocked_without_commit", "all_flink_alert_payloads_match"))):
        raise ValueError("Missing, failed or stale Kafka outbox evidence")


def validate_redis_outbox(report, source_hashes, alerts_hash):
    if not (report.get("success") and report.get("source_hashes") == source_hashes
            and report.get("source_alerts_sha256") == alerts_hash
            and report.get("final_states") == {"sent": 20}
            and report.get("unique_received") == 20 and report.get("redis_keys") == 20
            and report.get("pending_after_outage", 0) > 0
            and report.get("http_attempts", 0) >= 40
            and 86300 < report.get("ttl_min_seconds", 0) <= report.get("ttl_max_seconds", 0) <= 86400
            and all(report.get(key) is True for key in ("redis_cached_webhook_503_observed",
                    "redis_outage_durable_retry_observed", "process_restart_recovered",
                    "all_payloads_match", "auth_failure_dead_without_http"))):
        raise ValueError("Missing, failed or stale Redis outbox evidence")


def validate_outbox_operations(report, source_hashes, redis_report_hash):
    if not (report.get("success") and report.get("source_hashes") == source_hashes
            and report.get("source_redis_report_sha256") == redis_report_hash
            and report.get("automatic_restart_exit_codes") == [17, 17, 0]
            and report.get("bounded_failure_attempts") == 3
            and report.get("stop_terminated_child_without_restart") is True
            and report.get("read_only_inspection") is True and report.get("inspected_sent_count") == 20):
        raise ValueError("Missing, failed or stale outbox operations evidence")


def validate_depth(semantic, quarantine, equipment, semantic_hashes, quarantine_hashes, equipment_hash):
    if not (semantic.get("success") and semantic.get("source_hashes") == semantic_hashes
            and semantic.get("live_read_only_queries") == 29 and semantic.get("table_metrics_api_reconciled") == 29
            and semantic.get("scalar_metrics") == 7 and semantic.get("unsupported_filter_rejected") is True
            and set(semantic.get("manifest", {}).get("queries", {})) == {f"Q{i:02d}" for i in range(1,30)}
            and semantic["manifest"].get("consistency") == "MySQL REPEATABLE READ consistent snapshot READ ONLY"):
        raise ValueError("Missing, failed or stale semantic runtime evidence")
    if not (quarantine.get("success") and quarantine.get("source_hashes") == quarantine_hashes
            and [quarantine.get(k) for k in ("published", "committed_offset", "quarantined", "valid_alerts", "after_repair_alerts")] == [7,7,4,3,4]
            and all(quarantine.get(k) is True for k in ("crash_before_offset_commit_recovered", "raw_bytes_preserved", "repair_replay_idempotent", "repair_requires_explicit_input"))):
        raise ValueError("Missing, failed or stale quarantine runtime evidence")
    if not (equipment.get("success") and equipment.get("source_sha256") == equipment_hash
            and equipment.get("data_source") == "synthetic" and equipment.get("binary_states_min_up_ramp_site_and_precedence_reconciled") is True
            and abs(equipment.get("known_optimum_yuan", -1) - .2) < 1e-8 and equipment.get("mip_gap", 1) <= 1e-6
            and equipment.get("optimized_cost_yuan", 0) > 0
            and equipment.get("baseline_cost_yuan", 0) > equipment["optimized_cost_yuan"]):
        raise ValueError("Missing, failed or stale equipment runtime evidence")


def validate_consumers(report, source_hashes, semantic_report_hash):
    if not (report.get("success") and report.get("source_hashes") == source_hashes
            and report.get("source_semantic_report_sha256") == semantic_report_hash
            and report.get("report_data_unchanged") is True and report.get("table_metric_cases") == 29
            and report.get("legacy_anomaly_and_summary_ok") is True
            and len(report.get("legacy_api_cases", [])) == 3):
        raise ValueError("Missing, failed or stale semantic consumer evidence")


if __name__ == "__main__":
    folder = ROOT / "output/extensions"
    filenames = ("power_runtime.json", "iceberg_report.json", "airflow_quality_runtime.json", "sink_runtime.json", "pipeline_scheduler_runtime.json", "outbox_runtime.json", "kafka_outbox_runtime.json", "redis_outbox_runtime.json", "outbox_operations_runtime.json", "semantic_runtime.json", "quarantine_runtime.json", "equipment_runtime.json", "semantic_consumers_runtime.json")
    power, iceberg, airflow, sinks, scheduler, outbox, kafka_outbox, redis_outbox, operations, semantic, quarantine, equipment, consumers = [json.loads((folder / filename).read_text(encoding="utf-8")) for filename in filenames]
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    validate(power, iceberg, airflow, digest(ROOT / "streaming/flink/power_baseline.sql"),
             digest(ROOT / "lakehouse/iceberg_poc.py"))
    def current_hashes(paths):
        result = {}
        for name in paths:
            path = (ROOT / name).resolve()
            if not path.is_relative_to(ROOT.resolve()):
                raise ValueError("Source hash path is outside project")
            result[name] = digest(path)
        return result
    validate_sinks(sinks, current_hashes(sinks["source_hashes"]), digest(folder / "power_runtime.json"))
    validate_scheduler(scheduler, current_hashes(scheduler["source_hashes"]), digest(ROOT / "dags/energy_pipeline_dag.py"))
    if not (outbox.get("success") and outbox.get("source_sha256") == digest(ROOT / "streaming/alert_outbox.py")
            and all(outbox.get(key) is True for key in ("process_crash_after_remote_ack_recovered", "receiver_deduplicated", "automatic_retry_recovered", "manual_requeue_recovered"))
            and outbox.get("retry_exhaustion_attempts") == 3 and outbox.get("permanent_error_attempts") == 1
            and outbox.get("final_states") == {"dead": 1, "sent": 3}):
        raise ValueError("Missing, failed or stale outbox evidence")
    kafka_sources = ("streaming/kafka_outbox.py", "streaming/alert_outbox.py", "streaming/dispatch.py")
    validate_kafka_outbox(kafka_outbox, current_hashes(kafka_sources), digest(folder / "f2b3bd086c95/alerts.json"))
    validate_redis_outbox(redis_outbox, current_hashes(("streaming/alert_outbox.py", "streaming/dispatch.py")),
                          digest(folder / "f2b3bd086c95/alerts.json"))
    validate_outbox_operations(operations, current_hashes(("streaming/outbox_inspect.py", "streaming/outbox_supervisor.py")),
                              digest(folder / "redis_outbox_runtime.json"))
    guard_path = folder / 'delivery_guards_runtime.json'
    guards = json.loads(guard_path.read_text(encoding='utf-8'))
    expected_http = [(code, 'sent' if code < 300 else 'pending' if code == 503 else 'dead')
                     for code in (200,204,301,302,303,307,308,400,503)]
    if not (guards.get('success') and guards.get('source_hashes') == current_hashes(
                ('streaming/alert_outbox.py', 'streaming/dispatch.py', 'tools/verify_delivery_guards.py'))
            and [(c.get('http_status'), c.get('queue_state')) for c in guards.get('http_cases', [])] == expected_http
            and all(c.get('post_attempts') == 1 for c in guards['http_cases'])
            and all(guards.get(k) is True for k in ('redirects_not_followed',
                'lowered_budget_after_process_restart_blocks_delivery', 'manual_requeue_resets_budget'))):
        raise ValueError('Missing, failed or stale delivery acknowledgement/retry-budget evidence')
    validate_depth(semantic, quarantine, equipment,
                   current_hashes(("governance/metrics.yaml", "governance/catalog.py", "governance/export.py", "src/api.py")),
                   current_hashes(("streaming/kafka_outbox.py", "streaming/alert_quarantine.py")),
                   digest(ROOT / "optimization/equipment_schedule.py"))
    validate_consumers(consumers, current_hashes(("governance/consumers.py", "src/api.py", "src/make_report.py")),
                       digest(folder / "semantic_runtime.json"))
    bi_path = folder / 'metabase_semantic_runtime.json'
    bi = json.loads(bi_path.read_text(encoding='utf-8'))
    bi_folder = Path(bi['backup']).resolve().parent
    if not bi_folder.is_relative_to(folder.resolve()):
        raise ValueError('BI evidence is outside project output')
    browser_path = bi_folder / 'browser.json'
    browser = json.loads(browser_path.read_text(encoding='utf-8'))
    if not (bi.get('success') and bi.get('applied')
            and bi.get('source_hashes') == current_hashes(('governance/metabase.py', 'governance/metrics.yaml', 'tools/migrate_metabase_semantics.py'))
            and bi.get('canonical_daily_groups') == 5848
            and len(bi.get('checks', [])) == 6 and all(c.get('passed') for c in bi['checks'])
            and bi.get('permissions', {}).get('result') == 'pass'
            and browser.get('result') == 'pass' and browser.get('drillthrough_rows') == bi['permissions']['sample_filter']['rows']
            and browser.get('api_verified_cost_yuan') == bi['permissions']['sample_filter']['cost_yuan']):
        raise ValueError('Missing, failed or stale Metabase semantic evidence')
    front_path = folder / 'browser_semantic_runtime.json'
    front = json.loads(front_path.read_text(encoding='utf-8'))
    from governance.browser import contract
    if not (front.get('success') and front.get('contract_sha256') == contract()['sha256']
            and front.get('source_hashes') == current_hashes(('governance/browser.py', 'governance/metrics.yaml',
                'src/semantic_metrics.js', 'src/report_template.html', 'src/make_report.py', 'tools/verify_browser_semantics.py'))
            and front.get('source_semantic_report_sha256') == digest(folder / 'semantic_runtime.json')
            and len(front.get('scalar_cases', [])) == 10
            and all(c.get('passed') and c.get('metrics') == 7 for c in front['scalar_cases'])
            and all(front.get(k) is True for k in ('cost_shares_reconciled', 'actual_ui_single_workshop_and_reset',
                'day_type_totals_reconciled', 'net_energy_share_follows_filter', 'zero_empty_nonproduction_and_invalid_values_checked'))
            and front.get('browser_errors') == []):
        raise ValueError('Missing, failed or stale browser semantic evidence')
    summary = {"success": True, "generated_at": datetime.now(timezone.utc).isoformat(),
               "evidence_sha256": {**{filename: digest(folder / filename) for filename in filenames},
                                   bi_path.name: digest(bi_path), str(browser_path.relative_to(folder)): digest(browser_path),
                                   front_path.name: digest(front_path), guard_path.name: digest(guard_path)},
               "verified": ["Kafka/Flink 20 event-time windows", "Iceberg late merge/replay/time travel",
                            "Airflow isolated good/bad gate and downstream states", "Redis/Webhook/ClickHouse replay and failures",
                            "Two full main-DAG graph runs through isolated LocalExecutor scheduler",
                            "Durable Webhook outbox process-crash recovery, automatic retries and dead-letter recovery",
                            "Real Kafka outbox persist-before-commit crash/replay, poison blocking and 20 Webhook deliveries",
                            "Durable Redis/Webhook partial failure, Redis outage, process restart and auth rejection",
                            "Read-only outbox diagnostics and bounded process supervisor restart/cancellation",
                            "29 canonical semantic SQL analyses and API reconciliation in live read-only MySQL snapshot",
                            "Real Kafka poison quarantine, raw retention, crash recovery and explicit repair replay",
                            "Equipment MILP binary starts/min-up/ramp/site/process constraints and known optimum",
                            "Existing HTML report payload and legacy API migrated to semantic adapter without changing results",
                            "Two saved Metabase cost cards governed by semantic compiler, all workshop/day costs reconciled, filters/read-only permissions/browser drill verified",
                            "Seven offline browser scalar metrics reconciled in ten scopes, cost shares, actual controls/reset and net energy share checked",
                            "Direct HTTP 2xx acknowledgement, redirect rejection and restarted worker reduced retry budget verified"],
               "pending": ["live production deployment with enabled Hive/CDC/deep branches",
                           "real authorized notification receiver; browser analytical formulas beyond basic scalar metrics and additional BI metrics",
                           "real factory hourly forecast and realized savings", "multi-node HA"]}
    (folder / "runtime_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2))
