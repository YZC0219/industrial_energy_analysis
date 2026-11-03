"""Finite JSONL minute downsampling with optional ClickHouse HTTP insertion.

`aggregate` keeps the original single-series workshop mean used by the power POC.
Multi-device days use `aggregate_device_minutes` + `rollup_workshop_minutes`:
workshop total power is the sum of per-device means in the same UTC minute —
never the mean of every device sample flattened together. Missing devices are
omitted (not zero-filled).
"""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
import re
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def clickhouse_query(url, sql, payload=None, max_threads=2):
    if not isinstance(max_threads, int) or max_threads < 1:
        raise ValueError("max_threads must be positive")
    query = urlencode({"query": sql, "max_threads": max_threads, "max_final_threads": max_threads})
    separator = "&" if "?" in url else "?"
    request = Request(url + separator + query, data=payload if payload is not None else b"", method="POST")
    with urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError("ClickHouse query failed")
        return response.read().decode("utf-8")


def table_name(database):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", database):
        raise ValueError("Invalid ClickHouse database identifier")
    return database + ".power_minutes"


def insert_rows(rows, url, database="energy_poc"):
    table = table_name(database)
    payload = "".join(json.dumps(row, allow_nan=False) + "\n" for row in rows).encode()
    if not rows:
        return 0
    clickhouse_query(url, f"INSERT INTO {table} FORMAT JSONEachRow", payload)
    return len(rows)


def read_minutes(url, database="energy_poc"):
    table = table_name(database)
    text = clickhouse_query(url, f"SELECT workshop_code, minute, mean_kw, sample_count FROM {table} FINAL ORDER BY workshop_code, minute FORMAT JSONEachRow")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def floor_minute_utc(timestamp):
    if timestamp.tzinfo is None:
        raise ValueError("event_time must be timezone-aware")
    return timestamp.astimezone(timezone.utc).replace(second=0, microsecond=0)


def dedupe_event_ids(points):
    """Keep first occurrence of each event_id; report duplicate count."""
    unique, seen = [], set()
    duplicates = 0
    for point in points:
        event_id = point["event_id"]
        if event_id in seen:
            duplicates += 1
            continue
        seen.add(event_id)
        unique.append(point)
    return unique, duplicates


def aggregate(points):
    """Legacy single-series workshop minute mean (power POC). Rejects duplicates."""
    groups, seen = defaultdict(list), set()
    for point in points:
        timestamp = datetime.fromisoformat(point["event_time"])
        power = float(point["power_kw"])
        identity = (point["workshop_code"], point["event_id"])
        if identity in seen or timestamp.tzinfo is None or not math.isfinite(power) or power < 0:
            raise ValueError("Duplicate or invalid sample")
        seen.add(identity)
        minute = floor_minute_utc(timestamp)
        groups[(point["workshop_code"], minute)].append(power)
    return [{"workshop_code": workshop, "minute": minute.strftime("%Y-%m-%d %H:%M:%S.000"),
             "mean_kw": math.fsum(values) / len(values), "sample_count": len(values)}
            for (workshop, minute), values in sorted(groups.items())]


def aggregate_device_minutes(points):
    """Device-level UTC-minute means from unique event_id samples.

    Units: mean_kw in kW. Window: floor to UTC minute. Aggregation: arithmetic
    mean of unique samples for the same (workshop, device, minute). Absent
    device-minutes are omitted — missing is not treated as 0 kW.
    represented_seconds carries the sample interval used later for energy.
    """
    unique, duplicates = dedupe_event_ids(points)
    groups = defaultdict(list)
    for point in unique:
        if "device_id" not in point:
            raise ValueError("device_id is required for device-minute aggregation")
        timestamp = datetime.fromisoformat(point["event_time"])
        power = float(point["power_kw"])
        interval = int(point.get("interval_seconds", 60))
        if timestamp.tzinfo is None or not math.isfinite(power) or power < 0 or interval < 1:
            raise ValueError("Invalid device power sample")
        minute = floor_minute_utc(timestamp)
        key = (point["workshop_code"], point["device_id"], minute)
        groups[key].append((power, interval))
    rows = []
    for (workshop, device_id, minute), values in sorted(groups.items()):
        powers = [power for power, _ in values]
        intervals = [interval for _, interval in values]
        rows.append({
            "workshop_code": workshop,
            "device_id": device_id,
            "minute": minute.strftime("%Y-%m-%d %H:%M:%S.000"),
            "mean_kw": math.fsum(powers) / len(powers),
            "sample_count": len(powers),
            "unit": "kW",
            "aggregation": "mean_of_unique_device_samples",
            "window": "utc_minute_floor",
            "represented_seconds": max(intervals),
            "duplicate_samples_dropped": duplicates,
        })
    return rows


def rollup_workshop_minutes(device_minutes):
    """Workshop UTC-minute total = sum of device mean_kw present in that minute.

    Does not average flattened device samples. Missing devices contribute nothing
    (not zero). Energy later uses represented_seconds on each row.
    """
    groups = defaultdict(list)
    for row in device_minutes:
        minute = row["minute"]
        workshop = row["workshop_code"]
        groups[(workshop, minute)].append(row)
    rows = []
    for (workshop, minute), devices in sorted(groups.items()):
        total = math.fsum(float(d["mean_kw"]) for d in devices)
        represented = max(int(d["represented_seconds"]) for d in devices)
        rows.append({
            "workshop_code": workshop,
            "minute": minute,
            "total_kw": total,
            "device_count": len(devices),
            "device_ids": sorted(d["device_id"] for d in devices),
            "unit": "kW",
            "aggregation": "sum_of_device_minute_means",
            "window": "utc_minute_floor",
            "represented_seconds": represented,
        })
    return rows


def integrate_workshop_minutes_kwh(workshop_minutes):
    """Left-style energy from workshop minutes: total_kw * represented_seconds / 3600."""
    by_ws = defaultdict(float)
    for row in workshop_minutes:
        seconds = int(row["represented_seconds"])
        power = float(row["total_kw"])
        if seconds < 1 or not math.isfinite(power) or power < 0:
            raise ValueError("Invalid workshop minute row")
        by_ws[row["workshop_code"]] += power * seconds / 3600.0
    return dict(by_ws)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--clickhouse-url", help="Opt-in HTTP endpoint, e.g. http://localhost:8123")
    parser.add_argument("--database", default="energy_poc")
    args = parser.parse_args()
    with args.input.open(encoding="utf-8") as source:
        rows = aggregate([json.loads(line) for line in source if line.strip()])
    payload = "".join(json.dumps(row) + "\n" for row in rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    if args.clickhouse_url:
        insert_rows(rows, args.clickhouse_url, args.database)
