"""Offline guards for multi-device simulation and day reconciliation."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from streaming.device_simulator import DEVICE_FLEET, simulate_day
from streaming.reconcile_device_day import integrate_kwh, reconcile

ROOT = Path(__file__).resolve().parents[1]
SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_same_seed_is_deterministic():
    day = date(2025, 6, 15)
    targets = {ws: 1000.0 + i for i, ws in enumerate(sorted(DEVICE_FLEET))}
    a = simulate_day(day, seed=20240918, interval_seconds=600,
                     daily_targets_kwh=targets, calibrate=True)
    b = simulate_day(day, seed=20240918, interval_seconds=600,
                     daily_targets_kwh=targets, calibrate=True)
    assert a == b
    assert a[0]["event_time"].endswith("+00:00") or a[0]["event_time"].endswith("Z")


def test_modes_are_labeled_and_business_day_is_shanghai():
    day = date(2025, 6, 15)
    targets = {ws: 5000.0 for ws in DEVICE_FLEET}
    rows = simulate_day(day, seed=1, interval_seconds=1800,
                        daily_targets_kwh=targets, calibrate=True)
    assert {r["mode"] for r in rows} <= {"running", "idle", "standby", "spike"}
    assert all(r["label_boundary"] == "simulated_mode_not_verified_fault" for r in rows)
    local_hours = {
        datetime.fromisoformat(r["event_time"]).astimezone(SHANGHAI).hour for r in rows
    }
    assert min(local_hours) >= 0 and max(local_hours) <= 23
    assert all(r["business_date"] == "2025-06-15" for r in rows)


def test_calibrated_day_reconciles_within_tolerance():
    day = date(2025, 6, 15)
    targets = {ws: 10000.0 + 100 * i for i, ws in enumerate(sorted(DEVICE_FLEET))}
    rows = simulate_day(day, seed=7, interval_seconds=600, miss_rate=0.0,
                        daily_targets_kwh=targets, calibrate=True)
    report = reconcile(rows, targets, business_date=day,
                       relative_tolerance=0.01, min_coverage=0.99)
    assert report["success"] is True
    assert report["boundary"]["power_integral_is_not_meter_diff"] is True
    for item in report["workshops"]:
        assert item["relative_error"] <= 0.01 + 1e-9


def test_missing_samples_reduce_coverage_and_can_fail_gate():
    day = date(2025, 6, 15)
    targets = {ws: 8000.0 for ws in DEVICE_FLEET}
    rows = simulate_day(day, seed=3, interval_seconds=900, miss_rate=0.25,
                        daily_targets_kwh=targets, calibrate=True)
    detail = integrate_kwh(rows)
    assert all(v["coverage"] < 0.9 for v in detail.values())
    report = reconcile(rows, targets, business_date=day,
                       relative_tolerance=0.2, min_coverage=0.95)
    assert report["success"] is False
    assert report["counts"]["coverage_failures"] >= 1


def test_power_is_not_treated_as_daily_consumption_field():
    day = date(2025, 1, 2)
    targets = {ws: 1200.0 for ws in DEVICE_FLEET}
    rows = simulate_day(day, seed=9, interval_seconds=3600,
                        daily_targets_kwh=targets, calibrate=True)
    # A single power reading must not equal the daily kWh target.
    assert all(abs(r["power_kw"] - targets[r["workshop_code"]]) > 1.0 for r in rows)
    integrated = integrate_kwh(rows)
    for ws, target in targets.items():
        assert integrated[ws]["estimated_kwh"] == pytest.approx(target, rel=1e-6)


def test_verify_runtime_against_clean_energy_if_present(tmp_path):
    daily = ROOT / "output" / "clean_energy.csv"
    if not daily.is_file():
        pytest.skip("output/clean_energy.csv missing; run clean_data.py first")
    from tools.verify_device_day_runtime import run
    out = tmp_path / "device_day_runtime.json"
    evidence = run(date(2025, 6, 15), daily, out, seed=20240918,
                   interval_seconds=600, miss_rate=0.01)
    assert evidence["success"] is True
    assert out.is_file()
    assert evidence["sample_rows"] > 100
