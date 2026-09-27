"""Run unchanged DWD/DWS/ADS SQL over real DataX probe ODS partitions.

The ODS source was populated by verify_isolated_datax_sync.py. This script
creates only missing dimension and downstream tables in that isolated DB.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.check_isolated_lakehouse_replay import (  # noqa: E402
    execute_file, observe, seed_dimensions,
)
from tools.verify_isolated_datax_sync import inspect_partition, probe_names  # noqa: E402


def run(suffix: str, output: Path) -> dict:
    from pyspark.sql import SparkSession

    _, database = probe_names(suffix)
    spark = SparkSession.builder.appName("verify-datax-to-ads").enableHiveSupport().getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    try:
        if not spark.catalog.databaseExists(database):
            raise ValueError(f"missing isolated DataX target database: {database}")
        existing = {table.name for table in spark.catalog.listTables(database)}
        if existing != {"ods_energy_consumption"}:
            raise ValueError(f"refusing non-fresh downstream tables: {sorted(existing)}")
        expected_ods = {
            "2025-12-31": {"1": ("10.00", 0, "2025-12-30 09:00:00"),
                           "2": ("20.00", 0, "2025-12-30 09:00:00")},
            "2026-09-27": {"1": ("17.00", 0, "2026-09-27 11:00:00")},
            "2026-09-28": {"1": ("17.00", 1, "2026-09-28 09:00:00")},
        }
        for batch, expected in expected_ods.items():
            inspect_partition(spark, database, batch, expected)
        for ddl in ("hive/ddl/01_ods.sql", "hive/ddl/02_dwd.sql",
                    "hive/ddl/03_dws_ads.sql"):
            execute_file(spark, ddl, database)
        seed_dimensions(spark, database)
        phases = {}
        for name, batch, cost, deleted, e01_cost in (
            ("snapshot", "2025-12-31", Decimal("30.00"), 0, Decimal("10.00")),
            ("corrected", "2026-09-27", Decimal("37.00"), 0, Decimal("17.00")),
            ("deleted", "2026-09-28", Decimal("20.00"), 1, Decimal("17.00")),
        ):
            # Process each ODS sync partition in order. The initial DataX
            # snapshot is read by its partition instead of rescanning later
            # deltas already present in the probe table.
            for sql in ("spark/sql/10_dwd_energy.sql", "spark/sql/20_dws.sql",
                        "spark/sql/30_ads.sql"):
                execute_file(spark, sql, database, batch=batch, mode="incremental")
            phases[name] = observe(spark, database, batch, cost, deleted, e01_cost)
            print(f"DATAX_TO_ADS phase={name} PASS", flush=True)
        report = {
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "database": database, "spark_version": spark.version,
            "master": spark.sparkContext.master,
            "ods_source": "real DataX sync from isolated MySQL database",
            "sql_mode": "incremental by ODS partition, including initial snapshot",
            "phases": phases, "success": True,
            "scope": "isolated MySQL→DataX→HDFS/Hive ODS→DWD→DWS→ADS; no Airflow or production writes",
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
        return report
    finally:
        spark.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suffix", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.suffix, args.output)
    print(f"DATAX_TO_ADS PASS report={args.output} phases={len(report['phases'])}")


if __name__ == "__main__":
    main()
