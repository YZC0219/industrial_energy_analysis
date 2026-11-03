"""把本机 DWD 打包成「可上传 HDFS」清单；默认不连虚拟机。

宿主内存不够时：只生成上传说明书与校验哈希，不启动 Spark。
集群可用且你显式传 --hdfs-dir 时，才尝试 hdfs dfs -put（需本机/WSL 配好客户端）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lakehouse_local.paths import ensure_layout  # noqa: E402


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def latest_dwd() -> dict:
    meta = ensure_layout()["meta"] / "latest_dwd_batch.json"
    if not meta.exists():
        raise FileNotFoundError("没有 DWD 批次，先跑采集与预处理")
    return json.loads(meta.read_text(encoding="utf-8"))


def build_package() -> Path:
    info = latest_dwd()
    dwd = Path(info["dwd_dir"])
    batch_id = info["batch_id"]
    out_dir = ensure_layout()["meta"] / f"hdfs_ready_{batch_id}"
    out_dir.mkdir(parents=True, exist_ok=True)

    files = []
    for name in ("cleaned.csv", "cleaned.parquet", "raw_merged.csv"):
        p = dwd / name
        if p.exists():
            files.append(
                {
                    "name": name,
                    "path": str(p),
                    "bytes": p.stat().st_size,
                    "sha256": sha256(p),
                }
            )

    guide = {
        "batch_id": batch_id,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "host_constraint": "虚拟机分布式已部署，但宿主内存紧张时勿同时开多台 VM 跑作业验收",
        "local_files": files,
        "suggested_hdfs_path": f"/energy/course_collect/{batch_id}/",
        "upload_commands_when_cluster_ok": [
            "source /home/yzc/energy-cluster-20261103/env.sh",
            f"hdfs dfs -mkdir -p /energy/course_collect/{batch_id}",
            f"hdfs dfs -put -f {dwd / 'cleaned.parquet'} /energy/course_collect/{batch_id}/",
            f"hdfs dfs -put -f {dwd / 'cleaned.csv'} /energy/course_collect/{batch_id}/",
        ],
        "spark_example_when_memory_ok": (
            "spark-submit --master spark://192.168.21.131:7077 "
            "你的清洗或聚合脚本.py "
            f"--input hdfs:///energy/course_collect/{batch_id}/cleaned.parquet"
        ),
        "course_acceptance_without_vm": [
            "本机 ODS JSON 存在且 manifest 有真实 hourly_points",
            "DWD cleaned.csv 行数 > 0",
            "quality_*.json 含缺失填充与异常处理步骤",
        ],
    }
    out = out_dir / "upload_manifest.json"
    out.write_text(json.dumps(guide, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[hdfs-ready] {out}")
    return out


def try_hdfs_put(hdfs_dir: str) -> None:
    info = latest_dwd()
    local = Path(info["dwd_dir"]) / "cleaned.csv"
    if not local.exists():
        raise FileNotFoundError(local)
    cmd = ["hdfs", "dfs", "-mkdir", "-p", hdfs_dir]
    subprocess.run(cmd, check=False)
    cmd2 = ["hdfs", "dfs", "-put", "-f", str(local), hdfs_dir]
    print("[hdfs] ", " ".join(cmd2))
    subprocess.run(cmd2, check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hdfs-dir", default="", help="显式指定才尝试 hdfs dfs -put")
    args = parser.parse_args()
    build_package()
    if args.hdfs_dir:
        try_hdfs_put(args.hdfs_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
