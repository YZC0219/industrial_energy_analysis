"""Run a tiny Spark regression for latest-version and tombstone ordering."""

import argparse
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

# Keep the documented spark-submit entrypoint independent of PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.materialize_hive_scale_probe import latest_valid_live_rows


def run(output: Path) -> dict:
    from pyspark.sql import SparkSession

    spark = SparkSession.builder.appName("scale-probe-tombstone-regression").getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    try:
        # SQL VALUES avoids serializing Python Rows on this VM's newer Python
        # while still exercising the same Spark DataFrame transformation.
        cases = spark.sql("""
            SELECT plant_id, CAST(record_date AS DATE) AS record_date,
                   workshop_code, energy_code, source_id,
                   CAST(updated_at AS TIMESTAMP) AS updated_at, is_deleted,
                   CAST(consumption AS DECIMAL(16,3)) AS consumption,
                   CAST(cost AS DECIMAL(16,2)) AS cost
            FROM VALUES
              (0, '2024-03-15', 'W01', 'E01', 1, '2026-09-27 09:00:00', 0, 1.000, 10.00),
              (0, '2024-03-15', 'W01', 'E01', 2, '2026-09-27 10:00:00', 1, 1.000,  0.00),
              (0, '2024-03-15', 'W02', 'E01', 3, '2026-09-27 09:00:00', 0, 1.000,  5.00),
              (0, '2024-03-15', 'W02', 'E01', 4, '2026-09-27 10:00:00', 0, 1.000,  7.00),
              (0, '2024-03-15', 'W03', 'E01', 5, '2026-09-27 09:00:00', 0, 1.000,  5.00),
              (0, '2024-03-15', 'W03', 'E01', 6, '2026-09-27 10:00:00', 0, 1.000, -1.00),
              (0, '2024-03-15', 'W04', 'E01', 7, '2026-09-27 11:00:00', 0, 1.000,  1.00),
              (0, '2024-03-15', 'W04', 'E01', 8, '2026-09-27 11:00:00', 0, 1.000,  2.00)
            AS t(plant_id, record_date, workshop_code, energy_code, source_id,
                 updated_at, is_deleted, consumption, cost)
        """)
        observed = {
            row.workshop_code: f"{Decimal(str(row.cost)):.2f}"
            for row in latest_valid_live_rows(cases).collect()
        }
        expected = {"W02": "7.00", "W04": "2.00"}
        report = {
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "spark_version": spark.version,
            "master": spark.sparkContext.master,
            "input_versions": 8,
            "expected": expected,
            "observed": observed,
            "success": observed == expected,
            "scope": "synthetic read-only Spark DataFrame regression; no Hive writes",
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
        if not report["success"]:
            raise ValueError(f"latest-version/tombstone regression failed: {observed}")
        return report
    finally:
        spark.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.output)
    print(f"SCALE_TOMBSTONE PASS report={args.output} rows={len(report['observed'])}")


if __name__ == "__main__":
    main()
