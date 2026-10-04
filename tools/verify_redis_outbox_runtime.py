"""Real Redis/Webhook partial failure and outage recovery, localhost only."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import socket
from pathlib import Path
import subprocess
import sys
import threading
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dependencies/redisclient"))
import redis
from streaming.alert_outbox import Outbox, work_one
from streaming.dispatch import prepare


def docker(*args):
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True,
                          encoding="utf-8", timeout=30).stdout.strip()


def wait_for(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise TimeoutError("Acceptance condition not reached")


def main():
    ident = uuid4().hex[:12]
    folder = ROOT / "output/extensions" / ("redis_outbox_" + ident)
    folder.mkdir(parents=True)
    data = folder / "redis"
    data.mkdir()
    name = "energy-outbox-redis-" + ident
    attempts, received = [], {}
    fail_once = set()
    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            alert = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            key = self.headers["Idempotency-Key"]
            status = 200 if key in fail_once else 503
            fail_once.add(key)
            attempts.append({"id": key, "status": status})
            if status == 200:
                received.setdefault(key, alert)
            self.send_response(status)
            self.end_headers()
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    webhook = f"http://127.0.0.1:{server.server_port}/alerts"
    worker = None
    client = None
    started = False
    log = (folder / "worker.log").open("wb")
    path = folder / "queue.sqlite"
    try:
        # Docker's randomly allocated host port can change on container restart.
        # Reserve a free candidate and publish it explicitly so the bound route stays stable.
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            published_port = probe.getsockname()[1]
        docker("run", "-d", "--pull", "never", "--name", name, "-p", f"127.0.0.1:{published_port}:6379",
               "--mount", f"type=bind,source={data},target=/data", "redis:7.4-alpine",
               "redis-server", "--appendonly", "yes", "--appendfsync", "always")
        started = True
        port = int(docker("port", name, "6379/tcp").rsplit(":", 1)[1])
        redis_url = f"redis://127.0.0.1:{port}/0"
        client = redis.Redis.from_url(redis_url, decode_responses=True, socket_timeout=1,
                                      retry=redis.retry.Retry(redis.backoff.NoBackoff(), 0))
        def ready():
            try:
                return client.ping()
            except redis.exceptions.RedisError:
                return False
        wait_for(ready)
        alerts_path = ROOT / "output/extensions/f2b3bd086c95/alerts.json"
        alerts = json.loads(alerts_path.read_text(encoding="utf-8"))
        queue = Outbox(path, webhook, redis_url=redis_url)
        for alert in alerts:
            queue.enqueue(alert)
        queue.db.close()
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "dependencies/redisclient") + os.pathsep + str(ROOT)
        command = [sys.executable, "-m", "streaming.alert_outbox", "work", "--db", str(path),
                   "--webhook-url", webhook, "--redis-url", redis_url, "--base-delay", "2"]
        def start_worker():
            return subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=log)
        def stop_worker(process):
            process.terminate()
            process.wait(timeout=10)
        worker = start_worker()
        wait_for(lambda: bool(attempts))
        stop_worker(worker)
        worker = None
        partial_key = attempts[0]["id"]
        assert attempts[0]["status"] == 503
        assert client.get("energy:alert:" + partial_key) is not None
        assert partial_key not in received
        # Preserve pending alerts and restart a new process while Redis is offline.
        docker("stop", name)
        queue = Outbox(path, webhook, redis_url=redis_url)
        initial_errors = queue.db.execute("SELECT count(*) FROM audit WHERE detail LIKE 'Redis %'").fetchone()[0]
        def outage_recorded():
            count = queue.db.execute("SELECT count(*) FROM audit WHERE detail LIKE 'Redis %'").fetchone()[0]
            return count > initial_errors
        worker = start_worker()
        wait_for(outage_recorded)
        stop_worker(worker)
        worker = None
        pending_after_outage = queue.stats().get("pending", 0)
        assert pending_after_outage > 0
        attempts_before_recovery = len(attempts)
        docker("start", name)
        wait_for(ready)
        worker = start_worker()
        # Abruptly terminated workers may leave a 60-second lease. Preserve and
        # recover those records naturally; do not modify the database to speed up the test.
        wait_for(lambda: queue.stats() == {"sent": 20}, timeout=90)
        stop_worker(worker)
        worker = None
        assert received == {prepare(alert)[0]: alert for alert in alerts}
        cached = {key: client.get("energy:alert:" + key) for key in received}
        assert all(json.loads(cached[key]) == received[key] for key in received)
        ttls = [client.ttl("energy:alert:" + key) for key in received]
        assert min(ttls) > 86300 and max(ttls) <= 86400
        audit = [dict(row) for row in queue.db.execute("SELECT * FROM audit ORDER BY seq")]
        queue.db.close()
        # A bad credential is a permanent error; never confirm or send HTTP.
        auth_queue = Outbox(folder / "auth.sqlite", webhook,
                            redis_url=f"redis://:invalid@127.0.0.1:{port}/0")
        auth_queue.enqueue({"case": "auth_failure"})
        before_auth_http = len(attempts)
        work_one(auth_queue, webhook)
        assert auth_queue.stats() == {"dead": 1}
        auth_row = dict(auth_queue.db.execute("SELECT state,attempts,error FROM messages").fetchone())
        assert auth_row["attempts"] == 1 and auth_row["error"] == "Redis AuthenticationError"
        assert len(attempts) == before_auth_http
        auth_queue.redis_client.close()
        auth_queue.db.close()
        report = {"success": True, "run_id": ident, "container": name,
                  "source_hashes": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in
                                    ("streaming/alert_outbox.py", "streaming/dispatch.py")},
                  "source_alerts_sha256": hashlib.sha256(alerts_path.read_bytes()).hexdigest(),
                  "redis_cached_webhook_503_observed": True, "redis_outage_durable_retry_observed": True,
                  "process_restart_recovered": True, "pending_after_outage": pending_after_outage,
                  "http_attempts_before_recovery": attempts_before_recovery, "http_attempts": len(attempts),
                  "unique_received": len(received), "redis_keys": len(cached),
                  "ttl_min_seconds": min(ttls), "ttl_max_seconds": max(ttls),
                  "final_states": {"sent": 20}, "all_payloads_match": True,
                  "auth_failure_dead_without_http": True,
                  "redis_version": client.info("server")["redis_version"],
                  "evidence_directory": str(folder.relative_to(ROOT))}
        for filename, content in (("audit.json", audit), ("http_attempts.json", attempts),
                                  ("received.json", received), ("auth_failure.json", auth_row)):
            (folder / filename).write_text(json.dumps(content, indent=2), encoding="utf-8")
        (ROOT / "output/extensions/redis_outbox_runtime.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
    finally:
        if worker is not None and worker.poll() is None:
            worker.terminate()
            worker.wait(timeout=10)
        if client is not None:
            client.close()
        server.shutdown()
        log.close()
        if started:
            docker("stop", name)


if __name__ == "__main__":
    main()
