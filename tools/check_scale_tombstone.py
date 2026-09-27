"""Run a tiny Spark regression for latest-version and tombstone ordering."""

import argparse
import json
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

# Keep the documented spark-submit entrypoint independent of PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.materialize_hive_scale_probe import latest_valid_live_rows


def run(output: Path) -> dict:
    from pyspark.sql import Row, SparkSession

    spark = SparkSession.builder.appName("scale-probe-tombstone-regression").getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    try:
        def version(workshop: str, source_id: int, hour: int,
                    is_deleted: int, cost: str) -> Row:
            return Row(
                plant_id=0, record_date=date(2024, 3, 15),
                workshop_code=workshop, energy_code="E01", source_id=source_id,
                updated_at=datetime(2026, 9, 27, hour), is_deleted=is_deleted,
                consumption=Decimal("1.000"), cost=Decimal(cost),
            )

        cases = [
            version("W01", 1, 9, 0, "10.00"),
            version("W01", 2, 10, 1, "0.00"),  # delete hides older live row
            version("W02", 3, 9, 0, "5.00"),
            version("W02", 4, 10, 0, "7.00"),  # same-day update wins
            version("W03", 5, 9, 0, "5.00"),
            version("W03", 6, 10, 0, "-1.00"),  # invalid newest row is not bypassed
            version("W04", 7, 11, 0, "1.00"),
            version("W04", 8, 11, 0, "2.00"),  # source ID breaks timestamp tie
        ]
        observed = {
            row.workshop_code: f"{Decimal(str(row.cost)):.2f}"
            for row in latest_valid_live_rows(spark.createDataFrame(cases)).collect()
        }
        expected = {"W02": "7.00", "W04": "2.00"}
        report = {
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "spark_version": spark.version,
            "master": spark.sparkContext.master,
            "input_versions": len(cases),
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
