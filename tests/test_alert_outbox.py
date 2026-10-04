import pytest
import json
from pathlib import Path
from types import SimpleNamespace
from streaming.alert_outbox import Outbox, work_one
from urllib.error import HTTPError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread


def test_redis_failure_is_durable_and_route_cannot_be_changed(tmp_path, monkeypatch):
    import sys
    class RedisError(Exception):
        pass
    class AuthenticationError(RedisError):
        pass
    class ResponseError(RedisError):
        pass
    class ConnectionError(RedisError):
        pass
    client = SimpleNamespace(set=lambda *a, **k: None)
    fake = SimpleNamespace(Redis=SimpleNamespace(from_url=lambda *a, **k: client),
                           retry=SimpleNamespace(Retry=lambda *a: None), backoff=SimpleNamespace(NoBackoff=lambda: None),
                           exceptions=SimpleNamespace(RedisError=RedisError,
                             AuthenticationError=AuthenticationError, ResponseError=ResponseError))
    monkeypatch.setitem(sys.modules, "redis", fake)
    path = tmp_path / "redis.sqlite"
    url = "http://localhost"
    queue = Outbox(path, url, redis_url="redis://localhost:6379/0")
    queue.enqueue({"power": 100})
    delivered = []
    def failing(*a, **k):
        assert k["redis_client"] is client
        raise ConnectionError("redis://secret-must-not-appear")
    monkeypatch.setattr("streaming.alert_outbox.deliver", failing)
    work_one(queue, url, base_delay=0)
    assert queue.stats() == {"pending": 1}
    assert queue.db.execute("SELECT error FROM messages").fetchone()[0] == "Redis ConnectionError"
    monkeypatch.setattr("streaming.alert_outbox.deliver", lambda *a, **k: delivered.append(k))
    work_one(queue, url)
    assert queue.stats() == {"sent": 1} and len(delivered) == 1
    queue.db.close()
    for new_redis in (None, "redis://localhost:6379/1"):
        with pytest.raises(ValueError, match="different"):
            Outbox(path, url, redis_url=new_redis)


def test_redis_auth_failure_goes_dead(tmp_path, monkeypatch):
    import sys
    class RedisError(Exception):
        pass
    class AuthenticationError(RedisError):
        pass
    fake = SimpleNamespace(Redis=SimpleNamespace(from_url=lambda *a, **k: object()),
                           retry=SimpleNamespace(Retry=lambda *a: None), backoff=SimpleNamespace(NoBackoff=lambda: None),
                           exceptions=SimpleNamespace(RedisError=RedisError,
                             AuthenticationError=AuthenticationError, ResponseError=AuthenticationError))
    monkeypatch.setitem(sys.modules, "redis", fake)
    queue = Outbox(tmp_path / "auth.sqlite", "http://localhost", redis_url="redis://localhost")
    queue.enqueue({"power": 1})
    def fail(*a, **k):
        raise AuthenticationError("password")
    monkeypatch.setattr("streaming.alert_outbox.deliver", fail)
    work_one(queue, "http://localhost")
    assert queue.stats() == {"dead": 1}
    queue.db.close()


def test_kafka_evidence_rejects_stale_and_incomplete_reports():
    from tools.summarize_extension_runtime import validate_kafka_outbox
    report = json.loads((Path(__file__).resolve().parents[1] / "output/extensions/kafka_outbox_runtime.json").read_text(encoding="utf-8"))
    sources, alerts_hash = report["source_hashes"], report["source_alerts_sha256"]
    validate_kafka_outbox(report, sources, alerts_hash)
    for key, value in (("webhook_unique_received", 19), ("offset_after_replay", 22),
                       ("crash_after_persist_before_commit_recovered", False), ("source_alerts_sha256", "stale")):
        with pytest.raises(ValueError):
            validate_kafka_outbox({**report, key: value}, sources, alerts_hash)


def test_redis_evidence_rejects_missing_delivery_and_auth_checks():
    from tools.summarize_extension_runtime import validate_redis_outbox
    report = {"success": True, "source_hashes": {"code": "hash"}, "source_alerts_sha256": "alerts",
              "final_states": {"sent": 20}, "unique_received": 20, "redis_keys": 20,
              "pending_after_outage": 20, "http_attempts": 40, "ttl_min_seconds": 86398,
              "ttl_max_seconds": 86400, "redis_cached_webhook_503_observed": True,
              "redis_outage_durable_retry_observed": True, "process_restart_recovered": True,
              "all_payloads_match": True, "auth_failure_dead_without_http": True}
    validate_redis_outbox(report, {"code": "hash"}, "alerts")
    for key, value in (("redis_keys", 19), ("auth_failure_dead_without_http", False),
                       ("pending_after_outage", 0), ("source_hashes", {})):
        with pytest.raises(ValueError):
            validate_redis_outbox({**report, key: value}, {"code": "hash"}, "alerts")


def test_kafka_commit_only_after_durable_enqueue(tmp_path):
    from streaming.kafka_outbox import persist_and_commit
    queue = Outbox(tmp_path / "kafka.sqlite", "http://localhost")
    message = SimpleNamespace(topic="alerts", partition=0, offset=7, value=b'{"power":100}')
    committed = []
    def commit(offsets):
        # A separate connection sees the transaction before Kafka is acknowledged.
        second = Outbox(tmp_path / "kafka.sqlite", "http://localhost")
        assert second.stats() == {"pending": 1}
        second.db.close()
        committed.append(offsets)
    consumer = SimpleNamespace(commit=commit)
    factory = lambda *args: tuple(args)
    persist_and_commit(queue, consumer, message, factory, factory)
    persist_and_commit(queue, consumer, message, factory, factory)
    assert committed == [{("alerts", 0): (8, "", -1)}] * 2
    message.value = b"invalid"
    with pytest.raises(ValueError):
        persist_and_commit(queue, consumer, message, factory, factory)
    assert len(committed) == 2
    queue.db.close()


def test_restart_dedup_and_fencing(tmp_path):
    now = [100.0]
    path = tmp_path / "queue.sqlite"
    a = Outbox(path, "http://localhost/test", lambda: now[0])
    ident = a.enqueue({"power": 100})
    assert a.enqueue({"power": 100}) == ident
    old = a.claim(lease_seconds=1)
    b = Outbox(path, "http://localhost/test", lambda: now[0])
    assert b.claim() is None
    now[0] += 2
    new = b.claim()
    assert new["attempts"] == 2
    assert not a.finish(old)
    assert b.finish(new)
    a.db.close()
    b.db.close()
    reopened = Outbox(path, "http://localhost/test")
    assert reopened.stats() == {"sent": 1}
    reopened.db.close()
    with pytest.raises(ValueError, match="different"):
        Outbox(path, "http://localhost/other")


def test_retry_delay_dead_and_manual_recovery(tmp_path, monkeypatch):
    now = [0.0]
    queue = Outbox(tmp_path / "retry.sqlite", "http://localhost", lambda: now[0])
    ident = queue.enqueue({"fault": True})
    def fail(*args, **kwargs):
        raise HTTPError("http://localhost", 503, "unavailable", {}, None)
    monkeypatch.setattr("streaming.alert_outbox.deliver", fail)
    assert work_one(queue, "http://localhost", max_attempts=2, base_delay=3)
    assert not work_one(queue, "http://localhost", max_attempts=2, base_delay=3)
    now[0] = 3
    assert work_one(queue, "http://localhost", max_attempts=2, base_delay=3)
    assert queue.stats() == {"dead": 1}
    assert queue.requeue(ident)
    monkeypatch.setattr("streaming.alert_outbox.deliver", lambda *a, **k: None)
    assert work_one(queue, "http://localhost")
    assert queue.stats() == {"sent": 1}
    queue.db.close()


def test_permanent_http_failure_and_invalid_payload(tmp_path, monkeypatch):
    queue = Outbox(tmp_path / "permanent.sqlite", "http://localhost")
    for value in ([], {}, {"power": float("nan")}):
        with pytest.raises(ValueError):
            queue.enqueue(value)
    queue.enqueue({"power": 1})
    def fail(*a, **k):
        raise HTTPError("http://localhost", 400, "invalid", {}, None)
    monkeypatch.setattr("streaming.alert_outbox.deliver", fail)
    work_one(queue, "http://localhost")
    assert queue.stats() == {"dead": 1}
    assert queue.db.execute("SELECT attempts FROM messages").fetchone()[0] == 1
    queue.db.close()


@pytest.mark.parametrize("status", [200, 204, 301, 302, 303, 307, 308, 400, 503])
def test_webhook_requires_direct_success(tmp_path, status):
    received = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(("POST", self.path))
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(status)
            self.send_header("Location", "/landing")
            self.end_headers()

        def do_GET(self):
            received.append(("GET", self.path))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    url = f"http://127.0.0.1:{server.server_port}/alerts"
    queue = Outbox(tmp_path / "redirect.sqlite", url)
    try:
        queue.enqueue({"power": 100})
        assert work_one(queue, url)
        expected_state = "sent" if status < 300 else "pending" if status >= 500 else "dead"
        assert queue.stats() == {expected_state: 1}
        expected_error = None if status < 300 else f"HTTP {status}"
        assert queue.db.execute("SELECT error FROM messages").fetchone()[0] == expected_error
        assert received == [("POST", "/alerts")]
    finally:
        queue.db.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_lowered_retry_limit_after_restart_does_not_send_again(tmp_path):
    path = tmp_path / "retry-limit.sqlite"
    queue = Outbox(path, "http://localhost", clock=lambda: 0)
    ident = queue.enqueue({"power": 100})
    row = queue.claim(max_attempts=5)
    queue.finish(row, "HTTP 503", max_attempts=5, base_delay=0)
    queue.db.close()
    queue = Outbox(path, "http://localhost", clock=lambda: 0)
    try:
        assert queue.claim(max_attempts=1) is None
        assert queue.stats() == {"dead": 1}
        assert queue.db.execute("SELECT attempts FROM messages").fetchone()[0] == 1
        assert queue.requeue(ident)
        assert queue.claim(max_attempts=1)["attempts"] == 1
    finally:
        queue.db.close()
