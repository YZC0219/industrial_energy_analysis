"""Foreground process supervisor. This does not install or register an OS service."""
import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def supervise(command, log_dir, restart_limit=5, base_delay=1, max_delay=30, env=None, stop=None):
    if type(restart_limit) is not int or restart_limit < 0 or not all(
            math.isfinite(v) and v >= 0 for v in (base_delay, max_delay)):
        raise ValueError("Invalid supervisor restart policy")
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    history = []
    child = None
    stopping = lambda: stop is not None and stop.is_set()
    try:
        for attempt in range(restart_limit + 1):
            if stopping():
                return {"state": "stopped", "attempts": history}
            with (log_dir / f"child-{attempt + 1}.log").open("ab") as output:
                child = subprocess.Popen(command, cwd=ROOT, env=env, stdout=output, stderr=output)
                while child.poll() is None:
                    if stopping():
                        child.terminate()
                        break
                    time.sleep(0.1)
                code = child.wait(timeout=10)
            history.append({"attempt": attempt + 1, "exit_code": code})
            (log_dir / "supervisor.json").write_text(json.dumps(history, indent=2), encoding="utf-8")
            if stopping():
                return {"state": "stopped", "attempts": history}
            if code == 0:
                return {"state": "completed", "attempts": history}
            delay = min(max_delay, base_delay * 2 ** min(attempt, 30))
            deadline = time.monotonic() + delay
            while attempt < restart_limit and time.monotonic() < deadline and not stopping():
                time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        return {"state": "restart_limit_reached", "attempts": history}
    except KeyboardInterrupt:
        return {"state": "stopped", "attempts": history}
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


def command_from_config(config, env):
    role = config.get("role")
    if role not in ("worker", "consumer"):
        raise ValueError("Role must be worker or consumer")
    def required_env(key):
        name = config.get(key)
        if not name or not env.get(name):
            raise ValueError(f"Missing environment setting for {key}")
        return env[name]
    command = [sys.executable, "-m", "streaming.alert_outbox", "work"] if role == "worker" else [sys.executable, "-m", "streaming.kafka_outbox"]
    path = Path(config["db"])
    if not path.is_absolute():
        raise ValueError("Queue database must use an absolute path")
    command += ["--db", str(path), "--webhook-url", required_env("webhook_env")]
    if config.get("redis_env"):
        command += ["--redis-url", required_env("redis_env")]
    if role == "consumer":
        command += ["--bootstrap-servers", required_env("bootstrap_env"),
                    "--topic", config["topic"], "--group-id", config["group_id"]]
    return command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    log_dir = Path(config["log_dir"])
    if not log_dir.is_absolute():
        parser.error("Log directory must use an absolute path")
    result = supervise(command_from_config(config, os.environ), log_dir,
                       config.get("restart_limit", 5), config.get("base_delay", 1), config.get("max_delay", 30))
    print(json.dumps(result, indent=2))
    return 1 if result["state"] == "restart_limit_reached" else 0


if __name__ == "__main__":
    sys.exit(main())
