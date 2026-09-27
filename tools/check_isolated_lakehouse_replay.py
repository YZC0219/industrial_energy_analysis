"""Exercise the production Spark SQL in an isolated Hive database.

The fixtures model a historical correction, a later tombstone, and a stale
replay. Production tables are never modified. A fresh database name is required.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DATABASES = ("energy_ods", "energy_dwd", "energy_dws", "energy_ads")
BUSINESS_DATE = "2024-03-15"


def validate_database(name: str) -> str:
    if not re.fullmatch(r"energy_(?:contract|datax)_probe_[0-9]{8}", name):
        raise ValueError("database must be a dated contract/datax probe namespace")
    return name


def isolated_sql(sql: str, database: str) -> str:
    """Map only known lakehouse schemas and locations into a fresh probe DB."""
    validate_database(database)
    for source in SOURCE_DATABASES:
        sql = sql.replace(f"{source}.", f"{database}.")
    sql = re.sub(
        r"LOCATION '/warehouse/energy_(?:ods|dwd|dws|ads)/([^']+)'",
        lambda match: f"LOCATION 'hdfs://localhost:9000/warehouse/{database}/{match[1]}'",
        sql,
    )
    if re.search(r"\benergy_(?:ods|dwd|dws|ads)\.", sql):
        raise ValueError("a production table reference survived isolation")
    for location in re.findall(r"\bLOCATION\s+'([^']+)'", sql, flags=re.IGNORECASE):
        if not location.startswith(f"hdfs://localhost:9000/warehouse/{database}/"):
            raise ValueError(f"a non-isolated table location survived: {location}")
    return sql


def execute_file(spark, path: str, database: str, *, batch: str = "",
                 mode: str = "") -> None:
    sql = (ROOT / path).read_text(encoding="utf-8")
    if path == "hive/ddl/02_dwd.sql":
        # Reproduce the VM's legacy ADD COLUMNS physical order in the stage.
        original = "  is_deleted TINYINT,\n  target_dt STRING"
        if sql.count(original) != 1:
            raise ValueError("merge-stage DDL layout changed")
        sql = sql.replace(original, "  target_dt STRING,\n  is_deleted TINYINT")
    sql = isolated_sql(sql, database)
    sql = sql.replace("${biz_date}", batch).replace("${load_mode}", mode)
    sql = "\n".join(line for line in sql.splitlines()
                    if not line.lstrip().startswith("--"))
    for statement in sql.split(";"):
        if statement.strip():
            spark.sql(statement.strip())


def seed_dimensions(spark, db: str) -> None:
    spark.sql(f"""INSERT INTO {db}.ods_workshop PARTITION (dt='current')
        SELECT 'W01','Workshop 1','Process 1',cast(1 AS tinyint),'unit','100','owner'""")
    spark.sql(f"""INSERT INTO {db}.ods_energy_type PARTITION (dt='current')
        SELECT 'E01','Energy 1','unit','1.0000','2.0000','10.0000'
        UNION ALL SELECT 'E02','Energy 2','unit','2.0000','3.0000','10.0000'""")
    spark.sql(f"""INSERT INTO {db}.ods_calendar PARTITION (dt='current')
        SELECT date'{BUSINESS_DATE}',cast(2024 AS smallint),cast(1 AS tinyint),
               cast(3 AS tinyint),'2024-03',cast(5 AS tinyint),'Friday',
               cast(0 AS tinyint),'',cast(0 AS tinyint)""")
    spark.sql(f"""INSERT INTO {db}.ods_production PARTITION (dt='current')
        SELECT date'{BUSINESS_DATE}','W01','100.000','unit'""")


def seed_energy(spark, db: str, batch: str, versions: list[tuple]) -> None:
    rows = []
    for source_id, energy, consumption, cost, updated_at, deleted in versions:
        if not re.fullmatch(r"E0[12]", energy):
            raise ValueError("fixture energy code outside E01/E02")
        if not re.fullmatch(r"[0-9.]+", consumption + cost):
            raise ValueError("fixture numeric literal malformed")
        datetime.fromisoformat(updated_at)
        rows.append(
            f"SELECT {int(source_id)},date'{BUSINESS_DATE}','W01','{energy}',"
            f"'{consumption}','unit','10.0000','{cost}','normal','20.00',"
            f"'fixture',cast(1 AS tinyint),timestamp'{updated_at}',"
            f"cast({int(deleted)} AS tinyint)"
        )
    spark.sql(f"INSERT INTO {db}.ods_energy_consumption PARTITION (dt='{batch}') "
              + " UNION ALL ".join(rows))


def observe(spark, db: str, batch: str, expected_cost: Decimal,
            expected_deleted: int, expected_e01_cost: Decimal) -> dict:
    detail = spark.sql(f"""SELECT energy_code,cost,is_deleted,source_updated_at,dt
        FROM {db}.dwd_energy_consumption_detail WHERE dt='{BUSINESS_DATE}'""").collect()
    by_energy = {row.energy_code: row for row in detail}
    stage = spark.sql(f"""SELECT energy_code,is_deleted,target_dt
        FROM {db}.dwd_energy_consumption_merge_stage WHERE batch_id='{batch}'""").collect()
    day = spark.sql(f"""SELECT cost_yuan FROM {db}.dws_workshop_energy_day
        WHERE dt='{BUSINESS_DATE}'""").collect()
    month = spark.sql(f"""SELECT cost_yuan FROM {db}.dws_workshop_energy_month
        WHERE year_month='2024-03'""").collect()
    factory = spark.sql(f"""SELECT cost_yuan FROM {db}.ads_factory_energy_day
        WHERE dt='{BUSINESS_DATE}'""").collect()
    assert set(by_energy) == {"E01", "E02"}, f"DWD keys differ: {set(by_energy)}"
    assert len(stage) == 2 and all(row.target_dt == BUSINESS_DATE for row in stage)
    assert {row.energy_code: row.is_deleted for row in stage}["E01"] == expected_deleted
    assert int(by_energy["E01"].is_deleted) == expected_deleted
    assert Decimal(str(by_energy["E01"].cost)) == expected_e01_cost
    assert Decimal(str(by_energy["E02"].cost)) == Decimal("20.00")
    for layer, rows in (("dws_day", day), ("dws_month", month), ("ads_day", factory)):
        assert len(rows) == 1, f"{layer}: expected one row, found {len(rows)}"
        assert Decimal(str(rows[0].cost_yuan)) == expected_cost, f"{layer} cost mismatch"
    return {
        "batch": batch,
        "business_date": BUSINESS_DATE,
        "dwd_rows": len(detail),
        "stage_rows": len(stage),
        "e01_cost": str(by_energy["E01"].cost),
        "e01_is_deleted": int(by_energy["E01"].is_deleted),
        "e01_source_updated_at": str(by_energy["E01"].source_updated_at),
        "dws_day_cost": str(day[0].cost_yuan),
        "dws_month_cost": str(month[0].cost_yuan),
        "ads_day_cost": str(factory[0].cost_yuan),
        "success": True,
    }


def run(database: str, output: Path) -> dict:
    from pyspark.sql import SparkSession

    validate_database(database)
    spark = SparkSession.builder.appName("isolated-lakehouse-replay").enableHiveSupport().getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    try:
        if spark.catalog.databaseExists(database):
            raise ValueError(f"refusing to reuse existing database: {database}")
        location = f"hdfs://localhost:9000/warehouse/{database}"
        fs = spark._jvm.org.apache.hadoop.fs.FileSystem.get(
            spark._jvm.java.net.URI(location), spark._jsc.hadoopConfiguration()
        )
        if fs.exists(spark._jvm.org.apache.hadoop.fs.Path(location)):
            raise ValueError(f"refusing to reuse existing HDFS location: {location}")
        spark.sql(f"CREATE DATABASE {database} LOCATION '{location}'")
        for ddl in ("hive/ddl/01_ods.sql", "hive/ddl/02_dwd.sql",
                    "hive/ddl/03_dws_ads.sql"):
            execute_file(spark, ddl, database)
        seed_dimensions(spark, database)
        phases = [
            ("full", "2025-12-31", [
                (1, "E01", "1.000", "10.00", "2025-12-30 09:00:00", 0),
                (2, "E02", "2.000", "20.00", "2025-12-30 09:00:00", 0),
            ], Decimal("30.00"), 0, Decimal("10.00")),
            ("corrected", "2026-09-27", [
                (1, "E01", "1.500", "15.00", "2026-09-27 10:00:00", 0),
                (1, "E01", "1.700", "17.00", "2026-09-27 11:00:00", 0),
            ], Decimal("37.00"), 0, Decimal("17.00")),
            ("deleted", "2026-09-28", [
                (1, "E01", "0.000", "0.00", "2026-09-28 09:00:00", 1),
            ], Decimal("20.00"), 1, Decimal("0.00")),
            ("stale_replay", "2026-09-29", [
                (1, "E01", "1.000", "10.00", "2025-12-30 09:00:00", 0),
            ], Decimal("20.00"), 1, Decimal("0.00")),
        ]
        results = {}
        for name, batch, versions, cost, deleted, e01_cost in phases:
            seed_energy(spark, database, batch, versions)
            mode = "full" if name == "full" else "incremental"
            for sql in ("spark/sql/10_dwd_energy.sql", "spark/sql/20_dws.sql",
                        "spark/sql/30_ads.sql"):
                execute_file(spark, sql, database, batch=batch, mode=mode)
            results[name] = observe(spark, database, batch, cost, deleted, e01_cost)
            print(f"ISOLATED_REPLAY phase={name} PASS", flush=True)
        report = {
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "database": database, "location": location,
            "spark_version": spark.version, "master": spark.sparkContext.master,
            "stage_column_order": "target_dt,is_deleted",
            "phases": results, "success": True,
            "scope": "production SQL rewritten only to isolated database; synthetic ODS fixtures; no DataX or production writes",
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
        return report
    finally:
        spark.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=validate_database)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.database, args.output)
    print(f"ISOLATED_LAKEHOUSE_REPLAY PASS report={args.output} phases={len(report['phases'])}")


if __name__ == "__main__":
    main()
