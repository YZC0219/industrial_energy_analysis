"""Run real DataX full/incremental/retry sync into isolated MySQL and Hive DBs.

Requires a reachable MySQL server, DataX, HDFS and Spark on the executing VM.
The script refuses existing probe databases; it never writes production tables.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def probe_names(suffix: str) -> tuple[str, str]:
    if not re.fullmatch(r"[0-9]{8}", suffix):
        raise ValueError("suffix must be YYYYMMDD")
    return f"industrial_energy_datax_probe_{suffix}", f"energy_datax_probe_{suffix}"


def mysql_query(sql: str, env: dict[str, str]) -> str:
    command = ["mysql", "--batch", "--skip-column-names", "--protocol=tcp",
               "--host", env["MYSQL_HOST"], "--port", env["MYSQL_PORT"],
               "--user", env["MYSQL_USER"]]
    result = subprocess.run(command, input=sql, text=True, capture_output=True,
                            check=True, env={**env, "MYSQL_PWD": env["MYSQL_PASSWORD"]})
    return result.stdout.strip()


def source_ddl(mysql_db: str) -> str:
    if not re.fullmatch(r"industrial_energy_datax_probe_[0-9]{8}", mysql_db):
        raise ValueError("refusing non-probe MySQL schema")
    return f"""
        CREATE DATABASE {mysql_db};
        CREATE TABLE {mysql_db}.fact_energy_consumption (
            id BIGINT PRIMARY KEY, record_date DATE NOT NULL,
            workshop_code VARCHAR(20) NOT NULL, energy_code VARCHAR(20) NOT NULL,
            consumption DECIMAL(16,3), unit VARCHAR(20), unit_price DECIMAL(12,4),
            cost DECIMAL(16,2), record_status VARCHAR(20),
            avg_temperature DECIMAL(6,2), data_source VARCHAR(50),
            is_production_day TINYINT, updated_at DATETIME(6) NOT NULL,
            is_deleted TINYINT NOT NULL DEFAULT 0
        );
        INSERT INTO {mysql_db}.fact_energy_consumption VALUES
          (1,'2024-03-15','W01','E01',1.000,'unit',10.0000,10.00,'normal',20.00,
           'probe',1,'2025-12-30 09:00:00',0),
          (2,'2024-03-15','W01','E02',2.000,'unit',10.0000,20.00,'normal',20.00,
           'probe',1,'2025-12-30 09:00:00',0);
    """


def create_hive_target(spark, hive_db: str) -> None:
    location = f"hdfs://localhost:9000/warehouse/{hive_db}"
    if spark.catalog.databaseExists(hive_db):
        raise ValueError(f"refusing existing Hive database: {hive_db}")
    fs = spark._jvm.org.apache.hadoop.fs.FileSystem.get(
        spark._jvm.java.net.URI(location), spark._jsc.hadoopConfiguration()
    )
    if fs.exists(spark._jvm.org.apache.hadoop.fs.Path(location)):
        raise ValueError(f"refusing existing HDFS directory: {location}")
    spark.sql(f"CREATE DATABASE {hive_db} LOCATION '{location}'")
    source = (ROOT / "hive/ddl/01_ods.sql").read_text(encoding="utf-8")
    ddl = source.split(";", 1)[0]
    ddl = "\n".join(line for line in ddl.splitlines()
                    if not line.lstrip().startswith("--"))
    old_table = "energy_ods.ods_energy_consumption"
    old_location = "LOCATION '/warehouse/energy_ods/ods_energy_consumption'"
    if ddl.count(old_table) != 1 or ddl.count(old_location) != 1:
        raise ValueError("production ODS DDL changed; refusing unsafe rewrite")
    ddl = ddl.replace(old_table, f"{hive_db}.ods_energy_consumption")
    ddl = ddl.replace(old_location,
                      f"LOCATION '{location}/ods_energy_consumption'")
    spark.sql(ddl)


def sync(env: dict[str, str], hive_db: str, *, batch: str,
         window_start: str = "", window_end: str = "", full: bool = False) -> None:
    command = [sys.executable, str(ROOT / "datax/run_sync.py"), "--table",
               "fact_energy_consumption", "--biz-date", batch,
               "--probe-database", hive_db]
    if full:
        command.append("--full")
    else:
        command.extend(["--window-start", window_start, "--window-end", window_end])
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def inspect_partition(spark, hive_db: str, batch: str,
                      expected: dict[str, tuple[str, int, str]]) -> dict:
    from pyspark.sql import functions as F

    frame = spark.table(f"{hive_db}.ods_energy_consumption").where(F.col("dt") == batch)
    rows = frame.select("id", "cost", "is_deleted", "updated_at").collect()
    actual = {
        str(row.id): (f"{Decimal(str(row.cost)):.2f}", int(row.is_deleted),
                      str(row.updated_at))
        for row in rows
    }
    if len(rows) != len(expected) or actual != expected:
        raise ValueError(f"ODS partition {batch} mismatch: {actual} != {expected}")
    return {"partition": batch, "rows": len(rows),
            "versions": {key: {"cost": cost, "is_deleted": deleted,
                               "updated_at": timestamp}
                         for key, (cost, deleted, timestamp) in actual.items()},
            "success": True}


def inspect_with_spark(hive_db: str, batch: str,
                       expected: dict[str, tuple[str, int, str]]) -> dict:
    from pyspark.sql import SparkSession

    spark = SparkSession.builder.appName("verify-isolated-datax-sync").enableHiveSupport().getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    try:
        return inspect_partition(spark, hive_db, batch, expected)
    finally:
        spark.stop()


def run(suffix: str, output: Path) -> dict:
    from pyspark.sql import SparkSession

    mysql_db, hive_db = probe_names(suffix)
    host = os.environ["MYSQL_HOST"]
    port = os.environ["MYSQL_PORT"]
    user = os.environ["MYSQL_USER"]
    password = os.environ["MYSQL_PASSWORD"]
    jdbc = f"jdbc:mysql://{host}:{port}/{mysql_db}"
    env = {**os.environ, "MYSQL_HOST": host, "MYSQL_PORT": port,
           "MYSQL_USER": user, "MYSQL_PASSWORD": password,
           "MYSQL_JDBC_URL": jdbc, "HDFS_DEFAULT_FS": "hdfs://localhost:9000",
           "HIVE_STAGE_PATH": f"/warehouse/{hive_db}",
           "HDFS_BIN": "/usr/local/hadoop/bin/hdfs",
           "SPARK_SQL": "/usr/local/spark/bin/spark-sql",
           "DATAX_ENTRY": "/home/yzc/apps/datax/bin/datax.py",
           "DATAX_PYTHON": sys.executable,
           "HADOOP_CONF_DIR": "/usr/local/hadoop/etc/hadoop"}
    exists = mysql_query(
        f"SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='{mysql_db}';",
        env,
    )
    if exists:
        raise ValueError(f"refusing existing MySQL database: {mysql_db}")
    spark = SparkSession.builder.appName("create-isolated-datax-target").enableHiveSupport().getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    try:
        create_hive_target(spark, hive_db)
    finally:
        spark.stop()
    mysql_query(source_ddl(mysql_db), env)

    full_expected = {
        "1": ("10.00", 0, "2025-12-30 09:00:00"),
        "2": ("20.00", 0, "2025-12-30 09:00:00"),
    }
    sync(env, hive_db, batch="2025-12-31", full=True)
    phases = {"full": inspect_with_spark(hive_db, "2025-12-31", full_expected)}
    print("DATAX_PROBE phase=full PASS", flush=True)

    mysql_query(f"""UPDATE {mysql_db}.fact_energy_consumption
        SET consumption=1.700,cost=17.00,updated_at='2026-09-27 11:00:00'
        WHERE id=1;""", env)
    corrected = {"1": ("17.00", 0, "2026-09-27 11:00:00")}
    sync(env, hive_db, batch="2026-09-27",
         window_start="2026-09-27 10:00:00", window_end="2026-09-27 12:00:00")
    phases["corrected"] = inspect_with_spark(hive_db, "2026-09-27", corrected)
    print("DATAX_PROBE phase=corrected PASS", flush=True)

    mysql_query(f"""UPDATE {mysql_db}.fact_energy_consumption
        SET is_deleted=1,updated_at='2026-09-28 09:00:00' WHERE id=1;""", env)
    deleted = {"1": ("17.00", 1, "2026-09-28 09:00:00")}
    sync(env, hive_db, batch="2026-09-28",
         window_start="2026-09-28 08:00:00", window_end="2026-09-28 10:00:00")
    phases["deleted"] = inspect_with_spark(hive_db, "2026-09-28", deleted)
    print("DATAX_PROBE phase=deleted PASS", flush=True)

    sync(env, hive_db, batch="2026-09-28",
         window_start="2026-09-28 08:00:00", window_end="2026-09-28 10:00:00")
    phases["retry"] = inspect_with_spark(hive_db, "2026-09-28", deleted)
    print("DATAX_PROBE phase=retry PASS", flush=True)
    report = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "mysql_database": mysql_db, "hive_database": hive_db,
        "mysql_host": host, "mysql_port": port,
        "hdfs_location": f"hdfs://localhost:9000/warehouse/{hive_db}",
        "phases": phases, "success": True,
        "scope": "real DataX MySQL-to-HDFS/Hive sync in isolated source and target DBs; Airflow and production tables untouched",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suffix", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.suffix, args.output)
    print(f"DATAX_ISOLATED_SYNC PASS report={args.output} phases={len(report['phases'])}")


if __name__ == "__main__":
    main()
