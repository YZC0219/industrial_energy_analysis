"""Isolated real Kafka crash/replay and poison-message acceptance."""
import argparse
import hashlib
import json
import os
import socket
from pathlib import Path
import subprocess
import sys
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from streaming.alert_outbox import Outbox, work_one
from streaming.dispatch import prepare
from streaming.kafka_outbox import persist_and_commit


def child(args):
    from kafka import KafkaConsumer, TopicPartition
    from kafka.structs import OffsetAndMetadata
    queue = Outbox(args.db, args.url)
    consumer = KafkaConsumer(args.topic, bootstrap_servers=args.bootstrap, group_id=args.group,
                             enable_auto_commit=False, auto_offset_reset="earliest", max_poll_records=1)
    try:
        for message in consumer:
            if args.mode == "crash":
                queue.enqueue(json.loads(message.value))
                os._exit(87)  # SQLite committed; Kafka offset deliberately not committed.
            persist_and_commit(queue, consumer, message, TopicPartition, OffsetAndMetadata)
    finally:
        consumer.close(autocommit=False)
        queue.db.close()


def verify(args):
    # Fail before creating any topics when the local Docker/Kafka runtime is down.
    endpoint = args.bootstrap.split(",")[0]
    host, port = endpoint.rsplit(":", 1)
    try:
        with socket.create_connection((host, int(port)), timeout=3):
            pass
    except OSError as exc:
        raise SystemExit(f"Kafka unavailable at {endpoint}; restore Docker/Kafka before acceptance. No test topics were created.") from exc
    from kafka import KafkaConsumer, KafkaProducer, TopicPartition
    from kafka.admin import KafkaAdminClient, NewTopic
    ident = uuid4().hex[:12]
    topic, group = "energy-outbox-test-" + ident, "outbox-test-" + ident
    folder = ROOT / "output/extensions" / ("kafka_outbox_" + ident)
    folder.mkdir(parents=True)
    path = folder / "queue.sqlite"
    received = {}
    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.setdefault(self.headers["Idempotency-Key"], payload)
            self.send_response(200)
            self.end_headers()
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/acceptance-only"
    # Archived outputs from the already validated real Flink job, not new synthetic alerts.
    alerts_path = ROOT / "output/extensions/f2b3bd086c95/alerts.json"
    alerts = json.loads(alerts_path.read_text(encoding="utf-8"))
    assert len(alerts) == 20
    admin = KafkaAdminClient(bootstrap_servers=args.bootstrap)
    try:
        admin.create_topics([NewTopic(topic, 1, 1)])
    finally:
        admin.close()
    producer = KafkaProducer(bootstrap_servers=args.bootstrap, acks="all")
    records = [json.dumps(alert).encode() for alert in alerts + [alerts[0]]] + [b"not-json"]
    try:
        for value in records:
            producer.send(topic, partition=0, value=value).get(timeout=20)
    finally:
        producer.close()
    command = [sys.executable, str(Path(__file__).resolve()), "--bootstrap", args.bootstrap,
               "--topic", topic, "--group", group, "--db", str(path), "--url", url]
    crash = subprocess.run(command + ["--mode", "crash"], cwd=ROOT, capture_output=True, timeout=40)
    assert crash.returncode == 87, crash.stderr.decode(errors="replace")
    checker = KafkaConsumer(bootstrap_servers=args.bootstrap, group_id=group, enable_auto_commit=False)
    partition = TopicPartition(topic, 0)
    try:
        before = checker.committed(partition)
        assert before is None, before
        queue = Outbox(path, url)
        assert queue.stats() == {"pending": 1}
        queue.db.close()
        replay = subprocess.run(command + ["--mode", "consume"], cwd=ROOT, capture_output=True, timeout=40)
        (folder / "consumer_stderr.log").write_bytes(replay.stderr)
        assert replay.returncode != 0 and b"JSONDecodeError" in replay.stderr
        after = checker.committed(partition)
        assert after == 21, after
        # Another fresh process must hit the same poison offset, leaving state unchanged.
        restarted = subprocess.run(command + ["--mode", "consume"], cwd=ROOT, capture_output=True, timeout=40)
        assert restarted.returncode != 0 and b"JSONDecodeError" in restarted.stderr
        assert checker.committed(partition) == after
        queue = Outbox(path, url)
        assert queue.stats() == {"pending": 20}
        audit = [dict(row) for row in queue.db.execute("SELECT * FROM audit ORDER BY seq")]
        assert len(audit) == 20
        while work_one(queue, url):
            pass
        assert queue.stats() == {"sent": 20}
        assert received == {prepare(alert)[0]: alert for alert in alerts}
        audit = [dict(row) for row in queue.db.execute("SELECT * FROM audit ORDER BY seq")]
        queue.db.close()
    finally:
        checker.close(autocommit=False)
        server.shutdown()
    report = {"success": True, "topic": topic, "group_id": group, "bootstrap": args.bootstrap,
              "records_published": 22, "unique_durable_alerts": 20,
              "offset_before_restart": before, "offset_after_replay": after,
              "crash_after_persist_before_commit_recovered": True,
              "duplicate_replay_deduplicated": True, "poison_offset": 21,
              "poison_restarts_blocked_without_commit": True,
              "webhook_unique_received": len(received), "final_states": {"sent": 20},
              "all_flink_alert_payloads_match": True,
              "source_alerts_sha256": hashlib.sha256(alerts_path.read_bytes()).hexdigest(),
              "source_hashes": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
                                ("streaming/kafka_outbox.py", "streaming/alert_outbox.py", "streaming/dispatch.py")},
              "evidence_directory": str(folder.relative_to(ROOT))}
    (folder / "audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    (folder / "received.json").write_text(json.dumps(received, indent=2), encoding="utf-8")
    (ROOT / "output/extensions/kafka_outbox_runtime.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap", default="localhost:29092")
    parser.add_argument("--mode", choices=["verify", "crash", "consume"], default="verify")
    for option in ("topic", "group", "db", "url"):
        parser.add_argument("--" + option)
    args = parser.parse_args()
    verify(args) if args.mode == "verify" else child(args)
