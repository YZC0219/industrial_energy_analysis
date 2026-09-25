"""Read-only binlog preflight for the local Compose MySQL, not a CDC connector.

Only SHOW and information_schema SELECT statements are issued. A passing report
does not mean physical DELETE events are already published or reconciled.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VARIABLES_SQL = (
    "SHOW VARIABLES WHERE Variable_name IN "
    "('log_bin','binlog_format','binlog_row_image','server_id',"
    "'binlog_expire_logs_seconds','gtid_mode')"
)
PRIMARY_KEY_SQL = (
    "SELECT COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE "
    "WHERE TABLE_SCHEMA='industrial_energy' "
    "AND TABLE_NAME='fact_energy_consumption' "
    "AND CONSTRAINT_NAME='PRIMARY' ORDER BY ORDINAL_POSITION"
)
COLUMNS_SQL = (
    "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
    "WHERE TABLE_SCHEMA='industrial_energy' "
    "AND TABLE_NAME='fact_energy_consumption'"
)
BINLOG_STATUS_SQL = "SHOW MASTER STATUS"
REQUIRED_COLUMNS = {"id", "record_date", "workshop_id", "energy_id", "updated_at"}


def _query(sql: str) -> list[list[str]]:
    # The password stays inside the container environment, not the host command line.
    command = [
        "docker", "compose", "exec", "-T", "mysql", "sh", "-lc",
        'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot --batch --raw '
        '--skip-column-names -e "$1"',
        "mysql-cdc-preflight", sql,
    ]
    result = subprocess.run(command, cwd=ROOT, check=True, capture_output=True,
                            text=True, encoding="utf-8", timeout=30)
    return [line.split("\t") for line in result.stdout.splitlines() if line.strip()]


def inspect() -> dict:
    variables = dict(_query(VARIABLES_SQL))
    primary_key = [row[0] for row in _query(PRIMARY_KEY_SQL)]
    columns = {row[0] for row in _query(COLUMNS_SQL)}
    status = _query(BINLOG_STATUS_SQL)
    binlog_file = status[0][0] if status else None
    binlog_position = int(status[0][1]) if status and len(status[0]) > 1 else None
    checks = {
        "binary_logging_enabled": variables.get("log_bin", "").upper() == "ON",
        "row_format": variables.get("binlog_format", "").upper() == "ROW",
        "full_row_image": variables.get("binlog_row_image", "").upper() == "FULL",
        "nonzero_server_id": variables.get("server_id", "0").isdigit()
        and int(variables.get("server_id", "0")) > 0,
        "fact_primary_key_id": primary_key == ["id"],
        "required_fact_columns": REQUIRED_COLUMNS <= columns,
        "binlog_position_available": bool(binlog_file and binlog_position),
    }
    return {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "local Docker Compose MySQL; read-only preflight",
        "variables": variables,
        "fact_primary_key": primary_key,
        "required_fact_columns_present": sorted(REQUIRED_COLUMNS & columns),
        "binlog_file": binlog_file,
        "binlog_position": binlog_position,
        "checks": checks,
        "binlog_capture_prerequisites_met": all(checks.values()),
        "physical_delete_cdc_implemented": False,
        "full_snapshot_and_stream_verified": False,
        "version_warning": (
            "A physical DELETE does not update the deleted row's updated_at. "
            "Reconciliation needs binlog order or an independent monotonic source version; "
            "updated_at alone is not a valid delete ordering key."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "output" / "mysql_cdc_readiness.json")
    args = parser.parse_args(argv)
    try:
        report = inspect()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired,
            ValueError) as exc:
        report = {"binlog_capture_prerequisites_met": False,
                  "physical_delete_cdc_implemented": False,
                  "error": f"{type(exc).__name__}: {exc}"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(f"MYSQL_CDC_PREFLIGHT {'PASS' if report['binlog_capture_prerequisites_met'] else 'FAIL'} "
          f"report={args.output}")
    return 0 if report["binlog_capture_prerequisites_met"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
