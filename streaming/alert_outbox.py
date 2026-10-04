"""Local durable, at-least-once Webhook delivery. Receivers must deduplicate IDs.

Keep the SQLite file on a local disk, not a shared/network filesystem.
"""
import argparse
import hashlib
import json
import math
import sqlite3
import time
import uuid
from urllib.error import HTTPError, URLError

from streaming.dispatch import deliver, prepare


class Outbox:
    def __init__(self, path, webhook_url, clock=time.time, *, redis_url=None):
        from pathlib import Path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.webhook_url = webhook_url
        self.redis_url = redis_url
        self.redis_client = None
        if not webhook_url.startswith(("http://", "https://")):
            raise ValueError("Webhook URL must use HTTP or HTTPS")
        if redis_url is not None and not redis_url.startswith(("redis://", "rediss://")):
            raise ValueError("Redis URL must use redis or rediss")
        self.db = sqlite3.connect(path, timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS route (fingerprint TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS messages (
              id TEXT PRIMARY KEY, payload TEXT NOT NULL, state TEXT NOT NULL,
              attempts INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL,
              lease REAL, token TEXT, error TEXT);
            CREATE TABLE IF NOT EXISTS audit (
              seq INTEGER PRIMARY KEY, id TEXT, at REAL, event TEXT, detail TEXT);
        """)
        route = webhook_url if redis_url is None else json.dumps([webhook_url, redis_url])
        fingerprint = hashlib.sha256(route.encode()).hexdigest()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT fingerprint FROM route").fetchone()
            if row and row[0] != fingerprint:
                raise ValueError("Queue belongs to a different Webhook/Redis destination")
            self.db.execute("INSERT OR IGNORE INTO route VALUES (?)", (fingerprint,))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            self.db.close()
            raise

    def event(self, ident, event, detail=""):
        self.db.execute("INSERT INTO audit(id,at,event,detail) VALUES (?,?,?,?)",
                        (ident, self.clock(), event, detail))

    def enqueue(self, alert):
        if not isinstance(alert, dict) or not alert:
            raise ValueError("Alert must be a nonempty JSON object")
        ident, payload = prepare(alert)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            inserted = self.db.execute(
                "INSERT OR IGNORE INTO messages(id,payload,state,due) VALUES (?,?,'pending',?)",
                (ident, payload, self.clock())).rowcount
            if inserted:
                self.event(ident, "enqueued")
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return ident

    def claim(self, lease_seconds=60, max_attempts=5):
        if not math.isfinite(lease_seconds) or lease_seconds <= 0 or max_attempts < 1:
            raise ValueError("Lease and retry limit must be positive")
        now = self.clock()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            expired = self.db.execute("SELECT * FROM messages WHERE state='inflight' AND lease<=?", (now,)).fetchall()
            for row in expired:
                state = "dead" if row["attempts"] >= max_attempts else "pending"
                self.db.execute("UPDATE messages SET state=?,token=NULL,lease=NULL WHERE id=?", (state, row["id"]))
                self.event(row["id"], "lease_expired", state)
            exhausted = self.db.execute(
                "SELECT id FROM messages WHERE state='pending' AND attempts>=?",
                (max_attempts,)).fetchall()
            for row in exhausted:
                self.db.execute("UPDATE messages SET state='dead' WHERE id=?", (row["id"],))
                self.event(row["id"], "retry_limit_reached")
            row = self.db.execute("SELECT * FROM messages WHERE state='pending' AND due<=? ORDER BY due,id LIMIT 1", (now,)).fetchone()
            if row:
                token = uuid.uuid4().hex
                self.db.execute("UPDATE messages SET state='inflight',attempts=attempts+1,token=?,lease=? WHERE id=?",
                                (token, now + lease_seconds, row["id"]))
                self.event(row["id"], "claimed")
                row = dict(self.db.execute("SELECT * FROM messages WHERE id=?", (row["id"],)).fetchone())
            self.db.execute("COMMIT")
            return row
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def finish(self, row, error=None, retryable=True, max_attempts=5, base_delay=2, max_delay=300):
        if not all(math.isfinite(v) and v >= 0 for v in (base_delay, max_delay)) or max_attempts < 1:
            raise ValueError("Invalid retry policy")
        state = "sent" if error is None else (
            "pending" if retryable and row["attempts"] < max_attempts else "dead")
        delay = min(max_delay, base_delay * 2 ** min(row["attempts"] - 1, 30))
        self.db.execute("BEGIN IMMEDIATE")
        try:
            changed = self.db.execute(
                "UPDATE messages SET state=?,due=?,error=?,token=NULL,lease=NULL WHERE id=? AND state='inflight' AND token=?",
                (state, self.clock() + delay, error, row["id"], row["token"])).rowcount
            if changed:
                self.event(row["id"], state, error or "")
            self.db.execute("COMMIT")
            return bool(changed)
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def requeue(self, ident):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            changed = self.db.execute("UPDATE messages SET state='pending',attempts=0,due=?,error=NULL WHERE id=? AND state='dead'", (self.clock(), ident)).rowcount
            if changed:
                self.event(ident, "manual_requeue")
            self.db.execute("COMMIT")
            return bool(changed)
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def stats(self):
        return dict(self.db.execute("SELECT state,count(*) FROM messages GROUP BY state").fetchall())


def work_one(queue, webhook_url, max_attempts=5, base_delay=2, max_delay=300):
    if webhook_url != queue.webhook_url:
        raise ValueError("Worker destination differs from queue destination")
    if max_attempts < 1 or not all(math.isfinite(v) and v >= 0 for v in (base_delay, max_delay)):
        raise ValueError("Invalid retry policy")
    redis_errors = ()
    redis_permanent = ()
    if queue.redis_url is not None:
        import redis
        redis_errors = (redis.exceptions.RedisError,)
        redis_permanent = (redis.exceptions.AuthenticationError, redis.exceptions.ResponseError)
        if queue.redis_client is None:
            queue.redis_client = redis.Redis.from_url(queue.redis_url, socket_timeout=10,
                                                     socket_connect_timeout=5,
                                                     retry_on_timeout=False,
                                                     retry=redis.retry.Retry(redis.backoff.NoBackoff(), 0))
    row = queue.claim(max_attempts=max_attempts)
    if row is None:
        return False
    try:
        deliver(json.loads(row["payload"]), redis_client=queue.redis_client, webhook_url=webhook_url)
    except redis_errors as exc:
        queue.finish(row, "Redis " + type(exc).__name__, not isinstance(exc, redis_permanent),
                     max_attempts, base_delay, max_delay)
    except HTTPError as exc:
        queue.finish(row, f"HTTP {exc.code}", exc.code in (408, 429) or exc.code >= 500,
                     max_attempts, base_delay, max_delay)
    except (URLError, TimeoutError, OSError) as exc:
        queue.finish(row, type(exc).__name__, True, max_attempts, base_delay, max_delay)
    else:
        queue.finish(row)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["enqueue", "work", "inspect", "requeue"])
    parser.add_argument("--db", required=True)
    parser.add_argument("--webhook-url", required=True)
    parser.add_argument("--redis-url", help="Optional cache destination, permanently bound to this queue")
    parser.add_argument("--input")
    parser.add_argument("--id")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--max-attempts", type=int, default=5)
    parser.add_argument("--base-delay", type=float, default=2)
    args = parser.parse_args()
    if args.max_attempts < 1 or not math.isfinite(args.base_delay) or args.base_delay < 0:
        parser.error("Retry limit must be positive and delay nonnegative")
    queue = Outbox(args.db, args.webhook_url, redis_url=args.redis_url)
    try:
        if args.action == "enqueue":
            if not args.input:
                parser.error("enqueue requires --input")
            with open(args.input, encoding="utf-8") as source:
                for line in source:
                    if line.strip():
                        queue.enqueue(json.loads(line))
        elif args.action == "requeue":
            if not args.id or not queue.requeue(args.id):
                parser.error("A dead alert ID is required")
        elif args.action == "work":
            while True:
                worked = work_one(queue, args.webhook_url, args.max_attempts, args.base_delay)
                if args.once:
                    break
                if not worked:
                    time.sleep(0.5)
        print(json.dumps(queue.stats()))
    finally:
        if queue.redis_client is not None:
            queue.redis_client.close()
        queue.db.close()


if __name__ == "__main__":
    main()
