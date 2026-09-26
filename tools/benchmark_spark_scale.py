"""Read-only single-node Spark scale probe using isolated plant replicas.

This is a compute-scaling experiment, not a 10x/100x warehouse load: it does
not mutate ODS/DWD/DWS/ADS or claim multi-node performance.
"""
from __future__ import annotations

import argparse
import json
import platform
import time
from datetime import datetime, timezone
from pathlib import Path


def parse_scales(value: str) -> tuple[int, ...]:
    scales = tuple(int(part.strip()) for part in value.split(","))
    if not scales or len(set(scales)) != len(scales) or any(scale < 1 for scale in scales):
        raise ValueError("scales must be distinct positive integers")
    if 1 not in scales:
        raise ValueError("scales must include 1 for a same-run baseline")
    return scales


def validate_summary(summary: dict, baseline: dict, scale: int) -> None:
    expected_rows = baseline["fact_rows"] * scale
    expected_daily = baseline["daily_rows"] * scale
    expected_cost = baseline["total_cost"] * scale
    observed = (summary["fact_rows"], summary["daily_rows"], summary["total_cost"])
    expected = (expected_rows, expected_daily, expected_cost)
    if observed != expected:
        raise ValueError(f"scale {scale} mismatch: observed={observed}, expected={expected}")


def run(table: str, scales: tuple[int, ...], trials: int, output: Path) -> list[dict]:
    from pyspark.sql import SparkSession, functions as F

    spark = SparkSession.builder.appName("industrial-energy-scale-probe").enableHiveSupport().getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    try:
        source = spark.table(table)
        has_delete_flag = "is_deleted" in source.columns
        if has_delete_flag:
            source = source.where(F.coalesce(F.col("is_deleted"), F.lit(0)) == 0)
        base = source.select("record_date", "workshop_code", "cost")
        base_daily = base.groupBy("record_date", "workshop_code").agg(
            F.count("*").alias("fact_count"), F.sum("cost").alias("daily_cost")
        )
        reference = base_daily.agg(
            F.sum("fact_count").alias("fact_rows"),
            F.count("*").alias("daily_rows"),
            F.sum("daily_cost").alias("total_cost"),
        ).first()
        if reference is None or not reference["fact_rows"]:
            raise ValueError(f"source table is empty: {table}")
        baseline = {
            "fact_rows": int(reference["fact_rows"]),
            "daily_rows": int(reference["daily_rows"]),
            "total_cost": reference["total_cost"],
        }

        records = []
        for trial in range(1, trials + 1):
            # Rotate order to avoid always rewarding the last scale with a warm cache.
            ordered = scales[trial - 1:] + scales[:trial - 1]
            for scale in ordered:
                started = time.perf_counter()
                replicas = F.broadcast(spark.range(scale).selectExpr("id AS plant_replica"))
                daily = base.crossJoin(replicas).groupBy(
                    "plant_replica", "record_date", "workshop_code"
                ).agg(F.count("*").alias("fact_count"), F.sum("cost").alias("daily_cost"))
                result = daily.agg(
                    F.sum("fact_count").alias("fact_rows"),
                    F.count("*").alias("daily_rows"),
                    F.sum("daily_cost").alias("total_cost"),
                ).first()
                elapsed = time.perf_counter() - started
                summary = {
                    "fact_rows": int(result["fact_rows"]),
                    "daily_rows": int(result["daily_rows"]),
                    "total_cost": result["total_cost"],
                }
                validate_summary(summary, baseline, scale)
                record = {
                    "experiment": "read_only_plant_replica_aggregate",
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "host": platform.node(),
                    "spark_version": spark.version,
                    "master": spark.sparkContext.master,
                    "driver_memory": spark.sparkContext.getConf().get(
                        "spark.driver.memory", "unspecified"
                    ),
                    "shuffle_partitions": spark.conf.get("spark.sql.shuffle.partitions"),
                    "table": table,
                    "source_has_is_deleted": has_delete_flag,
                    "replica_dimension": "plant_replica (query-local, not persisted)",
                    "trial": trial,
                    "scale": scale,
                    "source_fact_rows": baseline["fact_rows"],
                    "source_daily_rows": baseline["daily_rows"],
                    "source_total_cost": str(baseline["total_cost"]),
                    "fact_rows": summary["fact_rows"],
                    "daily_rows": summary["daily_rows"],
                    "total_cost": str(summary["total_cost"]),
                    "elapsed_seconds": round(elapsed, 3),
                    "validated": True,
                    "scope": "single-node Spark SQL aggregation; no Hive writes or multi-node claim",
                }
                records.append(record)
                print(f"SCALE_PASS scale={scale} trial={trial} seconds={elapsed:.3f}", flush=True)

        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
            encoding="utf-8",
        )
        return records
    finally:
        spark.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", default="energy_dwd.dwd_energy_consumption_detail")
    parser.add_argument("--scales", type=parse_scales, default=(1, 10, 100))
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--output", type=Path, default=Path("output/spark_scale_benchmark.jsonl"))
    args = parser.parse_args()
    if args.trials < 1:
        parser.error("--trials must be positive")
    run(args.table, args.scales, args.trials, args.output)


if __name__ == "__main__":
    main()
