"""Verify real MySQL hard DELETE -> Debezium/Kafka -> isolated SQLite state.

Only the industrial_energy_cdc_probe database is modified. The project fact
table, energy-events, Flink jobs and Hive tables are never written by this tool.
Run --prepare before starting debezium-connect, then --run after it is healthy.
"""
from __future__ import annotations

import argparse
import json
import secrets
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError

from tools.register_mysql_cdc import CONNECTOR_CONFIG, request_json

ROOT = Path(__file__).resolve().parents[1]
SECRET_FILE = ROOT / "cdc" / "secrets" / "connect-secrets.properties"
PROBE_DB = "industrial_energy_cdc_probe"
PROBE_TOPIC = "energy-cdc-probe.industrial_energy_cdc_probe.fact_energy_consumption"
PROBE_CONNECTOR = "energy-mysql-cdc-probe"
PROBE_PASSWORD_KEY = "probe-password"
COMPOSE = ["docker", "compose", "-f", "docker-compose.yml", "-f", "docker-compose.cdc.yml"]


def mysql(sql: str) -> str:
    command = [*COMPOSE, "exec", "-T", "mysql", "sh", "-lc",
               'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot --batch --raw --skip-column-names']
    result = subprocess.run(command, cwd=ROOT, input=sql, text=True,
                            capture_output=True, encoding="utf-8", timeout=30)
    if result.returncode:
        if "IDENTIFIED BY" in sql:
            raise RuntimeError("MySQL account setup failed; SQL stderr was suppressed to protect the password")
        raise RuntimeError(f"MySQL command failed: {result.stderr.strip()}")
    return result.stdout.strip()


def secret() -> str:
    SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    if SECRET_FILE.exists():
        content = SECRET_FILE.read_text(encoding="utf-8")
        matches = [line.split("=", 1)[1].strip() for line in content.splitlines()
                   if line.startswith(PROBE_PASSWORD_KEY + "=")]
        if len(matches) > 1 or (matches and not matches[0]):
            raise ValueError("probe-password must appear exactly once with a value")
        if matches:
            return matches[0]
        password = secrets.token_hex(24)
        with SECRET_FILE.open("a", encoding="utf-8") as output:
            output.write("\n" + PROBE_PASSWORD_KEY + "=" + password + "\n")
        return password
    password = secrets.token_hex(24)
    with SECRET_FILE.open("x", encoding="utf-8") as output:
        output.write("db-password=not-configured\n" + PROBE_PASSWORD_KEY + "=" + password + "\n")
    return password


def prepare() -> dict:
    variables = dict(line.split("\t", 1) for line in mysql(
        "SHOW VARIABLES WHERE Variable_name IN "
        "('log_bin','binlog_format','binlog_row_image','server_id');"
    ).splitlines())
    if (variables.get("log_bin", "").upper() != "ON"
            or variables.get("binlog_format", "").upper() != "ROW"
            or variables.get("binlog_row_image", "").upper() != "FULL"
            or int(variables.get("server_id", "0")) <= 0):
        raise RuntimeError(f"MySQL CDC binlog prerequisites failed: {variables}")
    password = secret()
    # The password is hexadecimal and the SQL travels over stdin, not argv.
    mysql(f"""
        CREATE DATABASE IF NOT EXISTS {PROBE_DB};
        CREATE TABLE IF NOT EXISTS {PROBE_DB}.fact_energy_consumption (
            id BIGINT NOT NULL PRIMARY KEY,
            marker VARCHAR(64) NOT NULL,
            updated_at DATETIME(6) NOT NULL
        ) ENGINE=InnoDB;
        CREATE USER IF NOT EXISTS 'energy_cdc_probe'@'%' IDENTIFIED BY '{password}';
        ALTER USER 'energy_cdc_probe'@'%' IDENTIFIED BY '{password}';
        GRANT SELECT ON {PROBE_DB}.* TO 'energy_cdc_probe'@'%';
        GRANT RELOAD, SHOW DATABASES, REPLICATION SLAVE, REPLICATION CLIENT
          ON *.* TO 'energy_cdc_probe'@'%';
    """)
    return {"prepared": True, "database": PROBE_DB, "topic": PROBE_TOPIC,
            "binlog_variables": variables, "secret_file": str(SECRET_FILE)}


def connector_config() -> dict:
    return {**CONNECTOR_CONFIG,
            "database.user": "energy_cdc_probe",
            "database.password": "${file:/opt/connect-secrets.properties:probe-password}",
            "database.server.id": "184055",
            "topic.prefix": "energy-cdc-probe",
            "database.include.list": PROBE_DB,
            "table.include.list": PROBE_DB + ".fact_energy_consumption",
            "schema.history.internal.kafka.topic": "energy-cdc-probe-schema-history"}


def wait_connector(connect_url: str, timeout_seconds: int) -> dict:
    base = connect_url.rstrip("/") + "/connectors/" + PROBE_CONNECTOR
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            request_json(base + "/config", "PUT", connector_config())
            break
        except HTTPError as exc:
            if exc.code not in (409, 503):
                raise
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Kafka Connect REST stayed at HTTP {exc.code}")
            time.sleep(2)
        except URLError:
            if time.monotonic() >= deadline:
                raise TimeoutError("Kafka Connect REST did not become available")
            time.sleep(2)
    last = {}
    while time.monotonic() < deadline:
        last = request_json(base + "/status", "GET")
        if (last.get("connector", {}).get("state") == "RUNNING"
                and last.get("tasks")
                and all(task.get("state") == "RUNNING" for task in last["tasks"])):
            return last
        if any(task.get("state") == "FAILED" for task in last.get("tasks", [])):
            raise RuntimeError(f"CDC task failed: {last}")
        time.sleep(2)
    raise TimeoutError(f"CDC connector did not become RUNNING: {last}")


def decode(raw: bytes | None) -> dict | None:
    if raw is None:
        return None
    value = json.loads(raw)
    if isinstance(value, dict) and "payload" in value and "op" not in value:
        return value["payload"]
    return value


def consume_until(consumer, row_id: int, marker: str, state: sqlite3.Connection,
                  seen: list[dict], expected: set[str], timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    while not expected.issubset({item["kind"] for item in seen}):
        if time.monotonic() >= deadline:
            raise TimeoutError(f"CDC events missing: {expected - {item['kind'] for item in seen}}")
        for records in consumer.poll(timeout_ms=1000, max_records=100).values():
            for record in records:
                key = decode(record.key)
                if not isinstance(key, dict) or key.get("id") != row_id:
                    continue
                value = decode(record.value)
                if value is None:
                    kind = "tombstone"
                else:
                    op = value.get("op")
                    before = value.get("before")
                    after = value.get("after")
                    if op in ("c", "r") and isinstance(after, dict):
                        if after.get("id") != row_id or after.get("marker") != marker:
                            raise ValueError("CDC insert image does not match probe")
                        state.execute("INSERT OR REPLACE INTO projection (id, marker) VALUES (?, ?)",
                                      (row_id, marker))
                        kind = "insert"
                    elif op == "d":
                        if not isinstance(before, dict) or before.get("id") != row_id:
                            raise ValueError("CDC delete before image missing probe id")
                        if before.get("marker") != marker or after is not None:
                            raise ValueError("CDC delete image does not match probe")
                        state.execute("DELETE FROM projection WHERE id=?", (row_id,))
                        kind = "delete"
                    else:
                        continue
                state.commit()
                seen.append({"kind": kind, "partition": record.partition,
                             "offset": record.offset,
                             "source": None if value is None else {
                                 "file": value.get("source", {}).get("file"),
                                 "pos": value.get("source", {}).get("pos"),
                                 "row": value.get("source", {}).get("row")}})


def run(output: Path, *, bootstrap: str, connect_url: str,
        timeout_seconds: int) -> dict:
    from kafka import KafkaConsumer

    if output.exists():
        raise FileExistsError(f"Refusing to overwrite CDC evidence: {output}")
    if not SECRET_FILE.exists() or PROBE_PASSWORD_KEY + "=" not in SECRET_FILE.read_text(encoding="utf-8"):
        raise RuntimeError("Run --prepare before starting Debezium Connect")
    status = wait_connector(connect_url, timeout_seconds)
    row_id = secrets.randbelow(2**62 - 1) + 1
    marker = "cdc-probe-" + secrets.token_hex(12)
    state_path = output.with_suffix(".sqlite")
    if state_path.exists():
        raise FileExistsError(f"Refusing to overwrite downstream state: {state_path}")
    output.parent.mkdir(parents=True, exist_ok=True)
    state = sqlite3.connect(state_path)
    state.execute("CREATE TABLE projection (id INTEGER PRIMARY KEY, marker TEXT NOT NULL)")
    consumer = KafkaConsumer(PROBE_TOPIC, bootstrap_servers=[bootstrap],
                             auto_offset_reset="earliest", enable_auto_commit=False,
                             group_id=None, consumer_timeout_ms=1000)
    seen: list[dict] = []
    inserted = False
    try:
        # First poll establishes assignment before the probe row is committed.
        consumer.poll(timeout_ms=1000)
        mysql(f"INSERT INTO {PROBE_DB}.fact_energy_consumption "
              f"(id, marker, updated_at) VALUES ({row_id}, '{marker}', NOW(6));")
        inserted = True
        consume_until(consumer, row_id, marker, state, seen, {"insert"}, timeout_seconds)
        before_count = state.execute("SELECT COUNT(*) FROM projection WHERE id=?", (row_id,)).fetchone()[0]
        if before_count != 1:
            raise ValueError("Downstream projection did not receive insert")
        mysql(f"DELETE FROM {PROBE_DB}.fact_energy_consumption "
              f"WHERE id={row_id} AND marker='{marker}';")
        inserted = False
        consume_until(consumer, row_id, marker, state, seen,
                      {"insert", "delete", "tombstone"}, timeout_seconds)
        after_count = state.execute("SELECT COUNT(*) FROM projection WHERE id=?", (row_id,)).fetchone()[0]
        source_count = int(mysql(f"SELECT COUNT(*) FROM {PROBE_DB}.fact_energy_consumption "
                                 f"WHERE id={row_id};"))
        success = before_count == 1 and after_count == 0 and source_count == 0
        report = {"checked_at_utc": datetime.now(timezone.utc).isoformat(),
                  "success": success, "scope": "isolated MySQL hard delete -> Debezium/Kafka -> SQLite projection; not Hive/Spark",
                  "source_database": PROBE_DB, "topic": PROBE_TOPIC,
                  "probe_id": row_id, "marker": marker, "connector_status": status,
                  "events": seen, "source_rows_after_delete": source_count,
                  "downstream_rows_before_delete": before_count,
                  "downstream_rows_after_delete": after_count,
                  "downstream_state": str(state_path)}
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return report
    finally:
        consumer.close()
        state.close()
        if inserted:
            # Only the unique probe row created by this run may be cleaned up.
            mysql(f"DELETE FROM {PROBE_DB}.fact_energy_consumption "
                  f"WHERE id={row_id} AND marker='{marker}';")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "output" / "mysql_cdc_probe.json")
    parser.add_argument("--bootstrap", default="127.0.0.1:29092")
    parser.add_argument("--connect-url", default="http://127.0.0.1:8083")
    parser.add_argument("--timeout-seconds", type=int, default=120)
    args = parser.parse_args(argv)
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    try:
        report = prepare() if args.prepare else run(args.output, bootstrap=args.bootstrap,
                                                    connect_url=args.connect_url,
                                                    timeout_seconds=args.timeout_seconds)
    except (OSError, ValueError, RuntimeError, TimeoutError, subprocess.TimeoutExpired,
            HTTPError, URLError) as exc:
        if args.run and not args.output.exists():
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps({
                "checked_at_utc": datetime.now(timezone.utc).isoformat(),
                "success": False,
                "scope": "isolated MySQL CDC probe; failed before complete evidence",
                "error": f"{type(exc).__name__}: {exc}",
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"MYSQL_CDC_PROBE FAIL {type(exc).__name__}: {exc}")
        return 1
    print(f"MYSQL_CDC_PROBE {'PASS' if report.get('success', report.get('prepared')) else 'FAIL'} "
          f"report={args.output if args.run else 'not-written'}")
    return 0 if report.get("success", report.get("prepared")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
