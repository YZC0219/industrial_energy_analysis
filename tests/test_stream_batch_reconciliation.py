import csv
import hashlib
import json

from tools.reconcile_stream_batch import _batch_timestamp, main, reconcile


def _write_inputs(tmp_path, events, batch_rows):
    events_path = tmp_path / "events.jsonl"
    events_path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
    batch_path = tmp_path / "batch.csv"
    fields = ["record_date", "workshop_code", "energy_code", "consumption", "unit",
              "unit_price", "cost"]
    if any("updated_at" in row for row in batch_rows):
        fields.append("updated_at")
    with batch_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(batch_rows)
    return events_path, batch_path


def _event(event_id, updated_at, *, op="UPSERT", consumption=12.5, cost=25):
    return {
        "event_id": event_id, "event_time": updated_at, "updated_at": updated_at, "op": op,
        "record_date": "2026-09-01", "workshop_code": "W04", "energy_code": "E01",
        "consumption": consumption, "unit": "kWh", "unit_price": 2, "cost": cost,
    }


def test_reconciliation_uses_latest_correction_and_ignores_identical_event_replay(tmp_path):
    events = [
        _event("old", "2026-09-01T01:00:00Z", consumption=10, cost=20),
        _event("new", "2026-09-02T01:00:00Z"),
        _event("new", "2026-09-02T01:00:00Z"),
    ]
    batch = [{"record_date": "2026-09-01", "workshop_code": "W04", "energy_code": "E01",
              "consumption": "12.50", "unit": "kWh", "unit_price": "2.0", "cost": "25.00"}]
    report = reconcile(*_write_inputs(tmp_path, events, batch))
    assert report["success"] is True
    assert report["counts"]["event_rows"] == 3
    assert report["counts"]["stream_business_keys"] == 1
    assert report["counts"]["value_mismatches"] == 0


def test_reconciliation_applies_delete_and_fails_on_batch_key_left_behind(tmp_path):
    events = [_event("old", "2026-09-01T01:00:00Z"),
              _event("deleted", "2026-09-03T01:00:00Z", op="DELETE", consumption=None, cost=None)]
    batch = [{"record_date": "2026-09-01", "workshop_code": "W04", "energy_code": "E01",
              "consumption": "12.5", "unit": "kWh", "unit_price": "2", "cost": "25"}]
    report = reconcile(*_write_inputs(tmp_path, events, batch))
    assert report["success"] is False
    assert report["counts"]["delete_events_applied"] == 1
    assert report["counts"]["only_in_batch"] == 1


def test_reconciliation_reports_invalid_rows_and_event_id_conflicts(tmp_path):
    events = [_event("same", "2026-09-01T01:00:00Z"),
              _event("same", "2026-09-02T01:00:00Z", consumption=14, cost=28),
              _event("bad", "2026-09-03T01:00:00Z", consumption=-1)]
    batch = [{"record_date": "2026-09-01", "workshop_code": "W04", "energy_code": "E01",
              "consumption": "12.5", "unit": "kWh", "unit_price": "2", "cost": "25"}]
    report = reconcile(*_write_inputs(tmp_path, events, batch))
    assert report["success"] is False
    assert report["counts"]["event_invalid_rows"] == 1
    assert report["counts"]["event_id_conflicts"] == 1


def test_reconciliation_rejects_conflicting_states_at_same_source_version(tmp_path):
    events = [_event("a", "2026-09-01T01:00:00Z", consumption=12.5, cost=25),
              _event("z", "2026-09-01T01:00:00Z", consumption=14, cost=28)]
    batch = [{"record_date": "2026-09-01", "workshop_code": "W04", "energy_code": "E01",
              "consumption": "14", "unit": "kWh", "unit_price": "2", "cost": "28"}]
    report = reconcile(*_write_inputs(tmp_path, events, batch))
    assert report["success"] is False
    assert report["counts"]["version_conflicts"] == 1
    assert report["counts"]["value_mismatches"] == 0
    assert report["samples"]["version_conflicts"][0]["reason"] == "same_version_different_state"


def test_reconciliation_accepts_same_version_same_state_with_new_event_id(tmp_path):
    events = [_event("a", "2026-09-01T01:00:00Z"),
              _event("b", "2026-09-01T01:00:00+00:00")]
    batch = [{"record_date": "2026-09-01", "workshop_code": "W04", "energy_code": "E01",
              "consumption": "12.5", "unit": "kWh", "unit_price": "2", "cost": "25"}]
    report = reconcile(*_write_inputs(tmp_path, events, batch))
    assert report["success"] is True
    assert report["counts"]["version_conflicts"] == 0


def test_explicit_batch_timezone_catches_stale_version_even_when_values_match(tmp_path):
    event = _event("one", "2026-09-01T01:00:00Z")
    batch = [{"record_date": "2026-09-01", "workshop_code": "W04", "energy_code": "E01",
              "consumption": "12.5", "unit": "kWh", "unit_price": "2", "cost": "25",
              "updated_at": "2026-09-01 09:00:00"}]
    paths = _write_inputs(tmp_path, [event], batch)
    assert reconcile(*paths, batch_timezone="Asia/Shanghai")["success"] is True

    batch[0]["updated_at"] = "2026-09-01 08:59:00"
    paths = _write_inputs(tmp_path, [event], batch)
    report = reconcile(*paths, batch_timezone="Asia/Shanghai")
    assert report["success"] is False
    assert report["counts"]["value_mismatches"] == 0
    assert report["counts"]["version_mismatches"] == 1
    assert report["samples"]["version_mismatches"][0]["stream_updated_at_utc"] == (
        "2026-09-01T01:00:00+00:00"
    )


def test_explicit_batch_timezone_requires_valid_updated_at(tmp_path):
    event = _event("one", "2026-09-01T01:00:00Z")
    batch = [{"record_date": "2026-09-01", "workshop_code": "W04", "energy_code": "E01",
              "consumption": "12.5", "unit": "kWh", "unit_price": "2", "cost": "25"}]
    report = reconcile(*_write_inputs(tmp_path, [event], batch),
                       batch_timezone="Asia/Shanghai")
    assert report["success"] is False
    assert report["counts"]["batch_invalid_rows"] == 1


def test_naive_batch_timestamp_rejects_ambiguous_or_nonexistent_dst_time():
    assert _batch_timestamp("2024-11-03 01:30:00", "America/New_York") is None
    assert _batch_timestamp("2024-03-10 02:30:00", "America/New_York") is None


def test_reconciliation_rejects_duplicate_batch_keys(tmp_path):
    events = [_event("one", "2026-09-01T01:00:00Z")]
    row = {"record_date": "2026-09-01", "workshop_code": "W04", "energy_code": "E01",
           "consumption": "12.5", "unit": "kWh", "unit_price": "2", "cost": "25"}
    report = reconcile(*_write_inputs(tmp_path, events, [row, row]))
    assert report["success"] is False
    assert report["counts"]["batch_duplicate_keys"] == 1


def test_reconciliation_quarantines_non_object_json_and_non_finite_values(tmp_path):
    events_path = tmp_path / "events.jsonl"
    events_path.write_text('[1,2]\n' + json.dumps(_event("nan", "2026-09-01T01:00:00Z",
                                                            consumption="NaN")) + "\n",
                           encoding="utf-8")
    batch_path = tmp_path / "batch.csv"
    batch_path.write_text(
        "record_date,workshop_code,energy_code,consumption,unit,unit_price,cost\n"
        "2026-09-01,W04,E01,12.5,kWh,2,25\n", encoding="utf-8"
    )
    report = reconcile(events_path, batch_path)
    assert report["success"] is False
    assert report["counts"]["event_invalid_rows"] == 2


def test_invalid_utf8_is_reported_without_losing_later_valid_events(tmp_path):
    event = _event("good", "2026-09-01T01:00:00Z")
    row = {"record_date": "2026-09-01", "workshop_code": "W04", "energy_code": "E01",
           "consumption": "12.5", "unit": "kWh", "unit_price": "2", "cost": "25"}
    events_path, batch_path = _write_inputs(tmp_path, [event], [row])
    invalid_line = b"\xff\xfe\n"
    invalid_json_line = b'{"event_id":\n'
    events_path.write_bytes(invalid_line + invalid_json_line + events_path.read_bytes())

    output = tmp_path / "reconciliation.json"
    assert main(["--events", str(events_path), "--batch", str(batch_path),
                 "--output", str(output)]) == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["success"] is False
    assert report["counts"]["event_rows"] == 1
    assert report["counts"]["event_invalid_rows"] == 2
    assert report["samples"]["invalid_events"][0] == {
        "line": 1, "byte_offset": 0,
        "raw_sha256": hashlib.sha256(invalid_line).hexdigest(),
        "reason": "invalid_utf8",
    }
    json_error = report["samples"]["invalid_events"][1]
    assert json_error["reason"] == "invalid_json"
    assert json_error["line"] == 2
    assert json_error["byte_offset"] == len(invalid_line)
    assert json_error["raw_sha256"] == hashlib.sha256(invalid_json_line).hexdigest()
