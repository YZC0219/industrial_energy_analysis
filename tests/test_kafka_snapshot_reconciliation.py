"""Offline guards for bounded Kafka export; no broker or Docker is needed."""
from collections import namedtuple
import json
from pathlib import Path

import pytest

from tools.reconcile_kafka_snapshot import export_bounded_snapshot
from tools.reconcile_stream_batch import _fingerprint

TopicRef = namedtuple("TopicRef", "topic partition")
Record = namedtuple("Record", "offset value")


class FakeConsumer:
    def __init__(self, records, *, start=0, end=2):
        self.records = records
        self.start = start
        self.end = end
        self.positions = {}
        self.polled = False

    def partitions_for_topic(self, _topic):
        return {0}

    def assign(self, refs):
        self.refs = refs

    def beginning_offsets(self, refs):
        return {ref: self.start for ref in refs}

    def end_offsets(self, refs):
        return {ref: self.end for ref in refs}

    def seek(self, ref, offset):
        self.positions[ref] = offset

    def position(self, ref):
        return self.positions[ref]

    def poll(self, **_kwargs):
        if self.polled:
            return {}
        self.polled = True
        ref = self.refs[0]
        self.positions[ref] = self.end
        return {ref: self.records}


def test_export_stops_at_captured_exclusive_offset_and_never_overwrites(tmp_path):
    path = tmp_path / "events.jsonl"
    consumer = FakeConsumer([
        Record(0, b'{"event_id":"first"}'),
        Record(1, b'{"event_id":"second"}'),
        Record(2, b'{"event_id":"after-fence"}'),
    ])
    snapshot = export_bounded_snapshot(consumer, "energy-events", path, TopicRef)
    assert path.read_bytes() == b'{"event_id":"first"}\n{"event_id":"second"}\n'
    assert snapshot["partitions"] == [{"partition": 0, "start_offset": 0,
                                       "end_offset_exclusive": 2, "exported_records": 2}]
    with pytest.raises(FileExistsError):
        export_bounded_snapshot(FakeConsumer([]), "energy-events", path, TopicRef)


def test_export_refuses_retention_gap_and_multiline_value(tmp_path):
    path = tmp_path / "events.jsonl"
    with pytest.raises(ValueError, match="offset 0"):
        export_bounded_snapshot(FakeConsumer([], start=1), "energy-events", path, TopicRef)
    assert not path.exists()

    consumer = FakeConsumer([Record(0, b'{"event_id":"bad\nline"}')], end=1)
    with pytest.raises(ValueError, match="one JSONL line"):
        export_bounded_snapshot(consumer, "energy-events", path, TopicRef)
    assert not path.exists()


def test_committed_smoke_evidence_reconciles_with_its_exact_inputs():
    root = Path(__file__).resolve().parents[1]
    report = json.loads((root / "output" / "kafka_snapshot_smoke_20260926.json")
                        .read_text(encoding="utf-8"))
    events = root / "output" / "kafka_snapshot_smoke_20260926.jsonl"
    batch = root / "tests" / "fixtures" / "kafka_snapshot_smoke_batch.csv"
    assert report["success"] is True
    assert report["kafka_snapshot"]["events_sha256"] == _fingerprint(events)
    assert report["inputs"]["batch_sha256"] == _fingerprint(batch)
    assert report["inputs"]["batch_timezone"] == "Asia/Shanghai"
    assert report["kafka_snapshot"]["partitions"] == [
        {"partition": 0, "start_offset": 0, "end_offset_exclusive": 1,
         "exported_records": 1}
    ]
    assert report["counts"]["version_mismatches"] == 0


def test_schema_v1_smoke_evidence_matches_flink_event_contract():
    root = Path(__file__).resolve().parents[1]
    report = json.loads((root / "output/kafka_snapshot_v1_20260926.json")
                        .read_text(encoding="utf-8"))
    events = root / "output/kafka_snapshot_v1_20260926.jsonl"
    batch = root / "tests/fixtures/kafka_snapshot_smoke_batch.csv"
    payload = json.loads(events.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert report["success"] is True
    assert report["inputs"]["required_schema_version"] == 1
    assert report["kafka_snapshot"]["events_sha256"] == _fingerprint(events)
    assert report["inputs"]["batch_sha256"] == _fingerprint(batch)
    assert report["kafka_snapshot"]["partitions"] == [
        {"partition": 0, "start_offset": 0, "end_offset_exclusive": 1,
         "exported_records": 1}
    ]
