"""Reconcile device power samples to warehouse daily E01 kWh.

Power integral is an estimate from sampled kW × interval hours. It is not a
meter differential and missing samples reduce coverage; do not claim complete
electrical energy without coverage checks.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from streaming.device_simulator import ENERGY_CODE, load_daily_e01


def integrate_kwh(samples: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Left-Riemann integral per workshop using each sample's interval_seconds."""
    by_ws: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[str] = set()
    for row in samples:
        event_id = row["event_id"]
        if event_id in seen:
            raise ValueError(f"Duplicate event_id {event_id}")
        seen.add(event_id)
        power = float(row["power_kw"])
        interval = int(row["interval_seconds"])
        if not math.isfinite(power) or power < 0 or interval < 1:
            raise ValueError("Invalid power sample")
        if row.get("energy_code", ENERGY_CODE) != ENERGY_CODE:
            raise ValueError("Only E01 device samples are supported")
        by_ws[row["workshop_code"]].append(row)

    report: dict[str, dict[str, Any]] = {}
    for workshop, rows in sorted(by_ws.items()):
        energy = math.fsum(float(r["power_kw"]) * int(r["interval_seconds"]) / 3600.0
                           for r in rows)
        modes = defaultdict(int)
        devices = set()
        for row in rows:
            modes[row["mode"]] += 1
            devices.add(row["device_id"])
        intervals = {int(r["interval_seconds"]) for r in rows}
        if len(intervals) != 1:
            raise ValueError(f"{workshop} mixed interval_seconds: {sorted(intervals)}")
        interval = next(iter(intervals))
        expected_steps = int(86400 // interval)
        # expected device-steps if no misses
        device_count = len(devices)
        expected = expected_steps * device_count
        report[workshop] = {
            "workshop_code": workshop,
            "sample_count": len(rows),
            "device_count": device_count,
            "expected_samples": expected,
            "coverage": (len(rows) / expected) if expected else 0.0,
            "interval_seconds": interval,
            "estimated_kwh": energy,
            "mode_counts": dict(sorted(modes.items())),
        }
    return report


def reconcile(
    samples: list[dict[str, Any]],
    daily_targets: dict[str, float],
    *,
    business_date: date,
    relative_tolerance: float = 0.08,
    min_coverage: float = 0.90,
) -> dict[str, Any]:
    """Compare integrated device kWh to daily E01 facts."""
    if relative_tolerance < 0 or min_coverage < 0 or min_coverage > 1:
        raise ValueError("Invalid tolerance or coverage gate")
    integrated = integrate_kwh(samples)
    rows = []
    mismatches = 0
    coverage_fails = 0
    for workshop in sorted(set(integrated) | set(daily_targets)):
        detail = integrated.get(workshop)
        target = daily_targets.get(workshop)
        if detail is None or target is None:
            mismatches += 1
            rows.append({
                "workshop_code": workshop,
                "status": "missing_side",
                "estimated_kwh": None if detail is None else detail["estimated_kwh"],
                "daily_e01_kwh": target,
                "relative_error": None,
                "coverage": None if detail is None else detail["coverage"],
            })
            continue
        estimated = detail["estimated_kwh"]
        rel = abs(estimated - target) / target if target > 0 else float("inf")
        cover_ok = detail["coverage"] + 1e-12 >= min_coverage
        value_ok = rel <= relative_tolerance + 1e-12
        if not cover_ok:
            coverage_fails += 1
        if not value_ok:
            mismatches += 1
        rows.append({
            "workshop_code": workshop,
            "status": "ok" if cover_ok and value_ok else "mismatch",
            "estimated_kwh": round(estimated, 4),
            "daily_e01_kwh": round(target, 4),
            "relative_error": round(rel, 6),
            "coverage": round(detail["coverage"], 6),
            "sample_count": detail["sample_count"],
            "expected_samples": detail["expected_samples"],
            "mode_counts": detail["mode_counts"],
        })
    success = mismatches == 0 and coverage_fails == 0
    return {
        "success": success,
        "business_date": business_date.isoformat(),
        "energy_code": ENERGY_CODE,
        "relative_tolerance": relative_tolerance,
        "min_coverage": min_coverage,
        "counts": {
            "workshops": len(rows),
            "value_mismatches": mismatches,
            "coverage_failures": coverage_fails,
            "sample_rows": len(samples),
        },
        "boundary": {
            "power_integral_is_not_meter_diff": True,
            "modes_are_simulated_labels": True,
            "missing_samples_reduce_coverage": True,
        },
        "workshops": rows,
    }


def read_samples(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--daily-csv", type=Path, required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--relative-tolerance", type=float, default=0.08)
    parser.add_argument("--min-coverage", type=float, default=0.90)
    args = parser.parse_args(argv)

    business_date = date.fromisoformat(args.date)
    samples = read_samples(args.samples)
    targets = load_daily_e01(args.daily_csv, business_date)
    if not targets:
        raise SystemExit(f"No E01 rows for {business_date}")
    report = reconcile(
        samples,
        targets,
        business_date=business_date,
        relative_tolerance=args.relative_tolerance,
        min_coverage=args.min_coverage,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({"success": report["success"], "output": str(args.output),
                      "counts": report["counts"]}, ensure_ascii=False), flush=True)
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
