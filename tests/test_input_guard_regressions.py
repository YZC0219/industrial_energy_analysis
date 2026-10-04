"""Failures that previously hung jobs or silently corrupted input aggregates."""
import math
import json

import pandas as pd
import pytest

from ml.feature_pipeline import build_features
from ml.rolling_validation import rolling_splits
from ml.deep_benchmark import build_sequences
from ml.model_benchmark import NUMERIC_FEATURES
from streaming.alert_outbox import Outbox, work_one
from streaming.outbox_supervisor import supervise
from tools.check_energy_coverage import validate
from tools.reconcile_stream_batch import read_events


@pytest.mark.parametrize("option,value", [
    ("train_days", 0), ("test_days", 0), ("step_days", 0),
    ("step_days", -1), ("warmup_days", -1), ("train_days", 1.5),
])
def test_invalid_rolling_settings_fail_before_first_fold(option, value):
    with pytest.raises(ValueError):
        next(rolling_splits(pd.date_range("2024-01-01", periods=500), **{option: value}))


@pytest.mark.parametrize("column", ["record_date", "workshop_code", "energy_code"])
@pytest.mark.parametrize("value", [None, ""])
def test_coverage_cannot_ignore_invalid_energy_keys(column, value):
    valid = {"record_date": "2026-10-01", "workshop_code": "W01", "energy_code": "E01"}
    energy = pd.DataFrame([valid, {**valid, column: value}])
    production = pd.DataFrame([{k: valid[k] for k in ("record_date", "workshop_code")}])
    result = validate(energy, production, {"version": 1, "workshops": {"W01": ["E01"]}})
    assert result["success"] is False
    assert result["reason"] == "invalid_business_keys"


def inputs():
    energy = pd.DataFrame({"record_date": ["2026-10-01"], "workshop_code": ["W01"],
        "energy_code": ["E01"], "consumption": [100.], "cost": [10.],
        "avg_temperature": [20.], "record_status": ["正常"]})
    production = pd.DataFrame({"record_date": ["2026-10-01"], "workshop_code": ["W01"], "output_qty": [10.]})
    return energy, production


@pytest.mark.parametrize("table,column", [
    ("energy", "consumption"), ("energy", "cost"), ("production", "output_qty"),
])
@pytest.mark.parametrize("value", [math.nan, math.inf, -1.])
def test_features_reject_invalid_measurements(table, column, value):
    energy, production = inputs()
    (energy if table == "energy" else production).loc[0, column] = value
    with pytest.raises(ValueError):
        build_features(energy, production)


@pytest.mark.parametrize("table,column", [
    ("energy", "record_date"), ("energy", "workshop_code"), ("energy", "energy_code"),
    ("production", "record_date"), ("production", "workshop_code"),
])
def test_features_reject_missing_business_keys(table, column):
    energy, production = inputs()
    (energy if table == "energy" else production).loc[0, column] = None
    with pytest.raises(ValueError):
        build_features(energy, production)


@pytest.mark.parametrize("value", [math.nan, math.inf])
@pytest.mark.parametrize("operation", ["claim", "finish", "work"])
def test_nonfinite_retry_settings_cannot_strand_messages(tmp_path, value, operation):
    queue = Outbox(tmp_path / "finite.sqlite", "http://localhost", clock=lambda: 0)
    queue.enqueue({"power": 1})
    try:
        with pytest.raises(ValueError):
            if operation == "claim":
                queue.claim(lease_seconds=value)
            elif operation == "finish":
                row = queue.claim()
                queue.finish(row, "HTTP 503", base_delay=value)
            else:
                work_one(queue, queue.webhook_url, base_delay=value)
    finally:
        queue.db.close()


@pytest.mark.parametrize("field,value", [
    ("op", []), ("op", {}), ("event_id", []), ("event_id", 123),
    ("event_id", " "), ("unit", []), ("unit", {}), ("unit", " "),
])
def test_reconciliation_reports_malformed_types_instead_of_crashing(tmp_path, field, value):
    event = {"record_date": "2026-10-01", "workshop_code": "W01", "energy_code": "E01",
             "event_id": "event-1", "op": "UPSERT", "unit": "kWh", "consumption": 1,
             "unit_price": 2, "cost": 2, "updated_at": "2026-10-01T00:00:00Z",
             "event_time": "2026-10-01T00:00:00Z"}
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps({**event, field: value}) + "\n" + json.dumps(event) + "\n", encoding="utf-8")
    events, invalid = read_events(path)
    assert events == [event]
    assert len(invalid) == 1 and invalid[0]["line"] == 1


@pytest.mark.parametrize("mode", ["gap", "duplicate", "missing_workshop"])
def test_deep_sequences_cannot_treat_invalid_rows_as_calendar_history(mode):
    frame = pd.DataFrame({"record_date": pd.date_range("2026-10-01", periods=6),
                          "workshop_code": "W01", "tce": 1.,
                          **{column: 1. for column in NUMERIC_FEATURES}})
    if mode == "gap":
        frame = frame.drop(index=2)
    elif mode == "duplicate":
        frame = pd.concat([frame, frame.iloc[[2]]], ignore_index=True)
    else:
        frame.loc[0, "workshop_code"] = None
    with pytest.raises(ValueError):
        build_sequences(frame, sequence_days=3)


@pytest.mark.parametrize("value", [math.nan, math.inf])
def test_supervisor_rejects_nonfinite_delays_before_spawning(tmp_path, value):
    with pytest.raises(ValueError):
        supervise(["not-a-program"], tmp_path, base_delay=value)
