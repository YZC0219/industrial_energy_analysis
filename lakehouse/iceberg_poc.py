"""Isolated Spark/Iceberg late-correction and immutable snapshot experiment.

Launch with spark-submit and a compatible Iceberg runtime jar, not Python alone.
Never uses the production Hive catalog. The warehouse must be explicitly given.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from uuid import uuid4


def run(spark, warehouse):
    spark.conf.set("spark.sql.catalog.energy_poc", "org.apache.iceberg.spark.SparkCatalog")
    spark.conf.set("spark.sql.catalog.energy_poc.type", "hadoop")
    spark.conf.set("spark.sql.catalog.energy_poc.warehouse", warehouse)
    namespace = "audit_" + uuid4().hex
    spark.sql(f"CREATE NAMESPACE energy_poc.{namespace}")
    table = f"energy_poc.{namespace}.meter"
    spark.sql(f"""CREATE TABLE {table} (event_id STRING, record_date DATE,
                  consumption DECIMAL(18,3), updated_at TIMESTAMP)
                  USING iceberg PARTITIONED BY (record_date)""")
    spark.sql(f"""INSERT INTO {table} VALUES
        ('meter-1', DATE '2026-09-27', 100.0, TIMESTAMP '2026-09-27 10:00:00')""")
    original = spark.sql(f"SELECT snapshot_id FROM {table}.snapshots").first()[0]
    merge = f"""MERGE INTO {table} t USING (
        SELECT 'meter-1' event_id, DATE '2026-09-27' record_date,
               CAST(120.0 AS DECIMAL(18,3)) consumption,
               TIMESTAMP '2026-09-30 10:00:00' updated_at) s
        ON t.event_id = s.event_id
        WHEN MATCHED AND s.updated_at > t.updated_at THEN UPDATE SET *
        WHEN NOT MATCHED THEN INSERT *"""
    spark.sql(merge)
    spark.sql(merge)  # same version replay must not double-count
    spark.sql(merge.replace("120.0", "100.0").replace("2026-09-30 10:00:00", "2026-09-27 10:00:00"))
    current = spark.sql(f"SELECT COUNT(*), SUM(consumption) FROM {table}").first()
    historical = spark.sql(f"SELECT SUM(consumption) FROM {table} VERSION AS OF {original}").first()[0]
    if not (current[0] == 1 and float(current[1]) == 120.0 and float(historical) == 100.0):
        raise RuntimeError("Iceberg replay/time-travel audit failed")
    return {"table": table, "original_snapshot": original,
            "current_consumption": float(current[1]), "historical_consumption": float(historical),
            "late_days": 3, "replay_count": current[0], "status": "passed",
            "data_source": "synthetic", "spark_version": spark.version,
            "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "snapshots": [{"snapshot_id": row[0], "parent_id": row[1],
                           "operation": row[2], "committed_at": str(row[3])}
                          for row in spark.sql(f"SELECT snapshot_id,parent_id,operation,committed_at FROM {table}.snapshots ORDER BY committed_at").collect()]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--warehouse", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    from pyspark.sql import SparkSession
    spark = (SparkSession.builder.config("spark.sql.extensions",
             "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions").getOrCreate())
    try:
        result = run(spark, args.warehouse)
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    finally:
        spark.stop()
