"""阶段三实时事件契约的无依赖守卫。"""
import json
from pathlib import Path
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[1]
SCHEMA=json.loads((ROOT/"streaming/schemas/energy_event.schema.json").read_text(encoding="utf-8"))
COMPOSE=(ROOT/"docker-compose.yml").read_text(encoding="utf-8")
FLINK_SQL=(ROOT/"streaming/flink/energy_window.sql").read_text(encoding="utf-8")

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
    assert "flink-streaming-checkpoints" in COMPOSE
    assert "execution.checkpointing.mode: EXACTLY_ONCE" in COMPOSE
    assert "execution.checkpointing.interval: 2s" in COMPOSE


def test_flink_sql_uses_bounded_out_of_order_event_time_windows_and_exactly_once_sink():
    assert "WATERMARK FOR event_time AS event_time - INTERVAL '5' SECOND" in FLINK_SQL
    assert "TUMBLE(TABLE energy_events, DESCRIPTOR(event_time), INTERVAL '10' SECOND)" in FLINK_SQL
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
