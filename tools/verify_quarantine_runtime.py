"""Real isolated Kafka poison quarantine, crash-before-commit and repair replay."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from streaming.alert_outbox import Outbox
from streaming.alert_quarantine import Quarantine


def main():
    from kafka import KafkaConsumer, KafkaProducer, TopicPartition
    from kafka.admin import KafkaAdminClient, NewTopic
    ident = uuid4().hex[:12]
    folder = ROOT / "output/extensions" / ("quarantine_" + ident)
    folder.mkdir(parents=True)
    topic, group = "energy-quarantine-test-" + ident, "quarantine-test-" + ident
    bootstrap, url = "localhost:29092", "http://127.0.0.1:8765/acceptance-only"
    queue_path, bad_path = folder / "queue.sqlite", folder / "quarantine.sqlite"
    namespace = bootstrap + ":" + group
    admin = KafkaAdminClient(bootstrap_servers=bootstrap)
    try:
        admin.create_topics([NewTopic(topic, 1, 1)])
    finally:
        admin.close()
    producer = KafkaProducer(bootstrap_servers=bootstrap, acks="all")
    records = [b"bad-json", b'{"power":100}', b"\xff", b'{"power":NaN}', b'{"power":200}', None, b'{"power":300}']
    try:
        for raw in records:
            producer.send(topic, partition=0, key=b"acceptance", value=raw).get(timeout=20)
    finally:
        producer.close()
    # Reproduce quarantine durable commit then abrupt exit before Kafka commit.
    code = "from kafka import KafkaConsumer; from streaming.alert_outbox import Outbox; from streaming.alert_quarantine import Quarantine; import sys,os; q=Outbox(sys.argv[1],sys.argv[3]); d=Quarantine(sys.argv[2],q.db.execute('SELECT fingerprint FROM route').fetchone()[0]); c=KafkaConsumer(sys.argv[4],bootstrap_servers='localhost:29092',group_id=sys.argv[5],enable_auto_commit=False,auto_offset_reset='earliest'); m=next(c); d.put('localhost:29092:'+sys.argv[5],m,'JSONDecodeError'); os._exit(87)"
    crash = subprocess.run([sys.executable, "-c", code, str(queue_path), str(bad_path), url, topic, group],
                           cwd=ROOT, capture_output=True, timeout=30)
    assert crash.returncode == 87, crash.stderr.decode(errors="replace")
    checker = KafkaConsumer(bootstrap_servers=bootstrap, group_id=group, enable_auto_commit=False)
    partition = TopicPartition(topic, 0)
    assert checker.committed(partition) is None
    log = (folder / "consumer.log").open("wb")
    worker = subprocess.Popen([sys.executable, "-m", "streaming.kafka_outbox", "--bootstrap-servers", bootstrap,
                               "--topic", topic, "--group-id", group, "--db", str(queue_path),
                               "--webhook-url", url, "--quarantine-db", str(bad_path)], cwd=ROOT, stdout=log, stderr=log)
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and checker.committed(partition) != 7:
            if worker.poll() is not None:
                raise RuntimeError("Consumer exited; inspect preserved consumer.log")
            time.sleep(.1)
        assert checker.committed(partition) == 7
    finally:
        worker.terminate()
        worker.wait(timeout=10)
        checker.close(autocommit=False)
        log.close()
    queue = Outbox(queue_path, url)
    rejected = Quarantine(bad_path, queue.db.execute("SELECT fingerprint FROM route").fetchone()[0])
    assert queue.stats() == {"pending": 3}
    rows = rejected.db.execute("SELECT * FROM rejected ORDER BY offset_no").fetchall()
    assert [r["offset_no"] for r in rows] == [0,2,3,5]
    assert all(r["raw"] == records[r["offset_no"]] for r in rows)
    repair = {"power": 150, "repair_reference": "operator-reviewed-test-fixture"}
    repair_id = rejected.replay(rows[0]["id"], repair, queue)
    assert rejected.replay(rows[0]["id"], repair, queue) == repair_id
    assert queue.stats() == {"pending": 4}
    audit = [dict(r) for r in rejected.db.execute("SELECT id,namespace,topic,partition_no,offset_no,error,state,repair_id FROM rejected ORDER BY offset_no")]
    rejected.db.close()
    queue.db.close()
    report = {"success": True, "topic": topic, "group_id": group, "published": 7,
              "committed_offset": 7, "quarantined": 4, "valid_alerts": 3, "after_repair_alerts": 4,
              "crash_before_offset_commit_recovered": True, "raw_bytes_preserved": True,
              "repair_replay_idempotent": True, "repair_requires_explicit_input": True,
              "source_hashes": {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
                  ("streaming/kafka_outbox.py", "streaming/alert_quarantine.py")},
              "evidence_directory": str(folder.relative_to(ROOT)),
              "notification_scope": "no notifications sent; queued only"}
    (folder / "quarantine_audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    (ROOT / "output/extensions/quarantine_runtime.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
