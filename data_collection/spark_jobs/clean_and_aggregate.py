#!/usr/bin/env python3
"""Spark：ODS 展平表 → 字段级清洗 → DWD Parquet + 城市日汇总。

推荐输入：ods/<batch>/flat/hourly_raw.parquet（由 tools/flatten_ods.py 生成）
仍支持 --ods 目录下直接读 parquet。
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

# Windows 本机 Spark：为 Hadoop Shell 提供占位 HADOOP_HOME，避免启动失败
_PROJECT = Path(__file__).resolve().parents[2]
_HADOOP_DUMMY = _PROJECT / "dependencies" / "hadoop_dummy"
(_HADOOP_DUMMY / "bin").mkdir(parents=True, exist_ok=True)
_win = _HADOOP_DUMMY / "bin" / "winutils.exe"
if not _win.exists():
    _win.write_bytes(b"")
os.environ["HADOOP_HOME"] = str(_HADOOP_DUMMY)
os.environ["hadoop.home.dir"] = str(_HADOOP_DUMMY)

from pyspark.sql import SparkSession, Window, functions as F

SHORT_GAP = 3
VALUE_COLS = [
    "temperature_2m",
    "relative_humidity_2m",
    "wind_speed_10m",
    "shortwave_radiation",
    "precipitation",
    "cloud_cover",
    "pm2_5",
    "pm10",
    "nitrogen_dioxide",
    "european_aqi",
    "ozone",
    "carbon_monoxide",
]
PHYSICAL = {
    "temperature_2m": (-50.0, 55.0),
    "relative_humidity_2m": (0.0, 100.0),
    "wind_speed_10m": (0.0, 250.0),
    "shortwave_radiation": (0.0, 1300.0),
    "precipitation": (0.0, 200.0),
    "cloud_cover": (0.0, 100.0),
    "pm2_5": (0.0, 1000.0),
    "pm10": (0.0, 2000.0),
    "nitrogen_dioxide": (0.0, 1000.0),
    "european_aqi": (0.0, 500.0),
    "ozone": (0.0, 800.0),
    "carbon_monoxide": (0.0, 30000.0),
}
BOUNDED_CLIP = {"relative_humidity_2m", "cloud_cover"}
PRECIP = {"precipitation"}
POLLUTION = {
    "pm2_5",
    "pm10",
    "nitrogen_dioxide",
    "european_aqi",
    "ozone",
    "carbon_monoxide",
}


def build_spark(app_name: str, mode: str) -> SparkSession:
    builder = (
        SparkSession.builder.appName(app_name)
        .config("spark.sql.session.timeZone", "Asia/Shanghai")
        .config("spark.sql.shuffle.partitions", "4")
    )
    if mode == "local":
        # 本机演示才绑回环；集群模式若绑 127.0.0.1，Worker 无法回连 BlockManager
        builder = (
            builder.master("local[1]")
            .config("spark.driver.host", "127.0.0.1")
            .config("spark.driver.bindAddress", "127.0.0.1")
            .config("spark.driver.memory", "1g")
            .config("spark.ui.enabled", "false")
            .config("spark.ui.showConsoleProgress", "false")
        )
    else:
        builder = builder.config("spark.driver.bindAddress", "0.0.0.0")
    return builder.getOrCreate()


def _sums(df, cols: list[str]) -> dict[str, int]:
    if not cols:
        return {}
    row = df.agg(*[F.sum(F.col(c)).alias(c) for c in cols]).collect()[0].asDict()
    return {k: int(v or 0) for k, v in row.items()}


def load_input(spark: SparkSession, input_path: str):
    path = input_path.rstrip("/")
    if path.endswith(".parquet") or path.endswith("hourly_raw.parquet"):
        df = spark.read.parquet(path)
    else:
        # 目录：优先 flat parquet
        cand = path + "/flat/hourly_raw.parquet"
        try:
            df = spark.read.parquet(cand)
            path = cand
        except Exception:
            df = spark.read.parquet(path)
    for c in VALUE_COLS:
        if c in df.columns:
            df = df.withColumn(c, F.col(c).cast("double"))
    if "ts_local" in df.columns:
        df = df.withColumn("ts_local", F.to_timestamp("ts_local"))
    return df, path


def clean(df):
    stats: dict = {
        "nulls_before": {},
        "short_gap_filled": {},
        "long_gap_kept_null": {},
        "physical_violations": {},
        "pollution_peaks_marked": {},
        "bounded_clipped": {},
    }
    value_cols = [c for c in VALUE_COLS if c in df.columns]
    df = df.cache()
    before = df.count()
    stats["rows_before_dedupe"] = before
    df = df.dropDuplicates(["city_id", "ts_local"]).cache()
    after = df.count()
    stats["duplicates_removed"] = before - after
    stats["rows_after_dedupe"] = after

    for c in value_cols:
        df = df.withColumn(f"_nb_{c}", F.col(c).isNull().cast("int"))
    nb = _sums(df, [f"_nb_{c}" for c in value_cols])
    stats["nulls_before"] = {c: nb.get(f"_nb_{c}", 0) for c in value_cols}
    df = df.drop(*[f"_nb_{c}" for c in value_cols])

    for c in value_cols:
        lo, hi = PHYSICAL[c]
        bad = F.col(c).isNotNull() & ((F.col(c) < F.lit(lo)) | (F.col(c) > F.lit(hi)))
        if c in BOUNDED_CLIP:
            df = df.withColumn(f"flag_clip_{c}", bad.cast("int")).withColumn(
                c, F.when(F.col(c) < lo, lo).when(F.col(c) > hi, hi).otherwise(F.col(c))
            )
        else:
            df = df.withColumn(f"flag_phys_{c}", bad.cast("int")).withColumn(
                c, F.when(bad, F.lit(None)).otherwise(F.col(c))
            )

    w = Window.partitionBy("city_id").orderBy("ts_local")
    for c in value_cols:
        is_null = F.col(c).isNull().cast("int")
        grp = F.sum(1 - is_null).over(w)
        gap = F.sum(is_null).over(Window.partitionBy("city_id", grp))
        gap = F.when(is_null == 1, gap).otherwise(F.lit(0))
        prev_v = F.last(c, ignorenulls=True).over(w.rowsBetween(Window.unboundedPreceding, -1))
        next_v = F.first(c, ignorenulls=True).over(w.rowsBetween(1, Window.unboundedFollowing))
        short = (gap > 0) & (gap <= SHORT_GAP)
        longg = gap > SHORT_GAP
        if c in PRECIP:
            filled = F.when(short, F.lit(0.0)).otherwise(F.col(c))
        else:
            interp = (prev_v + next_v) / F.lit(2.0)
            filled = F.when(short & prev_v.isNotNull() & next_v.isNotNull(), interp).otherwise(F.col(c))
            filled = F.when(short & F.col(c).isNull() & prev_v.isNotNull() & next_v.isNull(), prev_v).otherwise(filled)
            filled = F.when(short & F.col(c).isNull() & next_v.isNotNull() & prev_v.isNull(), next_v).otherwise(filled)
        df = (
            df.withColumn(f"flag_long_gap_{c}", longg.cast("int"))
            .withColumn(f"flag_short_gap_filled_{c}", short.cast("int"))
            .withColumn(c, filled)
        )

    # 峰值：用 approxQuantile 一次列，避免过多 action
    for c in [x for x in POLLUTION if x in df.columns]:
        qs = df.approxQuantile(c, [0.25, 0.75], 0.1)
        if not qs or qs[0] is None or qs[1] is None or qs[1] == qs[0]:
            df = df.withColumn(f"flag_peak_{c}", F.lit(0))
            continue
        q1, q3 = qs
        iqr = q3 - q1
        low, high = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        peak = F.col(c).isNotNull() & ((F.col(c) < low) | (F.col(c) > high))
        df = df.withColumn(f"flag_peak_{c}", peak.cast("int"))

    peak_cols = [f"flag_peak_{c}" for c in POLLUTION if f"flag_peak_{c}" in df.columns]
    df = df.withColumn("peak_flag_any", F.greatest(*[F.col(c) for c in peak_cols]) if peak_cols else F.lit(0))

    for c in value_cols:
        df = df.withColumn(f"_na_{c}", F.col(c).isNull().cast("int"))
    df = df.cache()
    stats["rows_out"] = df.count()

    flag_cols = []
    for c in value_cols:
        flag_cols.extend(
            [
                x
                for x in (
                    f"flag_phys_{c}",
                    f"flag_clip_{c}",
                    f"flag_short_gap_filled_{c}",
                    f"flag_long_gap_{c}",
                    f"flag_peak_{c}",
                    f"_na_{c}",
                )
                if x in df.columns
            ]
        )
    summed = _sums(df, flag_cols)
    for c in value_cols:
        stats["physical_violations"][c] = summed.get(f"flag_phys_{c}", 0)
        stats["bounded_clipped"][c] = summed.get(f"flag_clip_{c}", 0)
        stats["short_gap_filled"][c] = summed.get(f"flag_short_gap_filled_{c}", 0)
        stats["long_gap_kept_null"][c] = summed.get(f"flag_long_gap_{c}", 0)
        if f"flag_peak_{c}" in summed:
            stats["pollution_peaks_marked"][c] = summed[f"flag_peak_{c}"]
    stats["nulls_after"] = {c: summed.get(f"_na_{c}", 0) for c in value_cols}
    df = df.drop(*[c for c in df.columns if c.startswith("_na_")])
    return df, stats


def aggregate(df):
    aggs = [
        F.count("*").alias("hours"),
        F.round(F.avg("temperature_2m"), 3).alias("avg_temp"),
        F.round(F.avg("pm2_5"), 3).alias("avg_pm25"),
        F.round(F.max("pm2_5"), 3).alias("max_pm25"),
        F.round(F.avg("european_aqi"), 3).alias("avg_aqi"),
        F.round(F.sum("precipitation"), 3).alias("precip_sum"),
    ]
    if "flag_peak_pm2_5" in df.columns:
        aggs.append(F.sum("flag_peak_pm2_5").alias("pm25_peak_hours"))
    else:
        aggs.append(F.lit(0).alias("pm25_peak_hours"))
    return df.groupBy("city_id", "city_name", F.to_date("ts_local").alias("dt")).agg(*aggs).orderBy("city_id", "dt")


def write_outputs(df, daily, out: str, stats: dict, mode: str, source: str):
    dwd_path = out.rstrip("/") + "/cleaned_parquet"
    daily_path = out.rstrip("/") + "/city_daily_parquet"
    csv_path = out.rstrip("/") + "/cleaned_csv"
    daily_csv = out.rstrip("/") + "/city_daily_csv"
    # Windows 本机缺少有效 winutils 时，Spark 写本地 parquet/csv 会失败；
    # 计算仍在 Spark，落盘改由 driver 侧 pandas 完成。集群 HDFS 走原生 write。
    use_pandas_sink = mode == "local" and os.name == "nt" and not str(out).startswith("hdfs")
    if use_pandas_sink:
        out_path = Path(out)
        out_path.mkdir(parents=True, exist_ok=True)
        pdf = df.toPandas()
        dpdf = daily.toPandas()
        parquet_file = out_path / "cleaned.parquet"
        daily_parquet = out_path / "city_daily.parquet"
        csv_file = out_path / "cleaned.csv"
        daily_csv_file = out_path / "city_daily.csv"
        pdf.to_parquet(parquet_file, index=False)
        dpdf.to_parquet(daily_parquet, index=False)
        pdf.to_csv(csv_file, index=False, encoding="utf-8-sig")
        dpdf.to_csv(daily_csv_file, index=False, encoding="utf-8-sig")
        dwd_path = str(parquet_file)
        daily_path = str(daily_parquet)
        csv_path = str(csv_file)
        daily_csv = str(daily_csv_file)
        stats["sink"] = "pandas_after_spark_compute_windows_local"
    else:
        df.write.mode("overwrite").parquet(dwd_path)
        daily.write.mode("overwrite").parquet(daily_path)
        df.coalesce(1).write.mode("overwrite").option("header", True).csv(csv_path)
        daily.coalesce(1).write.mode("overwrite").option("header", True).csv(daily_csv)
        stats["sink"] = "spark_write"

    report = {
        "job": "clean_and_aggregate",
        "engine": "spark",
        "mode": mode,
        "input": source,
        "out": out,
        "finished_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "data_nature_zh": "数值模式预报及归档预报产品（非站点原始观测）",
        "cleaning_policy": {
            "short_gap_hours": SHORT_GAP,
            "pollution": "IQR peak mark, keep value",
            "precipitation_short_gap": "fill 0",
            "long_gap": "keep null + flag_long_gap_*",
        },
        "stats": stats,
        "artifacts": {
            "cleaned_parquet": dwd_path,
            "city_daily_parquet": daily_path,
            "cleaned_csv": csv_path,
            "city_daily_csv": daily_csv,
        },
    }
    if not source.startswith("hdfs"):
        out_path = Path(out)
        out_path.mkdir(parents=True, exist_ok=True)
        (out_path / "spark_job_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print("SPARK_REPORT_JSON_BEGIN")
    print(json.dumps(report, ensure_ascii=False))
    print("SPARK_REPORT_JSON_END")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="", help="hourly_raw.parquet 路径")
    parser.add_argument("--ods", default="", help="ODS 批次目录（含 flat/）")
    parser.add_argument("--out", required=True)
    parser.add_argument("--mode", choices=["local", "cluster"], default="local")
    parser.add_argument("--app-name", default="course-meteo-aq-clean")
    args = parser.parse_args()
    source = args.input or args.ods
    if not source:
        raise SystemExit("需要 --input 或 --ods")

    spark = build_spark(args.app_name, args.mode)
    spark.sparkContext.setLogLevel("WARN")
    try:
        df, used = load_input(spark, source)
        cleaned, stats = clean(df)
        daily = aggregate(cleaned)
        stats["city_daily_rows"] = daily.count()
        stats["input_rows"] = stats.get("rows_before_dedupe")
        write_outputs(cleaned, daily, args.out, stats, args.mode, used)
    finally:
        spark.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
