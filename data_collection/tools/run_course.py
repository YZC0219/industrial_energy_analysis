"""期末主入口：真实采集 → Spark 清洗（本机/集群）→ PPT；pandas 仅备用。"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.run_collect import collect_batch  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="基于 Spark 的多城市气象与空气质量数据采集及预处理"
    )
    parser.add_argument("--past-days", type=int, default=3)
    parser.add_argument("--forecast-days", type=int, default=1)
    parser.add_argument(
        "--engine",
        choices=["spark-local", "spark-cluster", "pandas"],
        default="spark-local",
        help="spark-local=日常演示；spark-cluster=两节点验收；pandas=备用",
    )
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--ppt", action="store_true")
    parser.add_argument("--port", type=int, default=8020)
    parser.add_argument("--skip-collect", action="store_true")
    args = parser.parse_args()

    if args.ppt:
        subprocess.run([sys.executable, str(ROOT / "tools" / "make_ppt.py")], check=False)

    if not args.skip_collect:
        print("=== 真实 API 采集 ODS ===")
        man = collect_batch(past_days=args.past_days, forecast_days=args.forecast_days)
        if man["file_count"] == 0:
            return 1

    print(f"=== 预处理引擎: {args.engine} ===")
    if args.engine == "spark-local":
        try:
            from tools.run_spark_local import run_local

            run_local()
        except Exception as exc:  # noqa: BLE001
            print("[warn] Spark local 失败，回退 pandas：", exc)
            from preprocess.pipeline import run_preprocess

            run_preprocess()
    elif args.engine == "spark-cluster":
        from tools.run_spark_cluster import run_cluster

        run_cluster()
    else:
        from preprocess.pipeline import run_preprocess

        run_preprocess()

    if not args.serve:
        print("看板: python -m uvicorn server.app:app --app-dir data_collection --host 127.0.0.1 --port 8020")
        return 0

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "server.app:app",
        "--app-dir",
        str(ROOT),
        "--host",
        "127.0.0.1",
        "--port",
        str(args.port),
    ]
    proc = subprocess.Popen(cmd, cwd=str(ROOT))
    try:
        for _ in range(40):
            try:
                urlopen(f"http://127.0.0.1:{args.port}/api/health", timeout=1)
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.25)
        print(f"看板 http://127.0.0.1:{args.port}/  Ctrl+C 结束")
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
