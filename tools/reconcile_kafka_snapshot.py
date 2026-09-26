"""Export a bounded Kafka offset snapshot, then reconcile it with a batch CSV.

This is a one-shot audit, not a CDC source or an automatic repair service.
The batch timezone is mandatory because the project CSV stores naive MySQL DATETIME.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from tools.reconcile_stream_batch import DEFAULT_REPORT, _fingerprint, reconcile

ROOT = Path(__file__).resolve().parents[1]


def export_bounded_snapshot(consumer, topic: str, path: Path,
                            topic_partition: Callable, *, timeout_seconds: int = 60) -> dict:
    """Freeze exclusive end offsets before reading, and refuse truncated history."""
    partitions = consumer.partitions_for_topic(topic)
    if not partitions:
        raise ValueError(f"Kafka topic has no readable partitions: {topic}")
    refs = [topic_partition(topic, number) for number in sorted(partitions)]
    consumer.assign(refs)
    starts = consumer.beginning_offsets(refs)
    ends = consumer.end_offsets(refs)
    captured_at = datetime.now(timezone.utc).isoformat()
    if any(starts[ref] != 0 for ref in refs):
        raise ValueError("Kafka retained history does not start at offset 0; refusing incomplete replay")
    for ref in refs:
        consumer.seek(ref, starts[ref])
    if path.exists():
        raise FileExistsError(f"Snapshot export already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="kafka-snapshot-", suffix=".part",
                                              dir=path.parent)
    os.close(descriptor)
    exported = {ref: 0 for ref in refs}
    deadline = time.monotonic() + timeout_seconds
    try:
        with open(temporary, "wb") as output:
            while any(consumer.position(ref) < ends[ref] for ref in refs):
                if time.monotonic() >= deadline:
                    raise TimeoutError("Kafka snapshot export did not reach captured end offsets")
                for ref, records in consumer.poll(timeout_ms=1000, max_records=1000).items():
                    for record in records:
                        if record.offset >= ends[ref]:
                            continue
                        value = record.value
                        if value is None or b"\n" in value or b"\r" in value:
                            raise ValueError(
                                f"Kafka value cannot be represented as one JSONL line: "
                                f"partition={ref.partition} offset={record.offset}"
                            )
                        output.write(value + b"\n")
                        exported[ref] += 1
        os.link(temporary, path)  # Atomic create; never overwrite an earlier evidence export.
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {
        "topic": topic,
        "captured_at": captured_at,
        "partitions": [
            {"partition": ref.partition, "start_offset": starts[ref],
             "end_offset_exclusive": ends[ref], "exported_records": exported[ref]}
            for ref in refs
        ],
        "events_export": str(path),
        "events_sha256": _fingerprint(path),
        "history_note": (
            "Offset 0 and a bounded end prove only retained Kafka history was read. "
            "They do not prove that this topic contains every source-database change."
        ),
    }


def capture_and_reconcile(batch: Path, export_path: Path, *, topic: str,
                          bootstrap: str, batch_timezone: str,
                          required_schema_version: int = 1,
                          timeout_seconds: int = 60) -> dict:
    from kafka import KafkaConsumer, TopicPartition

    batch_sha_before = _fingerprint(batch)
    consumer = KafkaConsumer(bootstrap_servers=[bootstrap], enable_auto_commit=False,
                             group_id=None, value_deserializer=None)
    try:
        snapshot = export_bounded_snapshot(consumer, topic, export_path, TopicPartition,
                                           timeout_seconds=timeout_seconds)
    finally:
        consumer.close()
    report = reconcile(export_path, batch, batch_timezone=batch_timezone,
                       required_schema_version=required_schema_version)
    report["kafka_snapshot"] = snapshot
    if report["inputs"]["batch_sha256"] != batch_sha_before:
        report["success"] = False
        report["batch_changed_during_export"] = True
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--batch-timezone", required=True,
                        help="IANA zone for naive MySQL updated_at, e.g. Asia/Shanghai")
    parser.add_argument("--topic", default="energy-events")
    parser.add_argument("--required-schema-version", type=int, default=1,
                        help="reject Kafka events without this exact integer version")
    parser.add_argument("--bootstrap", default="127.0.0.1:29092")
    parser.add_argument("--events-export", type=Path)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--timeout-seconds", type=int, default=60)
    args = parser.parse_args(argv)
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    export_path = args.events_export or (
        ROOT / "output" / f"energy_events_snapshot_{uuid.uuid4().hex[:12]}.jsonl"
    )
    if export_path.resolve() == args.report.resolve():
        parser.error("--events-export and --report must be different paths")
    try:
        report = capture_and_reconcile(args.batch, export_path, topic=args.topic,
                                       bootstrap=args.bootstrap,
                                       batch_timezone=args.batch_timezone,
                                       required_schema_version=args.required_schema_version,
                                       timeout_seconds=args.timeout_seconds)
    except Exception as exc:
        report = {"success": False, "error": f"{type(exc).__name__}: {exc}",
                  "events_export": str(export_path), "batch_csv": str(args.batch)}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(f"KAFKA_SNAPSHOT_RECONCILIATION {'PASS' if report['success'] else 'FAIL'} "
          f"report={args.report}")
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
