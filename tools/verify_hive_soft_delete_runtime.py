"""Read-only verification of the deployed Hive soft-delete schema and old facts."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ODS = "energy_ods.ods_energy_consumption"
DWD = "energy_dwd.dwd_energy_consumption_detail"
STAGE = "energy_dwd.dwd_energy_consumption_merge_stage"


def classify_stage_columns(columns: list[str]) -> str:
    if columns.count("target_dt") != 1 or columns.count("is_deleted") != 1:
        raise ValueError("stage requires one target_dt and one is_deleted column")
    target = columns.index("target_dt")
    deleted = columns.index("is_deleted")
    if deleted == target + 1:
        return "legacy_append_is_deleted"
    if target == deleted + 1:
        return "fresh_ddl_is_deleted_first"
    raise ValueError("unexpected merge-stage column order")


def run(*, expected_rows: int | None, expected_cost: Decimal | None,
        output: Path) -> dict:
    from pyspark.sql import SparkSession, functions as F

    spark = SparkSession.builder.appName("verify-hive-soft-delete-runtime").enableHiveSupport().getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    try:
        table_objects = {table: spark.table(table) for table in (ODS, DWD, STAGE)}
        schemas = {table: frame.columns for table, frame in table_objects.items()}
        for table, columns in schemas.items():
            if columns.count("is_deleted") != 1:
                raise ValueError(f"{table} requires exactly one is_deleted column")
            if table_objects[table].schema["is_deleted"].dataType.simpleString() != "tinyint":
                raise ValueError(f"{table}.is_deleted must be TINYINT")
        if table_objects[STAGE].schema["target_dt"].dataType.simpleString() != "string":
            raise ValueError("stage.target_dt must be STRING")
        stage_variant = classify_stage_columns(schemas[STAGE])
        dwd = table_objects[DWD].agg(
            F.count("*").alias("rows"),
            F.sum("cost").alias("cost"),
            F.sum(F.coalesce(F.col("is_deleted"), F.lit(0))).alias("deleted_sum"),
        ).first()
        stage = table_objects[STAGE].agg(
            F.count("*").alias("rows"),
            F.count("target_dt").alias("target_dt_nonnull"),
            F.sum(F.coalesce(F.col("is_deleted"), F.lit(0))).alias("deleted_sum"),
        ).first()
        observed_rows = int(dwd["rows"])
        observed_cost = Decimal(str(dwd["cost"]))
        passed = (
            (expected_rows is None or observed_rows == expected_rows)
            and (expected_cost is None or observed_cost == expected_cost)
            and int(dwd["deleted_sum"] or 0) == 0
            and int(stage["rows"]) == int(stage["target_dt_nonnull"])
            and int(stage["deleted_sum"] or 0) == 0
        )
        report = {
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "spark_version": spark.version,
            "master": spark.sparkContext.master,
            "tables": {table: {"columns": columns,
                               "is_deleted_type": "tinyint"}
                       for table, columns in schemas.items()},
            "stage_schema_variant": stage_variant,
            "dwd": {"rows": observed_rows, "cost": str(observed_cost),
                    "deleted_sum": int(dwd["deleted_sum"] or 0)},
            "stage": {"rows": int(stage["rows"]),
                      "target_dt_nonnull": int(stage["target_dt_nonnull"]),
                      "deleted_sum": int(stage["deleted_sum"] or 0)},
            "expected_dwd_rows": expected_rows,
            "expected_dwd_cost": str(expected_cost) if expected_cost is not None else None,
            "success": passed,
            "scope": "read-only schema and aggregate check; no partition writes",
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
        return report
    finally:
        spark.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-rows", type=int)
    parser.add_argument("--expected-cost", type=Decimal)
    parser.add_argument("--output", type=Path, default=Path("output/hive_soft_delete_runtime.json"))
    args = parser.parse_args()
    report = run(expected_rows=args.expected_rows, expected_cost=args.expected_cost,
                 output=args.output)
    print(f"HIVE_SOFT_DELETE_RUNTIME {'PASS' if report['success'] else 'FAIL'} "
          f"report={args.output}")
    if not report["success"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
