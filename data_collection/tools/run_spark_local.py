"""本机 Spark local[1] 清洗（控制内存；失败则提示用 pandas 备用）。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lakehouse_local.paths import batch_dir, ensure_layout  # noqa: E402
from tools.flatten_ods import flatten  # noqa: E402


def latest_ods() -> Path:
    meta = ensure_layout()["meta"] / "latest_ods_batch.json"
    info = json.loads(meta.read_text(encoding="utf-8"))
    return Path(info["ods_dir"])


def pyspark_home() -> Path:
    import pyspark

    return Path(pyspark.__file__).resolve().parent


def promote_csv(spark_out: Path, dwd: Path) -> Path | None:
    """把 Spark 输出提升到 dwd 根目录，供看板读取。"""
    direct = spark_out / "cleaned.csv"
    if direct.exists():
        shutil.copyfile(direct, dwd / "cleaned.csv")
        daily = spark_out / "city_daily.csv"
        if daily.exists():
            shutil.copyfile(daily, dwd / "city_daily.csv")
        parquet = spark_out / "cleaned.parquet"
        if parquet.exists():
            shutil.copyfile(parquet, dwd / "cleaned.parquet")
        return dwd / "cleaned.csv"
    csv_dir = spark_out / "cleaned_csv"
    if not csv_dir.exists():
        return None
    parts = [p for p in list(csv_dir.glob("*.csv")) + list(csv_dir.glob("part-*")) if p.is_file()]
    if not parts:
        return None
    shutil.copyfile(parts[0], dwd / "cleaned.csv")
    return dwd / "cleaned.csv"


def run_local(ods: Path | None = None) -> dict:
    ensure_layout()
    ods_dir = ods or latest_ods()
    batch_id = ods_dir.name
    dwd = batch_dir("dwd", batch_id)
    spark_out = dwd / "spark_local"
    if spark_out.exists():
        shutil.rmtree(spark_out, ignore_errors=True)
    spark_out.mkdir(parents=True, exist_ok=True)

    flat_parquet = flatten(ods_dir)
    job = ROOT / "spark_jobs" / "clean_and_aggregate.py"
    home = pyspark_home()
    env = os.environ.copy()
    hadoop_dummy = PROJECT / "dependencies" / "hadoop_dummy"
    (hadoop_dummy / "bin").mkdir(parents=True, exist_ok=True)
    winutils = hadoop_dummy / "bin" / "winutils.exe"
    if not winutils.exists():
        winutils.write_bytes(b"")
    env["SPARK_HOME"] = str(home)
    env["HADOOP_HOME"] = str(hadoop_dummy)
    env["hadoop.home.dir"] = str(hadoop_dummy)
    env["PYSPARK_PYTHON"] = sys.executable
    env["PYSPARK_DRIVER_PYTHON"] = sys.executable
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")

    cmd = [
        sys.executable,
        str(job),
        "--input",
        str(flat_parquet).replace("\\", "/"),
        "--out",
        str(spark_out).replace("\\", "/"),
        "--mode",
        "local",
    ]

    print("[spark-local]", " ".join(cmd))
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    log_path = ensure_layout()["reports"] / f"spark_local_log_{batch_id}.txt"
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    log_path.write_text(stdout + "\n" + stderr, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(f"Spark local 失败，见 {log_path}\n{stderr[-2000:]}")

    report = {}
    if "SPARK_REPORT_JSON_BEGIN" in stdout:
        chunk = stdout.split("SPARK_REPORT_JSON_BEGIN", 1)[1].split("SPARK_REPORT_JSON_END", 1)[0].strip()
        report = json.loads(chunk)
    local_report = spark_out / "spark_job_report.json"
    if local_report.exists():
        report = json.loads(local_report.read_text(encoding="utf-8"))

    promote_csv(spark_out, dwd)
    # 复制 parquet 目录指针到 meta
    final_report = {
        "batch_id": batch_id,
        "engine": "spark-local",
        "ods_dir": str(ods_dir),
        "dwd_dir": str(dwd),
        "spark_out": str(spark_out),
        "log": str(log_path),
        "stats": report.get("stats"),
        "artifacts": report.get("artifacts"),
        "data_nature_zh": report.get("data_nature_zh"),
        "cleaning_policy": report.get("cleaning_policy"),
        "reconcile": {
            "weather_hourly_rows": (report.get("stats") or {}).get("weather_hourly_rows"),
            "aq_hourly_rows": (report.get("stats") or {}).get("aq_hourly_rows"),
            "duplicates_removed": (report.get("stats") or {}).get("duplicates_removed"),
            "rows_out": (report.get("stats") or {}).get("rows_out"),
            "city_daily_rows": (report.get("stats") or {}).get("city_daily_rows"),
            "pollution_peaks_marked": (report.get("stats") or {}).get("pollution_peaks_marked"),
        },
    }
    out_report = ensure_layout()["reports"] / f"spark_local_{batch_id}.json"
    out_report.write_text(json.dumps(final_report, ensure_ascii=False, indent=2), encoding="utf-8")
    (ensure_layout()["meta"] / "latest_dwd_batch.json").write_text(
        json.dumps(
            {
                "batch_id": batch_id,
                "engine": "spark-local",
                "dwd_dir": str(dwd),
                "quality_report": str(out_report),
                "rows": (report.get("stats") or {}).get("rows_out"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[spark-local] ok → {out_report}")
    return final_report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ods-dir", default="")
    args = parser.parse_args()
    run_local(Path(args.ods_dir) if args.ods_dir else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
