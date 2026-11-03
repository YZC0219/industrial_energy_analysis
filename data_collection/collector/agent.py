"""边缘采集 Agent：模拟多电表采样并批量上报。"""

from __future__ import annotations

import argparse
import math
import random
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request
import json

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collector.protocol import MeterSample, make_event_id, utc_now_iso  # noqa: E402

DEVICES = [
    ("M-W01-01", "W01", 95.0),
    ("M-W01-02", "W01", 48.0),
    ("M-W02-01", "W02", 72.0),
    ("M-W02-02", "W02", 68.0),
    ("M-W03-01", "W03", 110.0),
    ("M-W03-02", "W03", 28.0),
    ("M-W04-01", "W04", 42.0),
    ("M-W05-01", "W05", 55.0),
]


def _state_and_power(base_kw: float, tick: int, rng: random.Random) -> tuple[str, float]:
    phase = (tick % 60) / 60.0
    wave = 0.08 * math.sin(2 * math.pi * phase + rng.random())
    roll = rng.random()
    if roll < 0.03:
        return "spike", round(base_kw * (1.35 + rng.uniform(0, 0.2)), 3)
    if roll < 0.08:
        return "standby", round(base_kw * 0.12 + rng.uniform(0, 1.5), 3)
    if roll < 0.14:
        return "idle", round(base_kw * 0.25 + rng.uniform(0, 2.0), 3)
    if roll < 0.16:
        return "offline", 0.0
    return "running", round(max(0.0, base_kw * (1.0 + wave) + rng.uniform(-2, 2)), 3)


def build_batch(tick: int, seed: int) -> list[dict]:
    rng = random.Random(seed + tick * 17)
    ts = utc_now_iso()
    # Slight per-device offset so timestamps look live but stay same second mostly
    samples: list[dict] = []
    for device_id, workshop_id, base_kw in DEVICES:
        state, power = _state_and_power(base_kw, tick, rng)
        event_id = make_event_id(device_id, ts, power)
        sample = MeterSample(
            event_id=event_id,
            device_id=device_id,
            workshop_id=workshop_id,
            energy_code="E01",
            ts_utc=ts,
            power_kw=power,
            state=state,
            source="edge-agent-sim",
        )
        samples.append(sample.to_dict())
    return samples


def post_batch(endpoint: str, samples: list[dict], timeout: float = 5.0) -> dict:
    body = json.dumps(
        {"batch_id": uuid.uuid4().hex[:12], "samples": samples},
        ensure_ascii=False,
    ).encode("utf-8")
    req = request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def run_loop(endpoint: str, interval: float, rounds: int, seed: int) -> None:
    print(f"[agent] endpoint={endpoint} interval={interval}s rounds={rounds}")
    for tick in range(rounds):
        samples = build_batch(tick, seed)
        try:
            result = post_batch(endpoint, samples)
            print(
                f"[agent] tick={tick + 1}/{rounds} "
                f"accepted={result.get('accepted')} dup={result.get('duplicated')} "
                f"rej={result.get('rejected')} schema_rej={result.get('schema_rejected_count', 0)}"
            )
        except error.URLError as exc:
            print(f"[agent] 上报失败: {exc}")
        if tick + 1 < rounds:
            time.sleep(interval)


def main() -> int:
    parser = argparse.ArgumentParser(description="能迹边缘采集 Agent（模拟电表）")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8020/api/v1/ingest")
    parser.add_argument("--interval", type=float, default=2.0, help="采样间隔秒")
    parser.add_argument("--rounds", type=int, default=30, help="上报轮数")
    parser.add_argument("--seed", type=int, default=20240918)
    parser.add_argument("--once", action="store_true", help="只发一批")
    args = parser.parse_args()

    if args.once:
        samples = build_batch(0, args.seed)
        result = post_batch(args.endpoint, samples)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    run_loop(args.endpoint, args.interval, max(1, args.rounds), args.seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
