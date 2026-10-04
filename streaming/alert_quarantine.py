"""Durable poison-message quarantine and explicit, idempotent repair replay."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from streaming.alert_outbox import Outbox
from streaming.dispatch import prepare


class Quarantine:
    def __init__(self, path, queue_fingerprint):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.binding = queue_fingerprint
        self.db = sqlite3.connect(path, timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS binding(fingerprint TEXT PRIMARY KEY);
          CREATE TABLE IF NOT EXISTS rejected (
            id TEXT PRIMARY KEY, namespace TEXT, topic TEXT, partition_no INTEGER,
            offset_no INTEGER, raw BLOB, error TEXT, created REAL,
            state TEXT NOT NULL, repair_id TEXT, replayed REAL, repair_payload TEXT);
        """)
        # Additive migration preserves previously quarantined records.
        if "repair_payload" not in [r[1] for r in self.db.execute("PRAGMA table_info(rejected)")]:
            self.db.execute("ALTER TABLE rejected ADD COLUMN repair_payload TEXT")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            binding = self.db.execute("SELECT fingerprint FROM binding").fetchone()
            if binding and binding[0] != queue_fingerprint:
                raise ValueError("Quarantine belongs to a different outbox route")
            self.db.execute("INSERT OR IGNORE INTO binding VALUES (?)", (queue_fingerprint,))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            self.db.close()
            raise

    def put(self, namespace, message, error):
        locator = json.dumps([namespace, message.topic, message.partition, message.offset])
        ident = hashlib.sha256(locator.encode()).hexdigest()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            previous = self.db.execute("SELECT raw FROM rejected WHERE id=?", (ident,)).fetchone()
            if previous and previous[0] != message.value:
                raise ValueError("Kafka locator has conflicting raw bytes")
            self.db.execute("INSERT OR IGNORE INTO rejected(id,namespace,topic,partition_no,offset_no,raw,error,created,state) VALUES (?,?,?,?,?,?,?,?, 'quarantined')",
                            (ident, namespace, message.topic, message.partition, message.offset,
                             message.value, error, time.time()))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return ident

    def replay(self, ident, repaired, queue):
        if Path(queue.db.execute("PRAGMA database_list").fetchone()[2]).resolve() == Path(self.db.execute("PRAGMA database_list").fetchone()[2]).resolve():
            raise ValueError("Quarantine and outbox must use different database files")
        if queue.db.execute("SELECT fingerprint FROM route").fetchone()[0] != self.binding:
            raise ValueError("Repair target differs from original outbox route")
        if not isinstance(repaired, dict) or not repaired:
            raise ValueError("Repair must be a nonempty alert object")
        repair_id, payload = prepare(repaired)
        # Persist repair intent before enqueue, so a crash cannot allow a different
        # correction to replace a repair already written to the outbox.
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT * FROM rejected WHERE id=?", (ident,)).fetchone()
            if row is None:
                raise KeyError(ident)
            if row["repair_id"] and row["repair_id"] != repair_id:
                raise ValueError("Record was already replayed with a different repair")
            self.db.execute("UPDATE rejected SET repair_id=?,repair_payload=?,state=CASE WHEN state='replayed' THEN state ELSE 'replay_pending' END WHERE id=?",
                            (repair_id, payload, ident))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        queue.enqueue(repaired)
        self.db.execute("UPDATE rejected SET state='replayed',replayed=COALESCE(replayed,?) WHERE id=? AND repair_id=?",
                        (time.time(), ident, repair_id))
        return repair_id


def inspect_quarantine(path, limit=50, include_raw=False):
    if not 1 <= limit <= 1000:
        raise ValueError("Limit must be between 1 and 1000")
    path = Path(path).resolve(strict=True)
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        rows = []
        for row in db.execute("SELECT * FROM rejected ORDER BY created,id LIMIT ?", (limit,)):
            item = {key: row[key] for key in ("id", "topic", "partition_no", "offset_no", "error", "state", "repair_id")}
            item["is_tombstone"] = row["raw"] is None
            if include_raw:
                item["raw_base64"] = None if row["raw"] is None else base64.b64encode(row["raw"]).decode()
            rows.append(item)
        return {"counts": dict(db.execute("SELECT state,count(*) FROM rejected GROUP BY state").fetchall()), "records": rows}
    finally:
        db.close()


def persist_or_quarantine(queue, quarantine, namespace, consumer, message, partition_factory, offset_factory):
    try:
        if message.value is None:
            raise ValueError("Kafka tombstone is not an alert object")
        alert = json.loads(message.value.decode("utf-8"))
        if not isinstance(alert, dict) or not alert:
            raise ValueError("Invalid alert object")
        prepare(alert)  # Reject NaN/infinity before touching the durable outbox.
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        ident = quarantine.put(namespace, message, type(exc).__name__)
        outcome = "quarantined"
    else:
        ident = queue.enqueue(alert)
        outcome = "enqueued"
    consumer.commit({partition_factory(message.topic, message.partition):
                     offset_factory(message.offset + 1, "", -1)})
    return outcome, ident


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["list", "replay"])
    parser.add_argument("--quarantine-db", required=True)
    parser.add_argument("--queue-db")
    parser.add_argument("--webhook-url")
    parser.add_argument("--redis-url")
    parser.add_argument("--id")
    parser.add_argument("--repair", type=Path, help="Explicit corrected JSON object, never inferred")
    parser.add_argument("--include-raw", action="store_true")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    if args.action == "list":
        print(json.dumps(inspect_quarantine(args.quarantine_db, args.limit, args.include_raw), indent=2))
        return
    if not all((args.queue_db, args.webhook_url, args.id, args.repair)):
        parser.error("replay requires queue-db, webhook-url, id and repair")
    queue = Outbox(args.queue_db, args.webhook_url, redis_url=args.redis_url)
    quarantine = Quarantine(args.quarantine_db, queue.db.execute("SELECT fingerprint FROM route").fetchone()[0])
    try:
        print(json.dumps({"alert_id": quarantine.replay(args.id, json.loads(args.repair.read_text(encoding="utf-8")), queue)}))
    finally:
        quarantine.db.close()
        queue.db.close()


if __name__ == "__main__":
    main()
