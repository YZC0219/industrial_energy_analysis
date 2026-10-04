"""Real isolated Kafka/Flink window verification; keep input/output and SQL evidence."""
from __future__ import annotations
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.request import Request, urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from streaming.simulate_power import samples


def expected(points):
    result = {}
    start = datetime.fromisoformat(points[0]["event_time"])
    for offset in range(1, 61):
        end = start + timedelta(minutes=offset)
        subset = [p for p in points if p["workshop_code"] == "W01" and
                  end - timedelta(minutes=15) <= datetime.fromisoformat(p["event_time"]) < end]
        if len(subset) < 15:
            continue
        mean = sum(p["power_kw"] for p in subset) / len(subset)
        if mean <= 120:
            continue
        xs = [(datetime.fromisoformat(p["event_time"]) - end).total_seconds()/60 for p in subset]
        xm = sum(xs)/len(xs)
        slope = sum((x-xm)*(p["power_kw"]-mean) for x,p in zip(xs,subset)) / sum((x-xm)**2 for x in xs)
        result[end.replace(tzinfo=None).isoformat(timespec="milliseconds")] = (mean, slope, len(subset))
    return result


def verify(args):
    from kafka import KafkaConsumer, KafkaProducer
    from kafka.admin import KafkaAdminClient, NewTopic
    run_id = uuid4().hex[:12]
    source, sink = f"energy-power-poc-{run_id}", f"energy-power-alert-poc-{run_id}"
    folder = args.output.parent / run_id
    folder.mkdir(parents=True, exist_ok=True)
    original = (ROOT / "streaming/flink/power_baseline.sql").read_text(encoding="utf-8")
    sql = original.replace("energy-power-samples", source).replace("energy-power-alerts", sink).replace("energy-power-baseline-poc", run_id)
    path = folder / "submitted.sql"
    path.write_text(sql, encoding="utf-8")
    admin = KafkaAdminClient(bootstrap_servers=args.bootstrap)
    try:
        admin.create_topics([NewTopic(topic, 1, 1) for topic in (source, sink)])
    finally:
        admin.close()
    job = None
    consumer = producer = None
    report = {"run_id": run_id, "source_topic": source, "sink_topic": sink,
              "sql_sha256": hashlib.sha256(original.encode()).hexdigest(), "success": False,
              "generated_at": datetime.now(timezone.utc).isoformat()}
    try:
        subprocess.run(["docker", "cp", str(path), f"{args.jobmanager}:/tmp/power-{run_id}.sql"], check=True)
        submission = subprocess.run(["docker", "exec", args.jobmanager, "/opt/flink/bin/sql-client.sh",
                                    "-f", f"/tmp/power-{run_id}.sql"], capture_output=True, text=True, encoding="utf-8", timeout=90)
        (folder / "submission.log").write_text(submission.stdout + submission.stderr, encoding="utf-8")
        match = re.search(r"Job ID: ([0-9a-f]{32})", submission.stdout)
        if submission.returncode or not match or "[ERROR]" in submission.stdout:
            raise RuntimeError("SQL submission failed; inspect submission.log")
        job = match[1]
        report["job_id"] = job
        producer = KafkaProducer(bootstrap_servers=args.bootstrap, acks="all")
        consumer = KafkaConsumer(sink, bootstrap_servers=args.bootstrap, group_id=run_id,
                                 auto_offset_reset="earliest", enable_auto_commit=False)
        start = datetime(2026, 9, 27, tzinfo=timezone.utc)
        points = list(samples(start))
        points += [{**p, "workshop_code": "W02", "event_id": "other-" + p["event_id"], "power_kw": 200} for p in points]
        points.sort(key=lambda p: p["event_time"])
        wanted = expected(points)
        # Future valid samples advance the event-time watermark without adding a complete window.
        flush = {"event_id": "flush", "workshop_code": "W01", "event_time": (start+timedelta(minutes=60)).isoformat(), "power_kw": 100}
        for point in points + [flush]:
            producer.send(source, key=b"W01", value=json.dumps(point).encode()).get(timeout=30)
        producer.flush()
        (folder / "input.jsonl").write_text("".join(json.dumps(p)+"\n" for p in points+[flush]), encoding="utf-8")
        rows = []
        deadline = time.monotonic() + 80
        while time.monotonic() < deadline:
            polled = consumer.poll(timeout_ms=1000)
            for messages in polled.values():
                rows.extend(json.loads(m.value) for m in messages)
            if len(rows) >= len(wanted):
                break
        # Catch immediate duplicates after the expected rows arrived.
        for messages in consumer.poll(timeout_ms=2000).values():
            rows.extend(json.loads(m.value) for m in messages)
        (folder / "alerts.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
        seen = set()
        for row in rows:
            end = datetime.fromisoformat(row["window_end"]).isoformat(timespec="milliseconds")
            if row["workshop_code"] != "W01" or end not in wanted or end in seen:
                raise RuntimeError("Unexpected, cross-workshop, or duplicate alert")
            mean, slope, count = wanted[end]
            if abs(row["mean_kw"]-mean) > 1e-8 or abs(row["slope_kw_per_minute"]-slope)>1e-8 or row["sample_count"] != count:
                raise RuntimeError("Flink/reference numeric mismatch")
            seen.add(end)
        if len(seen) != len(wanted):
            raise RuntimeError(f"Expected {len(wanted)} windows, received {len(seen)}")
        report.update(success=True, expected_windows=len(wanted), observed_windows=len(rows),
                      cross_workshop_isolation=True, mean_slope_count_reconciled=True,
                      runtime="Flink 1.20.2 / Kafka 3.9.1", evidence_directory=str(folder))
    except Exception as exc:
        report["error"] = str(exc)
    finally:
        if consumer:
            consumer.close()
        if producer:
            producer.close()
        if job:
            try:
                with urlopen(Request(f"{args.rest}/jobs/{job}?mode=cancel", method="PATCH"), timeout=10):
                    report["cancel_requested"] = True
            except Exception as exc:
                report["cleanup_error"] = str(exc)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bootstrap", default="localhost:29092")
    parser.add_argument("--rest", default="http://localhost:8081")
    parser.add_argument("--jobmanager", default="industrial_energy_analysis-flink-jobmanager-1")
    parser.add_argument("--output", type=Path, default=ROOT / "output/extensions/power_runtime.json")
    raise SystemExit(verify(parser.parse_args()))
