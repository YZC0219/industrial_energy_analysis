"""Run a local Kafka/Flink out-of-order, checkpoint-recovery, alert experiment."""
from __future__ import annotations

import argparse
import json
import queue
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "streaming_experiment.json"
REST = "http://127.0.0.1:8081"


def _compose(*args: str, timeout: int = 120, check: bool = True, **kwargs):
    return subprocess.run(
        ["docker", "compose", "--profile", "streaming", *args],
        cwd=ROOT, timeout=timeout, check=check, capture_output=True, text=True, **kwargs,
    )


def _request(path: str) -> dict:
    with urlopen(f"{REST}{path}", timeout=3) as response:
        return json.loads(response.read().decode("utf-8"))


def _wait_for(predicate, *, timeout: int, description: str):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            value = predicate()
            if value:
                return value
        except (URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
        time.sleep(1)
    raise TimeoutError(f"Timed out waiting for {description}; last error={last_error}")


def build_window_events(run_id: str, base: datetime) -> tuple[list[dict], dict]:
    """Make three in-window UPSERTs whose arrival order is intentionally 1,5,3 s."""
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    events = []
    for index, (offset, cost) in enumerate(((1, 60), (5, 50), (3, 45)), start=1):
        event_time = base.replace(microsecond=0) + timedelta(seconds=offset)
        events.append({
            "schema_version": 1,
            "event_id": f"{run_id}-{index}",
            "event_time": event_time.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "updated_at": now,
            "op": "UPSERT",
            "record_date": event_time.date().isoformat(),
            "workshop_code": "W04",
            "energy_code": "E01",
            "consumption": float(cost),
            "unit": "kWh",
            "unit_price": 1.0,
            "cost": float(cost),
            "record_status": "正常",
            "is_production_day": 1,
        })
    next_window = base.replace(microsecond=0) + timedelta(seconds=17)
    watermark_event = {
        **events[0],
        "event_id": f"{run_id}-watermark",
        "event_time": next_window.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "record_date": next_window.date().isoformat(),
        "workshop_code": "W01",
        "consumption": 1.0,
        "unit_price": 1.0,
        "cost": 1.0,
    }
    return events, watermark_event


def _publish(records: list[dict]) -> None:
    data = "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records)
    _compose(
        "exec", "-T", "-i", "kafka", "/opt/kafka/bin/kafka-console-producer.sh",
        "--bootstrap-server", "kafka:9092", "--topic", "energy-events",
        input=data, timeout=30,
    )


def _running_job() -> dict | None:
    jobs = _request("/jobs/overview").get("jobs", [])
    return next((job for job in jobs if job.get("state") == "RUNNING"), None)


def _checkpoint_id(job_id: str) -> str | None:
    state = _request(f"/jobs/{job_id}/checkpoints")
    completed = state.get("latest", {}).get("completed") or {}
    return str(completed["id"]) if completed.get("id") is not None else None


def _start_alert_consumer(run_id: str):
    process = subprocess.Popen(
        ["docker", "compose", "--profile", "streaming", "exec", "-T", "kafka",
         "/opt/kafka/bin/kafka-console-consumer.sh", "--bootstrap-server", "kafka:9092",
         "--topic", "energy-alerts", "--group", f"stream-demo-{run_id}",
         "--consumer-property", "auto.offset.reset=latest", "--timeout-ms", "120000"],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    messages: queue.Queue = queue.Queue()

    def read_messages():
        assert process.stdout is not None
        for line in process.stdout:
            try:
                messages.put(json.loads(line))
            except json.JSONDecodeError:
                continue

    threading.Thread(target=read_messages, daemon=True).start()
    return process, messages


def run_experiment(*, keep_running: bool = False) -> dict:
    run_id = uuid.uuid4().hex[:8]
    now = int(datetime.now(timezone.utc).timestamp()) - 10
    window_start = datetime.fromtimestamp(now - now % 10, tz=timezone.utc)
    events, watermark_event = build_window_events(run_id, window_start)
    report = {
        "run_id": run_id,
        "runtime": "local Docker Compose; single Kafka broker, one Flink TaskManager",
        "window_seconds": 10,
        "watermark_lateness_seconds": 5,
        "arrival_event_time_offsets_seconds": [1, 5, 3],
        "expected_alert": {"workshop_code": "W04", "event_count": 3, "total_cost": 155.0},
        "taskmanager_restart_tested": False,
        "success": False,
    }
    consumer = None
    try:
        _compose("up", "-d", "kafka", "kafka-topics-init", "flink-connector-init",
                 "flink-checkpoint-init", "flink-jobmanager", "flink-taskmanager", timeout=900)
        _wait_for(lambda: _request("/overview"), timeout=180, description="Flink JobManager")
        _wait_for(lambda: _compose(
            "exec", "-T", "kafka", "/opt/kafka/bin/kafka-topics.sh",
            "--bootstrap-server", "kafka:9092", "--list", timeout=10,
        ), timeout=90, description="Kafka topics")

        job = _running_job()
        if job is None:
            client = subprocess.Popen(
                ["docker", "compose", "--profile", "streaming", "exec", "-T",
                 "flink-jobmanager", "/opt/flink/bin/sql-client.sh", "-f",
                 "/opt/flink/sql/energy_window.sql"],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                job = _running_job()
                if job is not None:
                    report["sql_client_stopped_after_job_running"] = client.poll() is None
                    if client.poll() is None:
                        client.terminate()
                    try:
                        stdout, stderr = client.communicate(timeout=10)
                    except subprocess.TimeoutExpired:
                        client.kill()
                        stdout, stderr = client.communicate()
                    if not report["sql_client_stopped_after_job_running"]:
                        report["sql_client_exit_code"] = client.returncode
                    report["sql_client_stderr"] = stderr[-2000:]
                    break
                if client.poll() is not None:
                    stdout, stderr = client.communicate()
                    report["sql_client_exit_code"] = client.returncode
                    report["sql_client_stderr"] = stderr[-2000:]
                    if client.returncode != 0:
                        raise RuntimeError(f"Flink SQL submission failed: {stderr[-2000:]}")
                time.sleep(1)
            else:
                if client.poll() is None:
                    client.terminate()
                    client.wait(timeout=10)
                raise TimeoutError("Flink SQL submission did not create a running job")
        report["flink_job_id"] = job["jid"]

        previous_checkpoint = _checkpoint_id(job["jid"])
        _publish(events)
        time.sleep(1.5)
        def new_checkpoint():
            current = _checkpoint_id(job["jid"])
            return current if current is not None and current != previous_checkpoint else None
        checkpoint = _wait_for(new_checkpoint, timeout=60, description="checkpoint containing in-window events")
        report["completed_checkpoint_before_restart"] = checkpoint

        prior_restore = (_request(f"/jobs/{job['jid']}/checkpoints")
                         .get("latest", {}).get("restored") or {})
        prior_restore_id = str(prior_restore["id"]) if prior_restore.get("id") is not None else None
        _compose("stop", "flink-taskmanager", timeout=30)
        _compose("start", "flink-taskmanager", timeout=60)
        _wait_for(lambda: len(_request("/taskmanagers").get("taskmanagers", [])) == 1,
                  timeout=90, description="TaskManager recovery")
        _wait_for(lambda: (_running_job() or {}).get("jid") == job["jid"],
                  timeout=90, description="Flink job recovery from checkpoint")
        def new_restore():
            value = _request(f"/jobs/{job['jid']}/checkpoints").get("latest", {}).get("restored")
            return value if value and str(value.get("id")) != prior_restore_id else None
        restored = _wait_for(new_restore, timeout=30, description="Flink restored-checkpoint marker")
        report["restored_checkpoint_id"] = str(restored["id"])
        report["restored_checkpoint_path"] = restored.get("external_path")
        if int(report["restored_checkpoint_id"]) < int(checkpoint):
            raise RuntimeError("Flink restored an older checkpoint than the event checkpoint")
        report["taskmanager_restart_tested"] = True

        consumer, messages = _start_alert_consumer(run_id)
        time.sleep(2)
        started = time.monotonic()
        _publish([watermark_event])
        deadline = time.monotonic() + 15
        matched = None
        while time.monotonic() < deadline:
            try:
                candidate = messages.get(timeout=max(0.1, deadline - time.monotonic()))
            except queue.Empty:
                break
            if candidate.get("workshop_code") == "W04" \
                    and int(candidate.get("event_count", -1)) == 3 \
                    and Decimal(str(candidate.get("total_cost", "0"))) == Decimal("155.00"):
                matched = candidate
                break
        report["alert_latency_seconds"] = round(time.monotonic() - started, 3)
        report["alert"] = matched
        report["seconds_level_alert"] = bool(
            matched is not None and report["alert_latency_seconds"] <= 10
        )
        report["success"] = bool(report["taskmanager_restart_tested"]
                                  and report["seconds_level_alert"])
        if not report["success"]:
            report["error"] = "Expected recovered 10-second window alert was not observed in <=10 seconds"
        return report
    finally:
        if consumer is not None and consumer.poll() is None:
            consumer.terminate()
            try:
                consumer.wait(timeout=10)
            except subprocess.TimeoutExpired:
                consumer.kill()
        if not keep_running:
            try:
                _compose("stop", "flink-taskmanager", "flink-jobmanager", "kafka", timeout=60)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep-running", action="store_true",
                        help="leave only the streaming containers running for inspection")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    report = run_experiment(keep_running=args.keep_running)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(f"STREAMING_EXPERIMENT {'PASS' if report['success'] else 'FAIL'} "
          f"report={args.output}")
    if not report["success"]:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
