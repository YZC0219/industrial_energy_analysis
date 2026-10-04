"""Finite JSONL minute downsampling with optional ClickHouse HTTP insertion."""
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


def aggregate(points):
    groups, seen = defaultdict(list), set()
    for point in points:
        timestamp = datetime.fromisoformat(point["event_time"])
        power = float(point["power_kw"])
        identity = (point["workshop_code"], point["event_id"])
        if identity in seen or timestamp.tzinfo is None or not math.isfinite(power) or power < 0:
            raise ValueError("Duplicate or invalid sample")
        seen.add(identity)
        minute = timestamp.astimezone(timezone.utc).replace(second=0, microsecond=0)
        groups[(point["workshop_code"], minute)].append(power)
    return [{"workshop_code": workshop, "minute": minute.strftime("%Y-%m-%d %H:%M:%S.000"),
             "mean_kw": math.fsum(values) / len(values), "sample_count": len(values)}
            for (workshop, minute), values in sorted(groups.items())]


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
