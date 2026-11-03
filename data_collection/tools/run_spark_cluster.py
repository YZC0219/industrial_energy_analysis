"""两节点 VM：上传 ODS/flat → HDFS → nohup spark-submit → 轮询日志 → 拉回对账。"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.configure_vm_cluster import BASE, client, run  # noqa: E402
from lakehouse_local.paths import batch_dir, ensure_layout  # noqa: E402
from tools.flatten_ods import flatten  # noqa: E402

MASTER = "192.168.21.131"
REMOTE_JOB_DIR = f"{BASE}/course_collect"
HDFS_ROOT = "/energy/course_collect"


def latest_ods() -> Path:
    meta = ensure_layout()["meta"] / "latest_ods_batch.json"
    return Path(json.loads(meta.read_text(encoding="utf-8"))["ods_dir"])


def run_cluster(ods: Path | None = None, poll_sec: int = 900) -> dict:
    ensure_layout()
    ods_dir = ods or latest_ods()
    batch_id = ods_dir.name
    dwd = batch_dir("dwd", batch_id)
    started = time.time()
    flat_parquet = flatten(ods_dir)

    c = client(MASTER)
    try:
        prefix = f". {BASE}/env.sh; "
        live = run(c, prefix + "hdfs dfsadmin -report | head -n 25")
        run(c, f"mkdir -p {REMOTE_JOB_DIR}/jobs {REMOTE_JOB_DIR}/batches/{batch_id}/ods {REMOTE_JOB_DIR}/batches/{batch_id}/flat {REMOTE_JOB_DIR}/logs")

        sftp = c.open_sftp()
        sftp.put(str(ROOT / "spark_jobs" / "clean_and_aggregate.py"), f"{REMOTE_JOB_DIR}/jobs/clean_and_aggregate.py")
        for path in ods_dir.glob("*.json"):
            sftp.put(str(path), f"{REMOTE_JOB_DIR}/batches/{batch_id}/ods/{path.name}")
        sftp.put(str(flat_parquet), f"{REMOTE_JOB_DIR}/batches/{batch_id}/flat/hourly_raw.parquet")
        csv_flat = ods_dir / "flat" / "hourly_raw.csv"
        if csv_flat.exists():
            sftp.put(str(csv_flat), f"{REMOTE_JOB_DIR}/batches/{batch_id}/flat/hourly_raw.csv")
        sftp.close()

        hdfs_ods = f"{HDFS_ROOT}/{batch_id}/ods"
        hdfs_flat = f"{HDFS_ROOT}/{batch_id}/flat"
        hdfs_dwd = f"{HDFS_ROOT}/{batch_id}/dwd"
        run(c, prefix + f"hdfs dfs -rm -r -f {HDFS_ROOT}/{batch_id} || true")
        run(c, prefix + f"hdfs dfs -mkdir -p {hdfs_ods} {hdfs_flat}")
        run(c, prefix + f"hdfs dfs -put -f {REMOTE_JOB_DIR}/batches/{batch_id}/ods/*.json {hdfs_ods}/")
        run(c, prefix + f"hdfs dfs -put -f {REMOTE_JOB_DIR}/batches/{batch_id}/flat/hourly_raw.parquet {hdfs_flat}/")

        remote_log = f"{REMOTE_JOB_DIR}/logs/spark_{batch_id}.log"
        spark_cmd = (
            "spark-submit "
            "--master spark://192.168.21.131:7077 "
            "--deploy-mode client "
            "--executor-memory 768m --executor-cores 1 --total-executor-cores 1 "
            "--conf spark.driver.memory=512m "
            "--conf spark.driver.host=192.168.21.131 "
            "--conf spark.sql.shuffle.partitions=4 "
            f"{REMOTE_JOB_DIR}/jobs/clean_and_aggregate.py "
            f"--input hdfs://192.168.21.131:19000{hdfs_flat}/hourly_raw.parquet "
            f"--out hdfs://192.168.21.131:19000{hdfs_dwd} "
            "--mode cluster"
        )
        # 后台跑，避免 SSH channel 读超时
        launch = (
            f"{prefix} nohup bash -lc {json.dumps(spark_cmd)} "
            f"> {remote_log} 2>&1 & echo $!"
        )
        pid = run(c, launch).strip().splitlines()[-1].strip()
        print(f"[spark-cluster] started pid={pid} log={remote_log}")

        deadline = time.time() + poll_sec
        spark_out = ""
        while time.time() < deadline:
            time.sleep(8)
            spark_out = run(c, f"tail -n 80 {remote_log} || true")
            alive = run(c, f"ps -p {pid} -o pid= || true").strip()
            if "SPARK_REPORT_JSON_BEGIN" in spark_out or not alive:
                # 再等片刻刷完文件
                time.sleep(2)
                spark_out = run(c, f"cat {remote_log} || true")
                break
            print(f"[spark-cluster] waiting… alive={bool(alive)}")

        if "SPARK_REPORT_JSON_BEGIN" not in spark_out:
            raise RuntimeError(f"集群作业未在 {poll_sec}s 内完成，日志尾：\n{spark_out[-2000:]}")

        local_log = ensure_layout()["reports"] / f"spark_cluster_log_{batch_id}.txt"
        local_log.write_text(spark_out, encoding="utf-8")
        chunk = spark_out.split("SPARK_REPORT_JSON_BEGIN", 1)[1].split("SPARK_REPORT_JSON_END", 1)[0].strip()
        report = json.loads(chunk)

        run(
            c,
            prefix
            + f"hdfs dfs -getmerge {hdfs_dwd}/cleaned_csv {REMOTE_JOB_DIR}/batches/{batch_id}/cleaned.csv",
        )
        sftp = c.open_sftp()
        local_csv = dwd / "cleaned_from_hdfs.csv"
        sftp.get(f"{REMOTE_JOB_DIR}/batches/{batch_id}/cleaned.csv", str(local_csv))
        try:
            run(
                c,
                prefix
                + f"hdfs dfs -getmerge {hdfs_dwd}/city_daily_csv {REMOTE_JOB_DIR}/batches/{batch_id}/city_daily.csv",
            )
            sftp.get(
                f"{REMOTE_JOB_DIR}/batches/{batch_id}/city_daily.csv",
                str(dwd / "city_daily_from_hdfs.csv"),
            )
        except Exception as exc:  # noqa: BLE001
            print("[spark-cluster] daily csv:", exc)
        sftp.close()
        wc = run(c, f"wc -l {REMOTE_JOB_DIR}/batches/{batch_id}/cleaned.csv").strip()
        hdfs_ls = run(c, prefix + f"hdfs dfs -ls -R {HDFS_ROOT}/{batch_id} | tail -n 60")

        final = {
            "batch_id": batch_id,
            "engine": "spark-cluster-two-node",
            "master": "spark://192.168.21.131:7077",
            "hdfs": "hdfs://192.168.21.131:19000",
            "hdfs_ods": hdfs_ods,
            "hdfs_flat": hdfs_flat,
            "hdfs_dwd": hdfs_dwd,
            "architecture": {
                "nodes": [
                    "192.168.21.131 NameNode/Spark Master/Driver",
                    "192.168.21.132 DataNode/Spark Worker",
                ],
                "executor": "1 core / 768m",
                "replication": 1,
            },
            "hdfs_report_head": live[-1500:],
            "hdfs_ls_tail": hdfs_ls,
            "cleaned_csv_wc": wc,
            "local_cleaned_csv": str(local_csv),
            "log": str(local_log),
            "remote_log": remote_log,
            "stats": report.get("stats"),
            "reconcile": {
                "duplicates_removed": (report.get("stats") or {}).get("duplicates_removed"),
                "rows_out": (report.get("stats") or {}).get("rows_out"),
                "city_daily_rows": (report.get("stats") or {}).get("city_daily_rows"),
                "pollution_peaks_marked": (report.get("stats") or {}).get("pollution_peaks_marked"),
                "long_gap_kept_null": (report.get("stats") or {}).get("long_gap_kept_null"),
                "short_gap_filled": (report.get("stats") or {}).get("short_gap_filled"),
            },
            "elapsed_sec": round(time.time() - started, 2),
            "data_nature_zh": report.get("data_nature_zh"),
        }
        out_report = ensure_layout()["reports"] / f"spark_cluster_{batch_id}.json"
        out_report.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
        (ensure_layout()["meta"] / "latest_dwd_batch.json").write_text(
            json.dumps(
                {
                    "batch_id": batch_id,
                    "engine": "spark-cluster-two-node",
                    "dwd_dir": str(dwd),
                    "quality_report": str(out_report),
                    "rows": (report.get("stats") or {}).get("rows_out"),
                    "hdfs_dwd": hdfs_dwd,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"[spark-cluster] ok → {out_report}")
        return final
    finally:
        c.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ods-dir", default="")
    parser.add_argument("--poll-sec", type=int, default=900)
    args = parser.parse_args()
    run_cluster(Path(args.ods_dir) if args.ods_dir else None, poll_sec=args.poll_sec)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
