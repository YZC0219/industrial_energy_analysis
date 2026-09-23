"""Render and run a DataX MySQL→HDFS job without putting passwords in source control."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TABLES = {
    "fact_energy_consumption": ("ods_energy_consumption", "incremental", [
        ("id","bigint"),("record_date","date"),("workshop_code","string"),("energy_code","string"),
        ("consumption","double"),("unit","string"),("unit_price","double"),("cost","double"),
        ("record_status","string"),("avg_temperature","double"),("data_source","string"),
        ("is_production_day","tinyint"),("updated_at","timestamp")]),
    "fact_production": ("ods_production", "full", [("record_date","date"),("workshop_code","string"),("output_qty","double"),("output_unit","string")]),
    "dim_workshop": ("ods_workshop", "full", [("workshop_code","string"),("workshop_name","string"),("process_type","string"),("is_continuous","tinyint"),("output_unit","string"),("area_m2","double"),("manager","string")]),
    "dim_energy_type": ("ods_energy_type", "full", [("energy_code","string"),("energy_name","string"),("unit","string"),("std_coal_factor","double"),("co2_factor","double"),("reference_price","double")]),
    "dim_calendar": ("ods_calendar", "full", [("calendar_date","date"),("year","smallint"),("quarter","tinyint"),("month","tinyint"),("year_month","string"),("day_of_week","tinyint"),("weekday_name","string"),("is_weekend","tinyint"),("holiday_name","string"),("is_holiday","tinyint")]),
}


def render(text: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        text = text.replace("${" + key + "}", value)
    unresolved = [part.split("}", 1)[0] for part in text.split("${")[1:]]
    if unresolved:
        raise ValueError(f"未提供 DataX 参数: {unresolved}")
    json.loads(text)
    return text


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--table", choices=TABLES, required=True)
    p.add_argument("--biz-date", required=True)
    p.add_argument("--window-start")
    p.add_argument("--window-end")
    p.add_argument("--full", action="store_true", help="能耗事实首次初始化时使用全量抽取")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    target, default_mode, columns = TABLES[args.table]
    mode = "full" if args.full else default_mode
    if args.full and args.table != "fact_energy_consumption":
        p.error("--full 只用于能耗事实首次初始化；其他表本身已是全量快照")
    if mode == "incremental" and (not args.window_start or not args.window_end):
        p.error("增量同步必须同时提供 --window-start 与 --window-end")
    vals = {
        "MYSQL_USER": os.environ["MYSQL_USER"],
        "MYSQL_PASSWORD": os.environ["MYSQL_PASSWORD"],
        "MYSQL_JDBC_URL": os.getenv("MYSQL_JDBC_URL", "jdbc:mysql://mysql:3306/industrial_energy"),
        "HDFS_DEFAULT_FS": os.getenv("HDFS_DEFAULT_FS", "hdfs://namenode:8020"),
        "HIVE_STAGE_PATH": os.getenv("HIVE_STAGE_PATH", "/warehouse/energy_ods"),
        "SOURCE_TABLE": args.table, "TARGET_TABLE": target, "BIZ_DATE": args.biz_date,
        "TARGET_PARTITION": args.biz_date if args.table == "fact_energy_consumption" else "current",
        "WINDOW_START": args.window_start or "1970-01-01 00:00:00",
        "WINDOW_END": args.window_end or "2999-12-31 00:00:00",
        "TARGET_COLUMNS": json.dumps([{"name": n, "type": t} for n, t in columns]),
    }
    template = ROOT / "datax" / "jobs" / f"mysql_to_hive_{mode}.json"
    payload = render(template.read_text(encoding="utf-8"), vals)
    if args.dry_run:
        print(json.dumps(json.loads(payload), ensure_ascii=False, indent=2).replace(vals["MYSQL_PASSWORD"], "***"))
        return
    with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8", delete=False) as f:
        f.write(payload); job_path = f.name
    try:
        subprocess.run([os.getenv("DATAX_PYTHON", "python"), os.getenv("DATAX_ENTRY", "/opt/datax/bin/datax.py"), job_path], check=True)
        table = f"energy_ods.{target}"
        partition = vals["TARGET_PARTITION"]
        location = f"{vals['HIVE_STAGE_PATH']}/{target}/dt={partition}"
        subprocess.run([os.getenv("SPARK_SQL", "spark-sql"), "-e",
                        f"ALTER TABLE {table} DROP IF EXISTS PARTITION(dt='{partition}'); "
                        f"ALTER TABLE {table} ADD PARTITION(dt='{partition}') LOCATION '{location}'"], check=True)
    finally:
        Path(job_path).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
