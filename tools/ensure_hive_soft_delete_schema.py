"""Idempotently add the soft-delete CDC flag to existing Hive tables."""
from __future__ import annotations

import os
import subprocess


TABLES = (
    "energy_ods.ods_energy_consumption",
    "energy_dwd.dwd_energy_consumption_detail",
    "energy_dwd.dwd_energy_consumption_merge_stage",
)


def ensure_column(table: str, *, run=subprocess.run) -> bool:
    """Return True when a migration ran; fail closed if schema inspection fails."""
    spark_sql = os.getenv("SPARK_SQL", "spark-sql")
    result = run([spark_sql, "--silent", "-e", f"SHOW COLUMNS IN {table}"],
                 check=True, capture_output=True, text=True)
    columns = {line.strip().split("\t", 1)[0].lower()
               for line in result.stdout.splitlines() if line.strip()}
    if "is_deleted" in columns:
        return False
    run([spark_sql, "-e", f"ALTER TABLE {table} ADD COLUMNS (is_deleted TINYINT)"],
        check=True, cwd=os.getcwd())
    return True


def main() -> None:
    migrated = [table for table in TABLES if ensure_column(table)]
    print("HIVE_SOFT_DELETE_SCHEMA " + (", ".join(migrated) if migrated else "up-to-date"))


if __name__ == "__main__":
    main()
