"""一键启动采集服务 + Agent，并生成 PPT。"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from urllib import error, request

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent


def wait_health(url: str, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            with request.urlopen(url, timeout=1.5) as resp:
                if resp.status == 200:
                    return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(0.4)
    raise RuntimeError(f"服务未就绪: {url} ({last_err})")


def main() -> int:
    parser = argparse.ArgumentParser(description="能迹采集台一键演示")
    parser.add_argument("--port", type=int, default=8020)
    parser.add_argument("--rounds", type=int, default=25)
    parser.add_argument("--interval", type=float, default=1.5)
    parser.add_argument("--skip-ppt", action="store_true")
    parser.add_argument("--no-agent", action="store_true", help="只启服务，不跑 Agent")
    args = parser.parse_args()

    if not args.skip_ppt:
        ppt = subprocess.run([sys.executable, str(ROOT / "tools" / "make_ppt.py")], check=False)
        if ppt.returncode != 0:
            print("[demo] PPT 生成失败，继续启动服务")

    host = "127.0.0.1"
    base = f"http://{host}:{args.port}"
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "server.app:app",
        "--app-dir",
        str(ROOT),
        "--host",
        host,
        "--port",
        str(args.port),
        "--log-level",
        "info",
    ]
    print(f"[demo] 启动服务 {base}/")
    server = subprocess.Popen(cmd, cwd=str(ROOT))
    try:
        wait_health(f"{base}/api/health")
        print(f"[demo] 看板已就绪：{base}/")
        if not args.no_agent:
            agent = [
                sys.executable,
                str(ROOT / "collector" / "agent.py"),
                "--endpoint",
                f"{base}/api/v1/ingest",
                "--rounds",
                str(args.rounds),
                "--interval",
                str(args.interval),
            ]
            print("[demo] 启动边缘 Agent …")
            subprocess.run(agent, check=False)
            print("[demo] Agent 结束。服务保持运行，Ctrl+C 退出。")
        else:
            print("[demo] 未启动 Agent。可另开终端运行 collector/agent.py")
        server.wait()
    except KeyboardInterrupt:
        print("\n[demo] 正在停止…")
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
