"""Replay actual Kafka CDC probe evidence into isolated Hive/Spark tables.

Requires a successful verify_mysql_cdc_probe report including raw row images.
This validates a separate warehouse projection, not the production DWD/DWS DAG.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path


def run(evidence: Path, database: str, output: Path) -> dict:
    from pyspark.sql import SparkSession, functions as F, types as T

    if not re.fullmatch(r"energy_cdc_probe_[0-9]{14}", database):
        raise ValueError("Use a fresh energy_cdc_probe_YYYYMMDDHHMMSS database")
    if output.exists():
        raise FileExistsError("Refusing to overwrite warehouse evidence")
    raw = evidence.read_bytes()
    report = json.loads(raw)
    if not report.get("success") or report.get("source_rows_after_delete") != 0:
        raise ValueError("Source evidence did not verify physical deletion")
    events = report["events"]
    if [event["kind"] for event in events] != ["insert", "delete", "tombstone"]:
        raise ValueError("Expected exactly insert/delete/tombstone in Kafka order")
    if len({event["partition"] for event in events}) != 1:
        raise ValueError("This probe requires one Kafka partition for ordering")
    offsets = [event["offset"] for event in events]
    if offsets != sorted(set(offsets)):
        raise ValueError("Kafka offsets are not strictly ordered")
    row_id = report["probe_id"]
    marker = report["marker"]
    rows = []
    for event in events:
        if event["kind"] == "tombstone":
            continue
        image = event["after"] if event["kind"] == "insert" else event["before"]
        if not image or image["id"] != row_id or image["marker"] != marker:
            raise ValueError("Actual CDC row image does not match source probe")
        source = event["source"]
        if not source or not source.get("file") or source.get("pos") is None:
            raise ValueError("Actual CDC binlog position is missing")
        rows.append((row_id, marker, event["op"], event["partition"], event["offset"],
                     source["file"], source["pos"], source.get("row") or 0))
    schema = T.StructType([
        T.StructField("id", T.LongType(), False),
        T.StructField("marker", T.StringType(), False),
        T.StructField("op", T.StringType(), False),
        T.StructField("kafka_partition", T.IntegerType(), False),
        T.StructField("kafka_offset", T.LongType(), False),
        T.StructField("binlog_file", T.StringType(), False),
        T.StructField("binlog_position", T.LongType(), False),
        T.StructField("binlog_row", T.LongType(), False),
    ])
    spark = SparkSession.builder.appName("real-cdc-hive-projection").enableHiveSupport().getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    location = f"hdfs://localhost:9000/warehouse/{database}"
    try:
        if spark.catalog.databaseExists(database):
            raise ValueError("Refusing to reuse an existing Hive database")
        fs = spark._jvm.org.apache.hadoop.fs.FileSystem.get(
            spark._jvm.java.net.URI(location), spark._jsc.hadoopConfiguration())
        if fs.exists(spark._jvm.org.apache.hadoop.fs.Path(location)):
            raise ValueError("Refusing to reuse an existing HDFS directory")
        spark.sql(f"CREATE DATABASE {database} LOCATION '{location}'")
        columns = "id BIGINT,marker STRING,op STRING,kafka_partition INT,kafka_offset BIGINT,binlog_file STRING,binlog_position BIGINT,binlog_row BIGINT"
        spark.sql(f"CREATE TABLE {database}.ods_cdc_events ({columns}) STORED AS ORC")
        spark.sql(f"CREATE TABLE {database}.dwd_cdc_current ({columns},is_deleted INT) STORED AS ORC")
        spark.sql(f"CREATE TABLE {database}.dws_cdc_totals (active_rows BIGINT,deleted_rows BIGINT) STORED AS ORC")

        def materialize(name: str, incoming: list[tuple], expected_active: int) -> dict:
            spark.createDataFrame(incoming, schema).write.mode("append").insertInto(f"{database}.ods_cdc_events")
            spark.sql(f"""INSERT OVERWRITE TABLE {database}.dwd_cdc_current
                SELECT id,marker,op,kafka_partition,kafka_offset,binlog_file,binlog_position,binlog_row,
                       CASE WHEN op='d' THEN 1 ELSE 0 END
                FROM (SELECT *,row_number() OVER (PARTITION BY id ORDER BY kafka_offset DESC) AS rn
                      FROM {database}.ods_cdc_events) ranked WHERE rn=1""")
            spark.sql(f"""INSERT OVERWRITE TABLE {database}.dws_cdc_totals
                SELECT sum(CASE WHEN is_deleted=0 THEN 1 ELSE 0 END),
                       sum(CASE WHEN is_deleted=1 THEN 1 ELSE 0 END)
                FROM {database}.dwd_cdc_current""")
            totals = spark.table(f"{database}.dws_cdc_totals").collect()[0]
            detail = spark.table(f"{database}.dwd_cdc_current").collect()
            if len(detail) != 1 or totals.active_rows != expected_active:
                raise ValueError(f"{name}: warehouse active-row count differs")
            if detail[0].id != row_id or detail[0].is_deleted != 1 - expected_active:
                raise ValueError(f"{name}: warehouse deletion state differs")
            return {"active_rows": totals.active_rows, "deleted_rows": totals.deleted_rows,
                    "latest_kafka_offset": detail[0].kafka_offset,
                    "ods_event_rows": spark.table(f"{database}.ods_cdc_events").count(),
                    "success": True}

        phases = {"insert": materialize("insert", rows[:1], 1),
                  "physical_delete": materialize("physical_delete", rows[1:], 0),
                  "full_replay": materialize("full_replay", rows, 0)}
        result = {"checked_at_utc": datetime.now(timezone.utc).isoformat(),
                  "success": True, "database": database, "location": location,
                  "spark_version": spark.version, "source_topic": report["topic"],
                  "source_evidence_sha256": hashlib.sha256(raw).hexdigest(),
                  "phases": phases,
                  "scope": "actual MySQL/Debezium/Kafka evidence -> isolated Hive ODS/DWD/DWS projection; production DAG not connected",
                  "ordering": "one source topic partition; Kafka offset, not deleted row updated_at"}
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return result
    finally:
        spark.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.evidence, args.database, args.output)
    print(f"CDC_HIVE_PROJECTION PASS report={args.output}")


if __name__ == "__main__":
    main()
