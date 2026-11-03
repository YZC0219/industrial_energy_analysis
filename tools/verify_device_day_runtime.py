"""One-shot offline demo: simulate day → minute rollup → E01 reconciliation.

Default path needs only Python + clean_energy.csv. No Kafka, MySQL, or
ClickHouse. Each run writes an isolated directory under
D:/industrial_energy_analysis/output/extensions/device_day_runs/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]

from streaming.device_simulator import SEED_DEFAULT, load_daily_e01, simulate_day
from streaming.reconcile_device_day import build_day_evidence


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as sink:
        for row in rows:
            sink.write(json.dumps(row, ensure_ascii=False) + "\n")


def run(business_date: date, daily_csv: Path, output: Path, *, seed: int,
        interval_seconds: int, miss_rate: float,
        relative_tolerance: float = 0.08, min_coverage: float = 0.90) -> dict:
    """Backward-compatible entry: writes evidence JSON at `output`. """
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
    evidence_body = build_day_evidence(
        samples,
        targets,
        business_date=business_date,
        relative_tolerance=relative_tolerance,
        min_coverage=min_coverage,
    )
    folder = output.parent
    folder.mkdir(parents=True, exist_ok=True)
    samples_path = folder / f"device_day_samples_{business_date.isoformat()}.jsonl"
    device_path = folder / f"device_minutes_{business_date.isoformat()}.jsonl"
    workshop_path = folder / f"workshop_minutes_{business_date.isoformat()}.jsonl"
    _write_jsonl(samples_path, samples)
    artifacts = evidence_body.pop("artifacts")
    _write_jsonl(device_path, artifacts["device_minutes"])
    _write_jsonl(workshop_path, artifacts["workshop_minutes"])
    evidence = {
        "success": evidence_body["success"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "business_date": business_date.isoformat(),
        "seed": seed,
        "interval_seconds": interval_seconds,
        "miss_rate": miss_rate,
        "daily_csv": str(daily_csv),
        "samples_path": str(samples_path),
        "device_minutes_path": str(device_path),
        "workshop_minutes_path": str(workshop_path),
        "sample_rows": len(samples),
        "source_hashes": {
            "streaming/device_simulator.py": hashlib.sha256(
                (ROOT / "streaming/device_simulator.py").read_bytes()).hexdigest(),
            "streaming/reconcile_device_day.py": hashlib.sha256(
                (ROOT / "streaming/reconcile_device_day.py").read_bytes()).hexdigest(),
            "streaming/minute_store.py": hashlib.sha256(
                (ROOT / "streaming/minute_store.py").read_bytes()).hexdigest(),
        },
        "reconciliation": evidence_body,
        "scope": (
            "Simulated multi-device power for one Asia/Shanghai day; post-hoc E01 "
            "calibration aligns integrals for demo only; modes are labels; "
            "workshop minutes sum device means; integral is not meter diff."
        ),
    }
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    return evidence


def run_demo(business_date: date, daily_csv: Path, runs_root: Path, *, seed: int,
             interval_seconds: int, miss_rate: float) -> dict:
    """Create an isolated run directory and print an 8-workshop table."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    run_dir = runs_root / f"{stamp}_{uuid4().hex[:8]}"
    run_dir.mkdir(parents=True, exist_ok=False)
    evidence_path = run_dir / "evidence.json"
    evidence = run(
        business_date,
        daily_csv,
        evidence_path,
        seed=seed,
        interval_seconds=interval_seconds,
        miss_rate=miss_rate,
    )
    # Move companion files produced next to evidence into the same run_dir —
    # `run` already wrote them beside evidence_path.
    print(f"run_dir={run_dir}", flush=True)
    print(
        f"{'WS':<4} {'daily_E01':>12} {'raw_kWh':>12} {'minute_kWh':>12} "
        f"{'coverage':>9} {'miss%':>8} {'raw_err':>9} {'min_err':>9} status",
        flush=True,
    )
    for row in evidence["reconciliation"]["workshops"]:
        print(
            f"{row['workshop_code']:<4} "
            f"{row['daily_e01_kwh']:>12.3f} "
            f"{row['raw_integral_kwh']:>12.3f} "
            f"{row['minute_integral_kwh']:>12.3f} "
            f"{row['coverage']:>9.4f} "
            f"{row['miss_rate'] * 100:>7.2f}% "
            f"{row['raw_vs_daily_rel']:>9.4f} "
            f"{row['minute_vs_daily_rel']:>9.4f} "
            f"{row['status']}",
            flush=True,
        )
    print(json.dumps({
        "success": evidence["success"],
        "evidence": str(evidence_path),
        "sample_rows": evidence["sample_rows"],
        "counts": evidence["reconciliation"]["counts"],
        "boundary": evidence["reconciliation"]["boundary"],
    }, ensure_ascii=False, indent=2), flush=True)
    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default="2025-06-15")
    parser.add_argument("--daily-csv", type=Path,
                        default=ROOT / "output" / "clean_energy.csv")
    parser.add_argument("--output", type=Path, default=None,
                        help="Optional fixed evidence path (compat). Default: new run dir.")
    parser.add_argument("--runs-root", type=Path,
                        default=ROOT / "output" / "extensions" / "device_day_runs")
    parser.add_argument("--seed", type=int, default=SEED_DEFAULT)
    parser.add_argument("--interval-seconds", type=int, default=300,
                        help="Default 5 min for faster local evidence; use 60 for denser demos")
    parser.add_argument("--miss-rate", type=float, default=0.02)
    args = parser.parse_args(argv)
    if not args.daily_csv.is_file():
        raise SystemExit(
            f"Missing {args.daily_csv}; run python src/clean_data.py first")
    if args.output is not None:
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
    evidence = run_demo(
        date.fromisoformat(args.date),
        args.daily_csv,
        args.runs_root,
        seed=args.seed,
        interval_seconds=args.interval_seconds,
        miss_rate=args.miss_rate,
    )
    return 0 if evidence["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
