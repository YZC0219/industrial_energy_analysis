"""Isolated Redis/Webhook/ClickHouse test using actual archived Flink alerts.

Requires pre-pulled images; never contacts a real notification destination.
All service data/log mounts reside under the project output directory on D.
Containers are stopped, retained for inspection, and never removed by this tool.
"""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dependencies/redisclient"))
from streaming.dispatch import deliver, prepare
from streaming.minute_store import aggregate, clickhouse_query, insert_rows, read_minutes


def docker(*args):
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True, encoding="utf-8", timeout=90).stdout.strip()


def wait_for(predicate, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except Exception:
            pass
        time.sleep(1)
    raise TimeoutError("Isolated service did not become ready")


def run():
    import redis
    folder = ROOT / "output/extensions"
    source_report = json.loads((folder / "power_runtime.json").read_text(encoding="utf-8"))
    if not source_report["success"] or source_report["sql_sha256"] != hashlib.sha256((ROOT / "streaming/flink/power_baseline.sql").read_bytes()).hexdigest():
        raise ValueError("Run current Flink verifier first")
    evidence = Path(source_report["evidence_directory"])
    if not evidence.resolve().is_relative_to(folder.resolve()):
        raise ValueError("Power evidence must reside in the project output folder")
    alerts = json.loads((evidence / "alerts.json").read_text(encoding="utf-8"))
    points = [json.loads(line) for line in (evidence / "input.jsonl").read_text().splitlines()]
    if len(alerts) != 20:
        raise ValueError("Expected 20 actual Flink alerts")
    run_id = uuid4().hex[:12]
    work = folder / ("sinks_" + run_id)
    work.mkdir(parents=True)
    names = ["energy-poc-redis-" + run_id, "energy-poc-clickhouse-" + run_id]
    report = {"success": False, "run_id": run_id, "generated_at": datetime.now(timezone.utc).isoformat(),
              "evidence_directory": str(work), "webhook_scope": "127.0.0.1 test receiver only",
              "containers": names, "source_power_report_sha256": hashlib.sha256((folder / "power_runtime.json").read_bytes()).hexdigest(),
              "source_hashes": {str(p.relative_to(ROOT)).replace('\\','/'): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in [ROOT / "streaming/dispatch.py", ROOT / "streaming/minute_store.py", ROOT / "streaming/clickhouse.sql"]}}
    report["phase"] = "service_start"
    started = []
    receiver = None
    client = None
    try:
        for directory in ("redis", "clickhouse_logs"):
            (work / directory).mkdir()
        config = work / "clickhouse-poc.xml"
        config.write_text("<clickhouse><background_schedule_pool_size>16</background_schedule_pool_size>"
                          "<background_pool_size>16</background_pool_size><max_thread_pool_size>1024</max_thread_pool_size>"
                          "<background_buffer_flush_schedule_pool_size>4</background_buffer_flush_schedule_pool_size>"
                          "<background_message_broker_schedule_pool_size>4</background_message_broker_schedule_pool_size>"
                          "<background_distributed_schedule_pool_size>4</background_distributed_schedule_pool_size></clickhouse>")
        docker("run", "-d", "--pull", "never", "--name", names[0], "-p", "127.0.0.1::6379",
               "--mount", f"type=bind,source={work / 'redis'},target=/data", "redis:7.4-alpine",
               "redis-server", "--appendonly", "yes")
        started.append(names[0])
        volume = "energy-poc-clickhouse-data-" + run_id
        docker("volume", "create", volume)
        report["clickhouse_volume"] = volume
        report["clickhouse_storage"] = "native Docker volume in D:/Docker/wsl/disk/docker_data.vhdx"
        docker("run", "-d", "--pull", "never", "--name", names[1], "--memory", "2g", "--cpus", "2",
               "-p", "127.0.0.1::8123", "-e", "CLICKHOUSE_SKIP_USER_SETUP=1",
               "--mount", f"type=volume,source={volume},target=/var/lib/clickhouse",
               "--mount", f"type=bind,source={work / 'clickhouse_logs'},target=/var/log/clickhouse-server",
               "--mount", f"type=bind,source={config},target=/etc/clickhouse-server/config.d/poc.xml,readonly",
               "clickhouse/clickhouse-server:25.8-alpine")
        started.append(names[1])
        redis_port = int(docker("port", names[0], "6379/tcp").rsplit(":", 1)[1])
        ch_port = int(docker("port", names[1], "8123/tcp").rsplit(":", 1)[1])
        client = redis.Redis(host="127.0.0.1", port=redis_port, socket_timeout=10, decode_responses=True)
        ch_url = f"http://127.0.0.1:{ch_port}"
        wait_for(client.ping)
        wait_for(lambda: clickhouse_query(ch_url, "SELECT 1").strip() == "1")
        report["phase"] = "redis_webhook"
        received, attempts = {}, []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                data = json.loads(body)
                key = self.headers.get("Idempotency-Key")
                attempts.append({"path": self.path, "key": key})
                if self.path == "/fail":
                    self.send_response(503)
                elif key != prepare(data)[0]:
                    self.send_response(400)
                else:
                    received.setdefault(key, data)
                    self.send_response(200)
                self.end_headers()
            def log_message(self, *_):
                pass
        receiver = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=receiver.serve_forever, daemon=True).start()
        webhook = f"http://127.0.0.1:{receiver.server_port}"
        for _ in range(2):
            for alert in alerts:
                deliver(alert, client, webhook + "/alerts")
        if len(received) != 20 or len(attempts) != 40:
            raise RuntimeError("Receiver replay deduplication mismatch")
        ttl_values = []
        for alert in alerts:
            key, payload = prepare(alert)
            if client.get("energy:alert:" + key) != payload:
                raise RuntimeError("Redis payload mismatch")
            ttl = client.ttl("energy:alert:" + key)
            if not 86300 <= ttl <= 86400:
                raise RuntimeError("Redis 24-hour TTL mismatch")
            ttl_values.append(ttl)
        partial = {"run_id": run_id, "test_case": "redis_success_webhook_failure"}
        try:
            deliver(partial, client, webhook + "/fail")
        except HTTPError as exc:
            if exc.code != 503:
                raise
        else:
            raise RuntimeError("Webhook failure was swallowed")
        partial_key, partial_payload = prepare(partial)
        if client.get("energy:alert:" + partial_key) != partial_payload:
            raise RuntimeError("Partial Redis success was not observable")
        deliver(partial, client, webhook + "/alerts")
        if partial_key not in received:
            raise RuntimeError("Explicit replay did not recover failed Webhook")
        (work / "receiver_attempts.json").write_text(json.dumps(attempts,indent=2))
        (work / "receiver_unique.json").write_text(json.dumps(received,indent=2))
        report["phase"] = "clickhouse_ddl"
        database = "energy_sink_probe_" + run_id
        ddl = (ROOT / "streaming/clickhouse.sql").read_text().replace("energy_poc", database)
        ddl = "\n".join(line for line in ddl.splitlines() if not line.lstrip().startswith("--"))
        for statement in ddl.split(";"):
            if statement.strip() and ("CREATE DATABASE" in statement or "CREATE TABLE" in statement):
                clickhouse_query(ch_url, statement)
        rows = aggregate(points)
        report["phase"] = "clickhouse_replay"
        insert_rows(rows, ch_url, database)
        insert_rows(rows, ch_url, database)
        actual = read_minutes(ch_url, database)
        if actual != rows:
            raise RuntimeError("ClickHouse FINAL minute rows differ after identical replay")
        physical = int(clickhouse_query(ch_url, f"SELECT count() FROM {database}.power_minutes").strip())
        try:
            insert_rows(rows, ch_url, "database_not_created_for_probe")
        except HTTPError:
            missing_table_failed = True
        else:
            raise RuntimeError("ClickHouse insertion failure was swallowed")
        (work / "receiver_attempts.json").write_text(json.dumps(attempts,indent=2))
        (work / "receiver_unique.json").write_text(json.dumps(received,indent=2))
        (work / "minute_expected.json").write_text(json.dumps(rows,indent=2))
        (work / "minute_actual.json").write_text(json.dumps(actual,indent=2))
        report.update(success=True, redis={"version": client.info("server")["redis_version"], "alert_keys": 20,
                                          "ttl_min_seconds": min(ttl_values), "ttl_max_seconds": max(ttl_values)},
                      webhook={"alert_attempts": 40, "unique_alerts": 20, "idempotency_scope": "test receiver",
                               "failure_503_propagated": True, "partial_redis_success_observed": True, "explicit_retry_recovered": True},
                      clickhouse={"version": clickhouse_query(ch_url,"SELECT version()").strip(), "unique_minute_rows": len(rows),
                                  "physical_rows": physical, "identical_replay_final_reconciled": True,
                                  "missing_table_failed": missing_table_failed, "database": database},
                      image_ids={name: docker("inspect", name, "--format", "{{.Image}}") for name in names})
        report["phase"] = "complete"
    except Exception as exc:
        report["error"] = str(exc)
        if isinstance(exc, HTTPError):
            report["http_error_body"] = exc.read().decode("utf-8", errors="replace")[:4000]
    finally:
        if receiver:
            receiver.shutdown()
            receiver.server_close()
        if client:
            client.close()
        for name in reversed(started):
            try:
                (work / (name + ".log")).write_text(docker("logs", name),encoding="utf-8")
                docker("stop", name)
            except Exception as exc:
                report.setdefault("cleanup_errors", []).append(str(exc))
        (folder / "sink_runtime.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(run())
