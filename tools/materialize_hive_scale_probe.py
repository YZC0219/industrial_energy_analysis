"""Materialize an isolated ODS→DWD→DWS→ADS scale experiment on Hive.

Production databases are read-only. Every output is confined to a new,
explicitly named probe database; a resumed run refuses an existing scale.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

SOURCE = "energy_ods.ods_energy_consumption"
ENERGY_DIM = "energy_ods.ods_energy_type"
TABLES = {
    "ods": "ods_energy_scaled",
    "dwd": "dwd_energy_scaled",
    "dws": "dws_workshop_day_scaled",
    "ads": "ads_plant_day_scaled",
}
BASE_ROWS = 19006
BASE_DAILY_ROWS = 5848
BASE_FACTORY_DAYS = 731
BASE_COST = Decimal("215364002.57")


def validate_database_name(name: str) -> str:
    if not re.fullmatch(r"energy_scale_probe_[0-9]{8}", name):
        raise ValueError("probe database must match energy_scale_probe_YYYYMMDD")
    return name


def expected_counts(scale: int) -> dict[str, int]:
    if scale < 1:
        raise ValueError("scale must be positive")
    return {"ods": BASE_ROWS * scale, "dwd": BASE_ROWS * scale,
            "dws": BASE_DAILY_ROWS * scale, "ads": BASE_FACTORY_DAYS * scale}


def _table(database: str, layer: str) -> str:
    return f"{database}.{TABLES[layer]}"


def _create_tables(spark, database: str) -> None:
    spark.sql(f"CREATE DATABASE {database} LOCATION 'hdfs://localhost:9000/warehouse/{database}'")
    spark.sql(f"""CREATE TABLE {_table(database, 'ods')} (
        plant_id INT, source_id BIGINT, record_date DATE, workshop_code STRING,
        energy_code STRING, consumption STRING, cost STRING,
        updated_at TIMESTAMP, is_deleted TINYINT, scale INT
    ) USING ORC PARTITIONED BY (scale)""")
    spark.sql(f"""CREATE TABLE {_table(database, 'dwd')} (
        plant_id INT, record_date DATE, workshop_code STRING, energy_code STRING,
        consumption DECIMAL(16,3), cost DECIMAL(16,2),
        std_coal_kgce DECIMAL(20,3), is_deleted TINYINT, scale INT
    ) USING PARQUET PARTITIONED BY (scale)""")
    spark.sql(f"""CREATE TABLE {_table(database, 'dws')} (
        plant_id INT, record_date DATE, workshop_code STRING,
        cost DECIMAL(20,2), std_coal_kgce DECIMAL(22,3),
        fact_count BIGINT, scale INT
    ) USING PARQUET PARTITIONED BY (scale)""")
    spark.sql(f"""CREATE TABLE {_table(database, 'ads')} (
        plant_id INT, record_date DATE, cost DECIMAL(22,2),
        std_coal_kgce DECIMAL(24,3), workshop_count BIGINT, scale INT
    ) USING PARQUET PARTITIONED BY (scale)""")


def _check_targets(spark, database: str, scale: int, *, resume: bool) -> None:
    exists = spark.catalog.databaseExists(database)
    if exists and not resume:
        raise ValueError(f"probe database already exists: {database}; use --resume for a new scale")
    if not exists and resume:
        raise ValueError(f"cannot resume missing database: {database}")
    if not exists:
        return
    for layer in TABLES:
        table = _table(database, layer)
        if not spark.catalog.tableExists(table):
            raise ValueError(f"cannot resume incomplete probe database: missing {table}")
        if spark.table(table).where(f"scale = {scale}").limit(1).count():
            raise ValueError(f"refusing to overwrite existing {scale}x data in {table}")


def _write_and_check(frame, spark, table: str, *, scale: int,
                     expected_rows: int, expected_cost: Decimal) -> dict:
    from pyspark.sql import functions as F

    columns = spark.table(table).columns
    started = time.perf_counter()
    frame.select(*columns).write.mode("append").insertInto(table)
    write_seconds = round(time.perf_counter() - started, 3)
    check_started = time.perf_counter()
    row = spark.table(table).where(F.col("scale") == scale).agg(
        F.count("*").alias("rows"),
        F.sum(F.col("cost").cast("decimal(24,2)")).alias("cost"),
    ).first()
    observed_rows = int(row["rows"])
    observed_cost = Decimal(str(row["cost"]))
    if (observed_rows, observed_cost) != (expected_rows, expected_cost):
        raise ValueError(f"{table} {scale}x mismatch: "
                         f"observed=({observed_rows},{observed_cost}), "
                         f"expected=({expected_rows},{expected_cost})")
    return {"table": table, "rows": observed_rows, "cost": str(observed_cost),
            "write_seconds": write_seconds,
            "validation_seconds": round(time.perf_counter() - check_started, 3)}


def run(database: str, *, source_dt: str, scale: int, resume: bool,
        output: Path) -> dict:
    from pyspark.sql import SparkSession, Window, functions as F

    validate_database_name(database)
    date.fromisoformat(source_dt)
    if scale not in (10, 100):
        raise ValueError("the materialization probe accepts only 10x or 100x")
    spark = SparkSession.builder.appName(f"hive-scale-probe-{scale}x").enableHiveSupport().getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    try:
        _check_targets(spark, database, scale, resume=resume)
        source = spark.table(SOURCE).where(F.col("dt") == source_dt)
        baseline = source.agg(
            F.count("*").alias("rows"),
            F.sum(F.col("cost").cast("decimal(24,2)")).alias("cost"),
        ).first()
        if (int(baseline["rows"]), Decimal(str(baseline["cost"]))) != (BASE_ROWS, BASE_COST):
            raise ValueError("source snapshot differs from checked-in 1x baseline")
        dim = spark.table(ENERGY_DIM).where(F.col("dt") == "current").select(
            "energy_code", F.col("std_coal_factor").cast("decimal(10,4)").alias("factor")
        )
        if dim.count() != 6 or dim.select("energy_code").distinct().count() != 6:
            raise ValueError("energy dimension must contain six unique codes")
        if not resume:
            _create_tables(spark, database)

        expected = expected_counts(scale)
        expected_cost = BASE_COST * scale
        replicas = F.broadcast(spark.range(scale).select(F.col("id").cast("int").alias("plant_id")))
        ods = source.crossJoin(replicas).select(
            "plant_id", F.col("id").alias("source_id"), "record_date",
            "workshop_code", "energy_code", "consumption", "cost", "updated_at",
            F.coalesce(F.col("is_deleted"), F.lit(0)).cast("tinyint").alias("is_deleted"),
            F.lit(scale).alias("scale"),
        )
        layers = {}
        layers["ods"] = _write_and_check(
            ods, spark, _table(database, "ods"), scale=scale,
            expected_rows=expected["ods"], expected_cost=expected_cost,
        )
        ods_read = spark.table(_table(database, "ods")).where(F.col("scale") == scale)
        window = Window.partitionBy(
            "plant_id", "record_date", "workshop_code", "energy_code"
        ).orderBy(F.col("updated_at").desc(), F.col("source_id").desc())
        clean = ods_read.where(
            (F.col("is_deleted") == 0)
            & (F.col("consumption").cast("decimal(16,3)") >= 0)
            & (F.col("cost").cast("decimal(16,2)") >= 0)
        ).withColumn("version_rank", F.row_number().over(window)).where(
            F.col("version_rank") == 1
        )
        dwd = clean.join(F.broadcast(dim), "energy_code", "inner").select(
            "plant_id", "record_date", "workshop_code", "energy_code",
            F.col("consumption").cast("decimal(16,3)").alias("consumption"),
            F.col("cost").cast("decimal(16,2)").alias("cost"),
            F.round(F.col("consumption").cast("decimal(16,3)") * F.col("factor"), 3)
             .cast("decimal(20,3)").alias("std_coal_kgce"),
            "is_deleted", F.lit(scale).alias("scale"),
        )
        layers["dwd"] = _write_and_check(
            dwd, spark, _table(database, "dwd"), scale=scale,
            expected_rows=expected["dwd"], expected_cost=expected_cost,
        )
        dwd_read = spark.table(_table(database, "dwd")).where(F.col("scale") == scale)
        dws = dwd_read.groupBy("plant_id", "record_date", "workshop_code").agg(
            F.sum("cost").cast("decimal(20,2)").alias("cost"),
            F.sum("std_coal_kgce").cast("decimal(22,3)").alias("std_coal_kgce"),
            F.count("*").alias("fact_count"),
        ).withColumn("scale", F.lit(scale))
        layers["dws"] = _write_and_check(
            dws, spark, _table(database, "dws"), scale=scale,
            expected_rows=expected["dws"], expected_cost=expected_cost,
        )
        dws_read = spark.table(_table(database, "dws")).where(F.col("scale") == scale)
        ads = dws_read.groupBy("plant_id", "record_date").agg(
            F.sum("cost").cast("decimal(22,2)").alias("cost"),
            F.sum("std_coal_kgce").cast("decimal(24,3)").alias("std_coal_kgce"),
            F.countDistinct("workshop_code").alias("workshop_count"),
        ).withColumn("scale", F.lit(scale))
        layers["ads"] = _write_and_check(
            ads, spark, _table(database, "ads"), scale=scale,
            expected_rows=expected["ads"], expected_cost=expected_cost,
        )
        report = {
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "database": database,
            "database_location": f"hdfs://localhost:9000/warehouse/{database}",
            "source_table": SOURCE, "source_dt": source_dt, "scale": scale,
            "spark_version": spark.version, "master": spark.sparkContext.master,
            "shuffle_partitions": spark.conf.get("spark.sql.shuffle.partitions"),
            "source_rows": BASE_ROWS, "source_cost": str(BASE_COST),
            "layers": layers, "success": True,
            "scope": "isolated four-layer materialization; omits production and non-cost metrics; no production table writes",
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
        return report
    finally:
        spark.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=validate_database_name)
    parser.add_argument("--source-dt", default="2025-12-31")
    parser.add_argument("--scale", required=True, type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.database, source_dt=args.source_dt, scale=args.scale,
                 resume=args.resume, output=args.output)
    print(f"HIVE_SCALE_PROBE PASS scale={args.scale} report={args.output}")


if __name__ == "__main__":
    main()
