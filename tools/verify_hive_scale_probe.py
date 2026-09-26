"""Read-only quality audit of the isolated 10x/100x Hive probe tables."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from tools.materialize_hive_scale_probe import (
    BASE_COST, TABLES, expected_counts, validate_database_name,
)

KEYS = {
    "ods": ("plant_id", "source_id"),
    "dwd": ("plant_id", "record_date", "workshop_code", "energy_code"),
    "dws": ("plant_id", "record_date", "workshop_code"),
    "ads": ("plant_id", "record_date"),
}


def expected_layer_cost(scale: int) -> Decimal:
    if scale < 1:
        raise ValueError("scale must be positive")
    return BASE_COST * scale


def run(database: str, scales: tuple[int, ...], output: Path) -> dict:
    from pyspark.sql import SparkSession, functions as F

    validate_database_name(database)
    spark = SparkSession.builder.appName("verify-hive-scale-probe").enableHiveSupport().getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    try:
        by_scale = {}
        for scale in scales:
            if scale < 1:
                raise ValueError("scale must be positive")
            expected = expected_counts(scale)
            layers = {}
            for layer, table_name in TABLES.items():
                table = f"{database}.{table_name}"
                frame = spark.table(table).where(F.col("scale") == scale)
                aggregates = [
                    F.count("*").alias("rows"),
                    F.sum(F.col("cost").cast("decimal(24,2)")).alias("cost"),
                    F.countDistinct("plant_id").alias("plant_count"),
                ]
                if layer in ("dwd", "dws", "ads"):
                    aggregates.append(F.sum("std_coal_kgce").alias("std_coal_kgce"))
                if layer == "dws":
                    aggregates.append(F.sum("fact_count").alias("fact_count_sum"))
                if layer == "ads":
                    aggregates.append(F.sum("workshop_count").alias("workshop_count_sum"))
                row = frame.agg(*aggregates).first()
                duplicate_groups = frame.groupBy(*KEYS[layer]).count().where(
                    F.col("count") > 1
                ).limit(1).count()
                null_key_rows = frame.where(
                    " OR ".join(f"{column} IS NULL" for column in KEYS[layer])
                ).limit(1).count()
                item = {
                    "rows": int(row["rows"]),
                    "cost": str(row["cost"]),
                    "plant_count": int(row["plant_count"]),
                    "duplicate_groups_found": bool(duplicate_groups),
                    "null_key_rows_found": bool(null_key_rows),
                }
                if layer in ("dwd", "dws", "ads"):
                    item["std_coal_kgce"] = str(row["std_coal_kgce"])
                if layer == "dws":
                    item["fact_count_sum"] = int(row["fact_count_sum"])
                if layer == "ads":
                    item["workshop_count_sum"] = int(row["workshop_count_sum"])
                layers[layer] = item
            passed = all(
                item["rows"] == expected[layer]
                and Decimal(item["cost"]) == expected_layer_cost(scale)
                and item["plant_count"] == scale
                and not item["duplicate_groups_found"]
                and not item["null_key_rows_found"]
                for layer, item in layers.items()
            )
            passed = passed and (
                Decimal(layers["dwd"]["std_coal_kgce"])
                == Decimal(layers["dws"]["std_coal_kgce"])
                == Decimal(layers["ads"]["std_coal_kgce"])
                and layers["dws"]["fact_count_sum"] == layers["dwd"]["rows"]
                and layers["ads"]["workshop_count_sum"] == layers["dws"]["rows"]
            )
            by_scale[str(scale)] = {"layers": layers, "success": passed}
            print(f"SCALE_QUALITY scale={scale} {'PASS' if passed else 'FAIL'}", flush=True)
        report = {
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "database": database, "spark_version": spark.version,
            "master": spark.sparkContext.master, "scales": by_scale,
            "success": all(item["success"] for item in by_scale.values()),
            "scope": "read-only audit of isolated probe tables; production tables untouched",
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
    parser.add_argument("--scales", default="10,100")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    scales = tuple(int(value.strip()) for value in args.scales.split(","))
    if not scales or len(set(scales)) != len(scales) or any(value < 1 for value in scales):
        parser.error("--scales must be distinct positive integers")
    report = run(args.database, scales, args.output)
    print(f"HIVE_SCALE_QUALITY {'PASS' if report['success'] else 'FAIL'} "
          f"report={args.output}")
    if not report["success"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
