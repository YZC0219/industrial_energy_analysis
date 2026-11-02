"""Offline multi-device day simulation and E01 reconciliation evidence.

Does not start Kafka/Flink. Writes JSON evidence under output/extensions/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from streaming.device_simulator import SEED_DEFAULT, load_daily_e01, simulate_day
from streaming.reconcile_device_day import reconcile


def run(business_date: date, daily_csv: Path, output: Path, *, seed: int,
        interval_seconds: int, miss_rate: float) -> dict:
    targets = load_daily_e01(daily_csv, business_date)
    if len(targets) < 8:
        raise ValueError(f"Expected 8 workshop E01 targets, got {len(targets)}")
    samples = simulate_day(
        business_date,
        seed=seed,
        interval_seconds=interval_seconds,
        miss_rate=miss_rate,
        daily_targets_kwh=targets,
        calibrate=True,
    )
    report = reconcile(samples, targets, business_date=business_date)
    folder = output.parent
    folder.mkdir(parents=True, exist_ok=True)
    samples_path = folder / f"device_day_samples_{business_date.isoformat()}.jsonl"
    with samples_path.open("w", encoding="utf-8") as sink:
        for row in samples:
            sink.write(json.dumps(row, ensure_ascii=False) + "\n")
    evidence = {
        "success": report["success"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "business_date": business_date.isoformat(),
        "seed": seed,
        "interval_seconds": interval_seconds,
        "miss_rate": miss_rate,
        "daily_csv": str(daily_csv),
        "samples_path": str(samples_path),
        "sample_rows": len(samples),
        "source_hashes": {
            "streaming/device_simulator.py": hashlib.sha256(
                (ROOT / "streaming/device_simulator.py").read_bytes()).hexdigest(),
            "streaming/reconcile_device_day.py": hashlib.sha256(
                (ROOT / "streaming/reconcile_device_day.py").read_bytes()).hexdigest(),
        },
        "reconciliation": report,
        "scope": (
            "Simulated multi-device power for one Asia/Shanghai day, calibrated to "
            "warehouse E01 kWh; modes are labels only; integral is not meter diff."
        ),
    }
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default="2025-06-15")
    parser.add_argument("--daily-csv", type=Path,
                        default=ROOT / "output" / "clean_energy.csv")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "output" / "extensions" / "device_day_runtime.json")
    parser.add_argument("--seed", type=int, default=SEED_DEFAULT)
    parser.add_argument("--interval-seconds", type=int, default=300,
                        help="Default 5 min for faster local evidence; use 60 for denser demos")
    parser.add_argument("--miss-rate", type=float, default=0.02)
    args = parser.parse_args(argv)
    if not args.daily_csv.is_file():
        raise SystemExit(
            f"Missing {args.daily_csv}; run python src/clean_data.py first")
    evidence = run(date.fromisoformat(args.date), args.daily_csv, args.output,
                   seed=args.seed, interval_seconds=args.interval_seconds,
                   miss_rate=args.miss_rate)
    print(json.dumps({
        "success": evidence["success"],
        "output": str(args.output),
        "sample_rows": evidence["sample_rows"],
        "counts": evidence["reconciliation"]["counts"],
    }, ensure_ascii=False), flush=True)
    return 0 if evidence["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
