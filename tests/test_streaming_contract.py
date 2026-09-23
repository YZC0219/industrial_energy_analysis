"""阶段二事件契约的无依赖守卫。"""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SCHEMA=json.loads((ROOT/"streaming/schemas/energy_event.schema.json").read_text(encoding="utf-8"))

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
