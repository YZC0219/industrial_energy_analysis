"""Reconcile device power samples to warehouse daily E01 kWh.

Compares three quantities per workshop:
1. raw left-Riemann integral from unique device samples
2. integral from workshop minute totals (sum of device means)
3. daily E01 reference kWh from the warehouse CSV

Power integrals are not meter differentials. Missing samples are omitted (not
zero-filled). Duplicate event_ids are dropped before coverage is computed so
coverage cannot exceed 100% via replay. Post-hoc E01 calibration in the
simulator is a demo alignment, not an independent forecast or plant validation.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from streaming.device_simulator import DEVICE_FLEET, ENERGY_CODE, load_daily_e01
from streaming.minute_store import (
    aggregate_device_minutes,
    dedupe_event_ids,
    integrate_workshop_minutes_kwh,
    rollup_workshop_minutes,
)


def expected_samples_for_workshop(workshop: str, interval_seconds: int,
                                  fleet: dict[str, list[dict[str, Any]]] | None = None) -> int:
    fleet = fleet or DEVICE_FLEET
    if workshop not in fleet:
        raise ValueError(f"Unknown workshop {workshop}")
    if interval_seconds < 1:
        raise ValueError("interval_seconds must be >= 1")
    steps = 86400 // interval_seconds
    return len(fleet[workshop]) * steps


def sample_quality(samples: list[dict[str, Any]],
                   *,
                   fleet: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    """Unique/duplicate/miss/coverage stats with fleet-based expected counts."""
    fleet = fleet or DEVICE_FLEET
    unique, duplicates = dedupe_event_ids(samples)
    if not unique:
        raise ValueError("No samples after deduplication")
    intervals = {int(row["interval_seconds"]) for row in unique}
    if len(intervals) != 1:
        raise ValueError(f"Mixed interval_seconds: {sorted(intervals)}")
    interval = next(iter(intervals))

    by_ws: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in unique:
        by_ws[row["workshop_code"]].append(row)

    workshops = []
    for workshop in sorted(set(fleet) | set(by_ws)):
        rows = by_ws.get(workshop, [])
        expected = expected_samples_for_workshop(workshop, interval, fleet)
        actual_unique = len(rows)
        miss = max(expected - actual_unique, 0)
        coverage = min(actual_unique / expected, 1.0) if expected else 0.0
        workshops.append({
            "workshop_code": workshop,
            "expected_samples": expected,
            "actual_unique_samples": actual_unique,
            "duplicate_samples_ignored": 0,  # filled below for totals only
            "missed_samples": miss,
            "miss_rate": (miss / expected) if expected else 0.0,
            "coverage": coverage,
        })
    return {
        "interval_seconds": interval,
        "input_rows": len(samples),
        "unique_rows": len(unique),
        "duplicate_rows": duplicates,
        "coverage_capped_at_one": True,
        "workshops": workshops,
        "unique_samples": unique,
    }


def integrate_kwh(samples: list[dict[str, Any]],
                  *,
                  fleet: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, dict[str, Any]]:
    """Left-Riemann integral per workshop from unique event_ids only."""
    quality = sample_quality(samples, fleet=fleet)
    unique = quality["unique_samples"]
    by_ws: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in unique:
        power = float(row["power_kw"])
        interval = int(row["interval_seconds"])
        if not math.isfinite(power) or power < 0 or interval < 1:
            raise ValueError("Invalid power sample")
        if row.get("energy_code", ENERGY_CODE) != ENERGY_CODE:
            raise ValueError("Only E01 device samples are supported")
        by_ws[row["workshop_code"]].append(row)

    quality_by_ws = {row["workshop_code"]: row for row in quality["workshops"]}
    report: dict[str, dict[str, Any]] = {}
    for workshop, rows in sorted(by_ws.items()):
        energy = math.fsum(float(r["power_kw"]) * int(r["interval_seconds"]) / 3600.0
                           for r in rows)
        modes = defaultdict(int)
        devices = set()
        for row in rows:
            modes[row["mode"]] += 1
            devices.add(row["device_id"])
        stats = quality_by_ws[workshop]
        report[workshop] = {
            "workshop_code": workshop,
            "sample_count": stats["actual_unique_samples"],
            "expected_samples": stats["expected_samples"],
            "missed_samples": stats["missed_samples"],
            "miss_rate": stats["miss_rate"],
            "coverage": stats["coverage"],
            "device_count": len(devices),
            "interval_seconds": quality["interval_seconds"],
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
    fleet: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Compare raw unique-sample integrals to daily E01 facts."""
    if relative_tolerance < 0 or min_coverage < 0 or min_coverage > 1:
        raise ValueError("Invalid tolerance or coverage gate")
    quality = sample_quality(samples, fleet=fleet)
    integrated = integrate_kwh(samples, fleet=fleet)
    rows = []
    mismatches = 0
    coverage_fails = 0
    for workshop in sorted(set(integrated) | set(daily_targets) | set(DEVICE_FLEET if fleet is None else fleet)):
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
            "missed_samples": detail["missed_samples"],
            "miss_rate": round(detail["miss_rate"], 6),
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
            "unique_rows": quality["unique_rows"],
            "duplicate_rows": quality["duplicate_rows"],
        },
        "boundary": {
            "power_integral_is_not_meter_diff": True,
            "modes_are_simulated_labels": True,
            "missing_samples_reduce_coverage": True,
            "missing_not_zero_filled": True,
            "post_hoc_e01_calibration_is_not_forecast": True,
            "coverage_cannot_exceed_one_via_duplicates": True,
        },
        "workshops": rows,
    }


def build_day_evidence(
    samples: list[dict[str, Any]],
    daily_targets: dict[str, float],
    *,
    business_date: date,
    relative_tolerance: float = 0.08,
    min_coverage: float = 0.90,
    fleet: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Full evidence: quality, raw integral, minute path, and daily E01 compare."""
    fleet = fleet or DEVICE_FLEET
    quality = sample_quality(samples, fleet=fleet)
    unique = quality["unique_samples"]
    raw = integrate_kwh(samples, fleet=fleet)
    device_minutes = aggregate_device_minutes(unique)
    workshop_minutes = rollup_workshop_minutes(device_minutes)
    minute_kwh = integrate_workshop_minutes_kwh(workshop_minutes)
    base = reconcile(
        samples,
        daily_targets,
        business_date=business_date,
        relative_tolerance=relative_tolerance,
        min_coverage=min_coverage,
        fleet=fleet,
    )

    workshops = []
    minute_mismatches = 0
    for workshop in sorted(set(daily_targets) | set(fleet)):
        target = daily_targets.get(workshop)
        raw_detail = raw.get(workshop)
        raw_kwh = None if raw_detail is None else raw_detail["estimated_kwh"]
        min_kwh = minute_kwh.get(workshop)
        q = next(row for row in quality["workshops"] if row["workshop_code"] == workshop)
        if target is None or raw_kwh is None or min_kwh is None:
            minute_mismatches += 1
            status = "missing_side"
            raw_vs_minute = None
            minute_vs_daily = None
            raw_vs_daily = None
        else:
            raw_vs_minute = abs(raw_kwh - min_kwh) / target if target else float("inf")
            minute_vs_daily = abs(min_kwh - target) / target if target else float("inf")
            raw_vs_daily = abs(raw_kwh - target) / target if target else float("inf")
            cover_ok = q["coverage"] + 1e-12 >= min_coverage
            value_ok = (raw_vs_daily <= relative_tolerance + 1e-12
                        and minute_vs_daily <= relative_tolerance + 1e-12)
            # Raw and minute paths should match closely when represented_seconds
            # mirrors sample intervals; allow a tight numeric band.
            path_ok = raw_vs_minute <= 1e-6 + 1e-12
            if not path_ok or not value_ok:
                minute_mismatches += 1
            status = "ok" if cover_ok and value_ok and path_ok else "mismatch"
        workshops.append({
            "workshop_code": workshop,
            "status": status,
            "daily_e01_kwh": None if target is None else round(target, 4),
            "raw_integral_kwh": None if raw_kwh is None else round(raw_kwh, 4),
            "minute_integral_kwh": None if min_kwh is None else round(min_kwh, 4),
            "raw_vs_minute_rel_to_daily": None if raw_vs_minute is None else round(raw_vs_minute, 9),
            "minute_vs_daily_rel": None if minute_vs_daily is None else round(minute_vs_daily, 6),
            "raw_vs_daily_rel": None if raw_vs_daily is None else round(raw_vs_daily, 6),
            "expected_samples": q["expected_samples"],
            "actual_unique_samples": q["actual_unique_samples"],
            "missed_samples": q["missed_samples"],
            "miss_rate": round(q["miss_rate"], 6),
            "coverage": round(q["coverage"], 6),
            "error_sources": [
                "post_hoc_e01_scale_aligns_raw_integral_when_calibrated",
                "missing_samples_omit_energy_not_zero_fill",
                "minute_path_sums_device_means_then_applies_represented_seconds",
                "not_a_utility_meter_differential",
            ],
        })

    success = base["success"] and minute_mismatches == 0
    return {
        **base,
        "success": success,
        "counts": {
            **base["counts"],
            "minute_path_mismatches": minute_mismatches,
            "device_minute_rows": len(device_minutes),
            "workshop_minute_rows": len(workshop_minutes),
        },
        "quality": {
            "input_rows": quality["input_rows"],
            "unique_rows": quality["unique_rows"],
            "duplicate_rows": quality["duplicate_rows"],
            "interval_seconds": quality["interval_seconds"],
            "coverage_capped_at_one": True,
        },
        "method": {
            "device_minute": "mean_of_unique_device_samples over utc_minute_floor; unit kW",
            "workshop_minute": "sum_of_device_minute_means; unit kW; missing devices omitted",
            "raw_integral": "sum(power_kw * interval_seconds / 3600) over unique event_ids",
            "minute_integral": "sum(total_kw * represented_seconds / 3600) over workshop minutes",
            "daily_reference": "clean_energy.csv E01 consumption kWh for the business date",
        },
        "workshops": workshops,
        "artifacts": {
            "device_minutes": device_minutes,
            "workshop_minutes": workshop_minutes,
        },
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
    parser.add_argument("--with-minutes", action="store_true",
                        help="Include device/workshop minute path in the report")
    args = parser.parse_args(argv)

    business_date = date.fromisoformat(args.date)
    samples = read_samples(args.samples)
    targets = load_daily_e01(args.daily_csv, business_date)
    if not targets:
        raise SystemExit(f"No E01 rows for {business_date}")
    if args.with_minutes:
        report = build_day_evidence(
            samples,
            targets,
            business_date=business_date,
            relative_tolerance=args.relative_tolerance,
            min_coverage=args.min_coverage,
        )
        # Strip bulky minute arrays from the JSON file unless caller wants them
        # elsewhere; keep counts and workshop summary.
        report = dict(report)
        report.pop("artifacts", None)
    else:
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
