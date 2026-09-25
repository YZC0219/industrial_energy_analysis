"""阶段三实时事件契约的无依赖守卫。"""
import base64
import json
import re
from pathlib import Path
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[1]
SCHEMA=json.loads((ROOT/"streaming/schemas/energy_event.schema.json").read_text(encoding="utf-8"))
COMPOSE=(ROOT/"docker-compose.yml").read_text(encoding="utf-8")
FLINK_SQL=(ROOT/"streaming/flink/energy_window.sql").read_text(encoding="utf-8")
QUALITY_SQL=(ROOT/"streaming/flink/energy_quality_routes.sql").read_text(encoding="utf-8")
RAW_SQL=(ROOT/"streaming/flink/energy_raw_quarantine.sql").read_text(encoding="utf-8")

def test_event_contract_has_version_identity_and_two_clocks():
    required=set(SCHEMA["required"])
    assert {"schema_version","event_id","event_time","updated_at","record_date"} <= required
    assert SCHEMA["properties"]["schema_version"]["const"] == 1

def test_event_contract_can_express_corrections_and_deletes():
    assert set(SCHEMA["properties"]["op"]["enum"]) == {"UPSERT","DELETE"}
    assert "updated_at" in SCHEMA["properties"]
    assert "allOf" in SCHEMA

def test_business_key_and_metrics_are_required():
    required=set(SCHEMA["required"])
    assert {"workshop_code","energy_code","consumption","unit_price","cost"} <= required
    assert SCHEMA["additionalProperties"] is False


def test_local_kafka_flink_profile_has_checkpoints_and_durable_state_volume():
    import yaml

    compose=yaml.safe_load(COMPOSE)
    services=compose["services"]
    assert services["kafka"]["profiles"] == ["streaming"]
    assert services["flink-jobmanager"]["profiles"] == ["streaming"]
    assert services["flink-taskmanager"]["profiles"] == ["streaming"]
    assert int(services["kafka"]["environment"]["KAFKA_TRANSACTION_MAX_TIMEOUT_MS"]) >= 7_200_000
    assert "flink-streaming-checkpoints" in COMPOSE
    assert "execution.checkpointing.mode: EXACTLY_ONCE" in COMPOSE
    assert "execution.checkpointing.interval: 2s" in COMPOSE
    assert COMPOSE.count("taskmanager.numberOfTaskSlots: 5") == 2
    init_command = services["kafka-topics-init"]["command"][0]
    assert {"energy-events", "energy-alerts", "energy-late-events",
            "energy-delete-events", "energy-invalid-events",
            "energy-malformed-events"} <= set(
                re.findall(r"--topic ([\w-]+)", init_command)
            )
    assert COMPOSE.count("./streaming/build:/udf:ro") == 2


def test_flink_sql_uses_bounded_out_of_order_event_time_windows_and_exactly_once_sink():
    assert "WATERMARK FOR event_time_safe AS event_time_safe - INTERVAL '5' SECOND" in FLINK_SQL
    assert "TUMBLE(TABLE energy_events, DESCRIPTOR(event_time_safe), INTERVAL '10' SECOND)" in FLINK_SQL
    assert "'sink.delivery-guarantee' = 'exactly-once'" in FLINK_SQL
    assert "WHERE total_cost >= 100.00" in FLINK_SQL


def test_demo_stream_contains_out_of_order_events_and_watermark_advancer():
    from tools.run_streaming_experiment import build_window_events

    base=datetime(2026, 9, 24, 10, 0, 0, tzinfo=timezone.utc)
    events, advancer=build_window_events("test-run",base)
    offsets=[(datetime.fromisoformat(item["event_time"].replace("Z","+00:00"))-base).total_seconds()
             for item in events]
    assert offsets == [1,5,3]
    assert sum(item["cost"] for item in events)==155
    assert (datetime.fromisoformat(advancer["event_time"].replace("Z","+00:00"))-base).total_seconds()==17


def test_docker_console_transport_explicitly_uses_utf8(monkeypatch):
    from tools import run_streaming_experiment as experiment

    captured = {}

    def fake_run(_command, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(experiment.subprocess, "run", fake_run)
    experiment._compose("ps")
    assert captured["encoding"] == "utf-8"
    assert captured["text"] is True


def test_flink_routes_late_delete_and_invalid_events_to_durable_topics():
    assert "CURRENT_WATERMARK(event_time_safe) IS NOT NULL" in QUALITY_SQL
    assert "event_time_safe <= CURRENT_WATERMARK(event_time_safe)" in QUALITY_SQL
    assert QUALITY_SQL.count("WATERMARK FOR") == 1
    assert "'topic' = 'energy-late-events'" in QUALITY_SQL
    assert "WHERE op = 'DELETE'" in QUALITY_SQL
    assert "'topic' = 'energy-delete-events'" in QUALITY_SQL
    assert "'topic' = 'energy-invalid-events'" in QUALITY_SQL
    assert "'sink.delivery-guarantee' = 'exactly-once'" in QUALITY_SQL
    assert "properties.group.id" in QUALITY_SQL
    assert "does not capture a physical MySQL delete" in QUALITY_SQL
    groups = re.findall(r"'properties.group.id'\s*=\s*'([^']+)'", QUALITY_SQL)
    assert len(groups) == len(set(groups)) == 3


def test_raw_quarantine_keeps_kafka_offsets_and_original_bytes():
    assert "payload BYTES" in RAW_SQL
    assert "METADATA FROM 'partition' VIRTUAL" in RAW_SQL
    assert "METADATA FROM 'offset' VIRTUAL" in RAW_SQL
    assert "'format' = 'raw'" in RAW_SQL
    assert "'topic' = 'energy-malformed-events'" in RAW_SQL
    assert "strict_utf8(payload)" in RAW_SQL
    assert "raw_base64(payload)" in RAW_SQL
    assert "json_text IS JSON OBJECT" in RAW_SQL
    assert "unsupported_schema_version" in RAW_SQL
    assert "invalid_numeric_field" in RAW_SQL
    assert "invalid_event_time" in RAW_SQL
    assert "invalid_updated_at" in RAW_SQL
    assert "invalid_record_date" in RAW_SQL
    assert "valid_iso_datetime(JSON_VALUE(json_text, '$.event_time'))" in RAW_SQL
    assert "valid_iso_datetime(JSON_VALUE(json_text, '$.updated_at'))" in RAW_SQL
    assert "valid_iso_date(JSON_VALUE(json_text, '$.record_date'))" in RAW_SQL
    assert "iso_epoch_millis(event_time) IS NOT NULL" in FLINK_SQL
    assert "valid_iso_datetime(updated_at)" in FLINK_SQL
    assert "valid_iso_date(record_date)" in FLINK_SQL
    assert "event_time_safe AS TO_TIMESTAMP_LTZ" in FLINK_SQL
    assert "'sink.delivery-guarantee' = 'exactly-once'" in RAW_SQL
    assert "WHERE op = 'UPSERT' AND schema_version = 1" in FLINK_SQL
    assert QUALITY_SQL.count("schema_version INT") == 3
    assert FLINK_SQL.count("'json.ignore-parse-errors' = 'true'") == 1
    assert QUALITY_SQL.count("'json.ignore-parse-errors' = 'true'") == 3


def test_checked_in_four_fault_runtime_evidence_has_recoverable_payloads():
    report=json.loads((ROOT/"output/streaming_experiment_schema_20260926.json")
                      .read_text(encoding="utf-8"))
    assert report["success"] is True
    assert report["taskmanager_restart_tested"] is True
    assert all(report[key] for key in (
        "malformed_json_quarantine_tested", "invalid_utf8_quarantine_tested",
        "unsupported_schema_quarantine_tested", "invalid_numeric_quarantine_tested",
        "late_side_output_tested", "delete_event_route_tested",
        "invalid_event_quarantine_tested",
    ))
    evidence={item["quality_error"]: base64.b64decode(item["payload_base64"], validate=True)
              for item in report["malformed_events"]}
    assert set(evidence)=={"invalid_json", "invalid_utf8",
                           "unsupported_schema_version", "invalid_numeric_field"}
    assert json.loads(evidence["unsupported_schema_version"])["schema_version"] == 2
    assert json.loads(evidence["invalid_numeric_field"])["consumption"] == "not-a-number"
    try:
        evidence["invalid_utf8"].decode("utf-8")
    except UnicodeDecodeError:
        pass
    else:
        raise AssertionError("invalid_utf8 evidence unexpectedly decodes")


def test_checked_in_temporal_runtime_evidence_covers_all_seven_faults():
    report = json.loads((ROOT / "output/streaming_experiment_temporal_20260926.json")
                        .read_text(encoding="utf-8"))
    assert report["success"] is True
    assert report["taskmanager_restart_tested"] is True
    assert report["completed_checkpoint_before_restart"] == report["restored_checkpoint_id"]
    assert all(report[key] for key in (
        "malformed_json_quarantine_tested", "invalid_utf8_quarantine_tested",
        "unsupported_schema_quarantine_tested", "invalid_numeric_quarantine_tested",
        "invalid_event_time_quarantine_tested", "invalid_updated_at_quarantine_tested",
        "invalid_record_date_quarantine_tested", "seconds_level_alert",
    ))
    events = report["malformed_events"]
    assert len(events) == 7
    assert len({(item["source_partition"], item["source_offset"]) for item in events}) == 7
    evidence = {item["quality_error"]: base64.b64decode(item["payload_base64"], validate=True)
                for item in events}
    assert set(evidence) == {
        "invalid_json", "invalid_utf8", "unsupported_schema_version",
        "invalid_numeric_field", "invalid_event_time", "invalid_updated_at",
        "invalid_record_date",
    }
    assert json.loads(evidence["invalid_event_time"])["event_time"] == "2026-13-99T00:00:00Z"
    assert json.loads(evidence["invalid_updated_at"])["updated_at"] == "2026-02-30T00:00:00Z"
    assert json.loads(evidence["invalid_record_date"])["record_date"] == "2026-02-30"


def test_poison_before_watermark_does_not_contaminate_alert():
    from tools.run_streaming_experiment import build_fault_samples

    template = {"schema_version": 1, "event_time": "2026-09-25T22:12:01Z",
                "updated_at": "2026-09-25T22:11:00Z", "record_date": "2026-09-25",
                "op": "UPSERT", "consumption": 60, "unit_price": 1, "cost": 60}
    faults = build_fault_samples("unit-run", template)
    assert len(faults) == 7
    for reason in ("unsupported_schema_version", "invalid_numeric_field",
                   "invalid_event_time", "invalid_updated_at", "invalid_record_date"):
        assert json.loads(faults[reason])["cost"] == 500

    report = json.loads((ROOT / "output/streaming_experiment_poison_20260926.json")
                        .read_text(encoding="utf-8"))
    assert report["success"] is True
    assert report["faults_sent_before_watermark"] is True
    assert report["alert_latency_reference"] == "watermark_publish_started"
    assert report["alert"]["event_count"] == 3
    assert report["alert"]["total_cost"] == 155
    assert len(report["malformed_events"]) == 7
    assert {item["quality_error"] for item in report["malformed_events"]} == set(faults)
