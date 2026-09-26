"""Run a local Kafka/Flink out-of-order, checkpoint-recovery, alert experiment."""
from __future__ import annotations

import argparse
import base64
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

from tools.build_flink_udf import main as build_flink_udf

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "streaming_experiment.json"
REST = "http://127.0.0.1:8081"


def _compose(*args: str, timeout: int = 120, check: bool = True, **kwargs):
    return subprocess.run(
        ["docker", "compose", "--profile", "streaming", *args],
        cwd=ROOT, timeout=timeout, check=check, capture_output=True,
        text=True, encoding="utf-8", **kwargs,
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


def _publish_raw(payload: bytes) -> tuple[int, int]:
    """Use the byte-oriented Kafka producer; console-producer transcodes UTF-8."""
    KafkaProducer = _require_kafka_producer()
    producer = KafkaProducer(bootstrap_servers=["127.0.0.1:29092"], acks="all")
    try:
        record = producer.send("energy-events", value=payload).get(timeout=30)
        return record.partition, record.offset
    finally:
        producer.close(timeout=10)


def _require_kafka_producer():
    try:
        from kafka import KafkaProducer
    except ImportError as exc:
        raise RuntimeError(
            "kafka-python is required before starting Docker/Flink; "
            "install requirements.txt in the project Python environment "
            "(on Windows, keep it on D:)"
        ) from exc
    return KafkaProducer


def build_fault_samples(run_id: str, template: dict) -> dict[str, bytes]:
    """Poison the same alert window before its watermark to test isolation."""
    def encoded(suffix: str, **overrides) -> bytes:
        return json.dumps({**template, "event_id": f"{run_id}-{suffix}",
                           "cost": 500, **overrides}, separators=(",", ":")).encode("utf-8")

    return {
        "invalid_json": b'{"event_id":"' + run_id.encode("ascii") + b'-broken-json"',
        "invalid_utf8": b'{"event_id":"' + run_id.encode("ascii") + b'-bad-utf8","text":"\xff"}',
        "unsupported_schema_version": encoded("schema-v2", schema_version=2),
        "invalid_numeric_field": encoded("bad-numeric", consumption="not-a-number"),
        "invalid_event_time": encoded("bad-event-time", event_time="2026-13-99T00:00:00Z"),
        "invalid_updated_at": encoded("bad-updated-at", updated_at="2026-02-30T00:00:00Z"),
        "invalid_record_date": encoded("bad-record-date", record_date="2026-02-30"),
        "invalid_business_key_workshop": encoded("bad-workshop-code", workshop_code="WXX"),
        "invalid_business_key_energy": encoded("bad-energy-code", energy_code="EXX"),
        "invalid_op": encoded("bad-op", op="MERGE"),
        "invalid_event_id": encoded("bad-event-id", event_id=""),
        "invalid_unit": encoded("bad-unit", unit=""),
    }


def _running_job() -> dict | None:
    jobs = _request("/jobs/overview").get("jobs", [])
    return next((job for job in jobs if job.get("state") == "RUNNING"), None)


def _checkpoint_id(job_id: str) -> str | None:
    state = _request(f"/jobs/{job_id}/checkpoints")
    completed = state.get("latest", {}).get("completed") or {}
    return str(completed["id"]) if completed.get("id") is not None else None


def _start_topic_consumer(topic: str, run_id: str):
    process = subprocess.Popen(
        ["docker", "compose", "--profile", "streaming", "exec", "-T", "kafka",
         "/opt/kafka/bin/kafka-console-consumer.sh", "--bootstrap-server", "kafka:9092",
         "--topic", topic, "--group", f"stream-demo-{topic}-{run_id}",
         "--consumer-property", "auto.offset.reset=latest", "--timeout-ms", "120000"],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8",
    )
    messages: queue.Queue = queue.Queue()

    def read_messages():
        assert process.stdout is not None
        for line in process.stdout:
            try:
                messages.put((time.monotonic(), json.loads(line)))
            except json.JSONDecodeError:
                continue

    threading.Thread(target=read_messages, daemon=True).start()
    return process, messages


def _wait_for_new_jobs(previous_ids: set[str], *, minimum: int, timeout: int) -> list[dict]:
    def new_running_jobs():
        jobs = _request("/jobs/overview").get("jobs", [])
        return [job for job in jobs if job.get("state") == "RUNNING"
                and job.get("jid") not in previous_ids]

    result = _wait_for(lambda: (jobs if len(jobs := new_running_jobs()) >= minimum else None),
                       timeout=timeout, description=f"{minimum} routed Flink jobs")
    return result


def run_experiment(*, keep_running: bool = False) -> dict:
    # Fail before touching containers or checkpoint state if the raw producer is absent.
    _require_kafka_producer()
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
        "alert_latency_reference": "watermark_publish_started",
        "late_side_output_tested": False,
        "delete_event_route_tested": False,
        "invalid_event_quarantine_tested": False,
        "malformed_json_quarantine_tested": False,
        "invalid_utf8_quarantine_tested": False,
        "unsupported_schema_quarantine_tested": False,
        "invalid_numeric_quarantine_tested": False,
        "invalid_event_time_quarantine_tested": False,
        "invalid_updated_at_quarantine_tested": False,
        "invalid_record_date_quarantine_tested": False,
        "invalid_business_key_quarantine_tested": False,
        "invalid_op_quarantine_tested": False,
        "invalid_event_id_quarantine_tested": False,
        "invalid_unit_quarantine_tested": False,
        "invalid_late_op_blocked": False,
        "invalid_temporal_delete_blocked": False,
        "taskmanager_restart_tested": False,
        "success": False,
    }
    consumer = None
    try:
        # Always rebuild: a stale local JAR could otherwise silently omit a new validator.
        build_flink_udf()
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
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8",
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

        prior_job_ids = {item.get("jid") for item in _request("/jobs/overview").get("jobs", [])}
        route_client = subprocess.Popen(
            ["docker", "compose", "--profile", "streaming", "exec", "-T",
             "flink-jobmanager", "/opt/flink/bin/sql-client.sh", "-f",
             "/opt/flink/sql/energy_quality_routes.sql"],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8",
        )
        try:
            _wait_for_new_jobs(prior_job_ids, minimum=3, timeout=120)
            report["quality_route_jobs_started"] = 3
        finally:
            route_client_stopped_after_jobs_running = route_client.poll() is None
            report["quality_route_sql_client_stopped_after_jobs_running"] = (
                route_client_stopped_after_jobs_running
            )
            if route_client_stopped_after_jobs_running:
                route_client.terminate()
                try:
                    route_stdout, route_stderr = route_client.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    route_client.kill()
                    route_stdout, route_stderr = route_client.communicate()
            else:
                route_stdout, route_stderr = route_client.communicate()
            # A deliberate SIGTERM after all INSERT jobs are RUNNING is expected;
            # do not report that client shutdown status as a submission failure.
            report["quality_route_sql_client_exit_code"] = (
                None if route_client_stopped_after_jobs_running else route_client.returncode
            )
            report["quality_route_sql_client_stderr"] = route_stderr[-2000:]

        raw_prior_ids = {item.get("jid") for item in _request("/jobs/overview").get("jobs", [])}
        raw_client = subprocess.Popen(
            ["docker", "compose", "--profile", "streaming", "exec", "-T",
             "flink-jobmanager", "/opt/flink/bin/sql-client.sh", "-f",
             "/opt/flink/sql/energy_raw_quarantine.sql"],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8",
        )
        try:
            raw_jobs = _wait_for_new_jobs(raw_prior_ids, minimum=1, timeout=120)
            report["raw_quarantine_job_id"] = raw_jobs[0]["jid"]
        finally:
            if raw_client.poll() is None:
                raw_client.terminate()
            try:
                _, raw_stderr = raw_client.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                raw_client.kill()
                _, raw_stderr = raw_client.communicate()
            report["raw_quarantine_sql_client_stderr"] = raw_stderr[-2000:]

        previous_checkpoint = _checkpoint_id(job["jid"])
        consumers = {}
        for topic in ("energy-alerts", "energy-late-events", "energy-delete-events",
                      "energy-invalid-events", "energy-malformed-events"):
            consumers[topic] = _start_topic_consumer(topic, run_id)
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
        # Compose v5 start also starts one-shot dependencies; use up --no-deps
        # so a TaskManager restart does not rerun network-dependent init jobs.
        _compose("up", "-d", "--no-deps", "flink-taskmanager", timeout=60)
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

        # Use a fresh future window after recovery. Matching only workshop/count/cost
        # could accidentally consume a retained alert from an earlier run and report
        # a negative latency. The unique window_start makes this run's alert explicit.
        fresh_base_ts = int(datetime.now(timezone.utc).timestamp())
        fresh_window_start = datetime.fromtimestamp(
            fresh_base_ts - fresh_base_ts % 10 + 60, tz=timezone.utc
        )
        fresh_events, fresh_watermark = build_window_events(f"{run_id}-recovered", fresh_window_start)
        report["post_restart_window_start"] = fresh_window_start.isoformat(timespec="seconds")
        _publish(fresh_events)
        fault_payloads = build_fault_samples(run_id, fresh_events[0])
        expected_malformed = {
            _publish_raw(payload): (
                "invalid_business_key" if reason.startswith("invalid_business_key_") else reason,
                payload,
            )
            for reason, payload in fault_payloads.items()
        }
        report["faults_sent_before_watermark"] = True
        time.sleep(0.5)
        alert_started = time.monotonic()
        _publish([fresh_watermark])
        late_event = {
            **fresh_events[0], "event_id": f"{run_id}-late", "event_time": fresh_events[0]["event_time"],
            "op": "UPSERT", "consumption": 9.0, "unit_price": 1.0, "cost": 9.0,
        }
        delete_event = {
            **fresh_events[0], "event_id": f"{run_id}-delete", "event_time": fresh_watermark["event_time"],
            "op": "DELETE", "workshop_code": "W02", "consumption": None,
            "unit_price": None, "cost": None,
        }
        invalid_event = {
            **fresh_events[0], "event_id": f"{run_id}-invalid", "event_time": fresh_watermark["event_time"],
            "consumption": -1.0, "cost": -1.0,
        }
        time.sleep(1)
        _publish([late_event, delete_event, invalid_event])

        def wait_for_event(topic: str, event_id: str, timeout: int = 20):
            process, messages = consumers[topic]
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if process.poll() is not None and messages.empty():
                    return None
                try:
                    received_at, item = messages.get(
                        timeout=min(0.5, max(0.05, deadline - time.monotonic()))
                    )
                except queue.Empty:
                    continue
                if item.get("event_id") == event_id:
                    return received_at, item
            return None

        late_result = wait_for_event("energy-late-events", late_event["event_id"])
        delete_result = wait_for_event("energy-delete-events", delete_event["event_id"])
        invalid_result = wait_for_event("energy-invalid-events", invalid_event["event_id"])
        late_routed = late_result[1] if late_result else None
        delete_routed = delete_result[1] if delete_result else None
        invalid_routed = invalid_result[1] if invalid_result else None
        report["late_event"] = late_routed
        report["delete_event"] = delete_routed
        report["invalid_event"] = invalid_routed
        report["late_side_output_tested"] = late_routed is not None
        report["delete_event_route_tested"] = delete_routed is not None
        report["invalid_event_quarantine_tested"] = (
            invalid_routed is not None and invalid_routed.get("quality_error") == "invalid_consumption"
        )

        # An invalid DELETE must not leak into either the delete or late side topic.
        invalid_delete = {
            **delete_event, "event_id": f"{run_id}-bad-delete-time",
            "event_time": fresh_events[0]["event_time"],
            "updated_at": "2026-02-30T00:00:00Z",
        }
        invalid_delete_raw = json.dumps(invalid_delete, separators=(",", ":")).encode("utf-8")
        expected_malformed[_publish_raw(invalid_delete_raw)] = (
            "invalid_updated_at", invalid_delete_raw
        )
        report["invalid_temporal_delete_blocked"] = (
            wait_for_event("energy-delete-events", invalid_delete["event_id"], timeout=8) is None
            and wait_for_event("energy-late-events", invalid_delete["event_id"], timeout=8) is None
        )
        invalid_late = {
            **late_event, "event_id": f"{run_id}-bad-late-op", "op": "MERGE",
        }
        invalid_late_raw = json.dumps(invalid_late, separators=(",", ":")).encode("utf-8")
        expected_malformed[_publish_raw(invalid_late_raw)] = ("invalid_op", invalid_late_raw)
        report["invalid_late_op_blocked"] = (
            wait_for_event("energy-late-events", invalid_late["event_id"], timeout=8) is None
        )

        observed_malformed = {}
        malformed_process, malformed_messages = consumers["energy-malformed-events"]
        malformed_deadline = time.monotonic() + 30
        while len(observed_malformed) < len(expected_malformed) and time.monotonic() < malformed_deadline:
            if malformed_process.poll() is not None and malformed_messages.empty():
                break
            try:
                _, item = malformed_messages.get(timeout=0.5)
            except queue.Empty:
                continue
            key = (item.get("source_partition"), item.get("source_offset"))
            if key in expected_malformed:
                observed_malformed[key] = item
        report["malformed_events"] = list(observed_malformed.values())
        flag_name_by_reason = {
            "invalid_json": "malformed_json_quarantine_tested",
            "invalid_utf8": "invalid_utf8_quarantine_tested",
            "unsupported_schema_version": "unsupported_schema_quarantine_tested",
            "invalid_numeric_field": "invalid_numeric_quarantine_tested",
            "invalid_event_time": "invalid_event_time_quarantine_tested",
            "invalid_updated_at": "invalid_updated_at_quarantine_tested",
            "invalid_record_date": "invalid_record_date_quarantine_tested",
            "invalid_business_key": "invalid_business_key_quarantine_tested",
            "invalid_op": "invalid_op_quarantine_tested",
            "invalid_event_id": "invalid_event_id_quarantine_tested",
            "invalid_unit": "invalid_unit_quarantine_tested",
        }
        raw_passed_by_reason = {}
        for key, (reason, payload) in expected_malformed.items():
            item = observed_malformed.get(key, {})
            passed = (item.get("quality_error") == reason
                      and item.get("source_topic") == "energy-events"
                      and item.get("payload_base64") == base64.b64encode(payload).decode("ascii"))
            raw_passed_by_reason.setdefault(reason, []).append(passed)
        for reason, flag_name in flag_name_by_reason.items():
            report[flag_name] = bool(raw_passed_by_reason.get(reason)) and all(
                raw_passed_by_reason[reason]
            )

        deadline = alert_started + 15
        matched = None
        alert_received_at = None
        alert_process, messages = consumers["energy-alerts"]
        while True:
            if alert_process.poll() is not None and messages.empty():
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0 and messages.empty():
                break
            try:
                received_at, candidate = messages.get(timeout=max(0.05, min(0.5, remaining)))
            except queue.Empty:
                break
            if candidate.get("workshop_code") == "W04" \
                    and str(candidate.get("window_start", "")).startswith(
                        fresh_window_start.isoformat(timespec="seconds")[:19]
                    ) \
                    and int(candidate.get("event_count", -1)) == 3 \
                    and Decimal(str(candidate.get("total_cost", "0"))) == Decimal("155.00"):
                matched = candidate
                alert_received_at = received_at
                break
        report["alert_latency_seconds"] = (
            round(alert_received_at - alert_started, 3) if alert_received_at else None
        )
        report["alert"] = matched
        report["seconds_level_alert"] = bool(
            matched is not None and report["alert_latency_seconds"] <= 10
        )
        report["success"] = bool(report["taskmanager_restart_tested"]
                                  and report["faults_sent_before_watermark"]
                                  and report["seconds_level_alert"]
                                  and report["late_side_output_tested"]
                                  and report["delete_event_route_tested"]
                                  and report["invalid_event_quarantine_tested"]
                                  and report["malformed_json_quarantine_tested"]
                                  and report["invalid_utf8_quarantine_tested"]
                                  and report["unsupported_schema_quarantine_tested"]
                                  and report["invalid_numeric_quarantine_tested"]
                                  and report["invalid_event_time_quarantine_tested"]
                                  and report["invalid_updated_at_quarantine_tested"]
                                  and report["invalid_record_date_quarantine_tested"]
                                  and report["invalid_business_key_quarantine_tested"]
                                  and report["invalid_op_quarantine_tested"]
                                  and report["invalid_event_id_quarantine_tested"]
                                  and report["invalid_unit_quarantine_tested"]
                                  and report["invalid_late_op_blocked"]
                                  and report["invalid_temporal_delete_blocked"])
        if not report["success"]:
            report["error"] = "One or more window, recovery, late-event, delete-route, or quality-route checks failed"
        return report
    finally:
        for process, _ in locals().get("consumers", {}).values():
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
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
