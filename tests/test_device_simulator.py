"""Offline guards for multi-device simulation, minute rollup, and day reconciliation.

Expected values are constructed independently in each test; they are not taken
from the code under test's own summary fields as ground truth.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from streaming.device_simulator import DEVICE_FLEET, simulate_day
from streaming.minute_store import (
    aggregate_device_minutes,
    integrate_workshop_minutes_kwh,
    rollup_workshop_minutes,
)
from streaming.reconcile_device_day import (
    build_day_evidence,
    expected_samples_for_workshop,
    integrate_kwh,
    reconcile,
    sample_quality,
)

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
    assert report["boundary"]["post_hoc_e01_calibration_is_not_forecast"] is True
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
    assert all(abs(r["power_kw"] - targets[r["workshop_code"]]) > 1.0 for r in rows)
    integrated = integrate_kwh(rows)
    for ws, target in targets.items():
        assert integrated[ws]["estimated_kwh"] == pytest.approx(target, rel=1e-6)


def test_workshop_minute_sums_device_means_not_flat_average():
    """Independent expectation: 10 kW + 20 kW devices => 30 kW workshop total."""
    minute = "2025-06-15T01:00:00+00:00"
    points = [
        {"event_id": "a", "event_time": minute, "workshop_code": "W01",
         "device_id": "W01-furnace", "power_kw": 10.0, "interval_seconds": 60,
         "mode": "running", "energy_code": "E01"},
        {"event_id": "b", "event_time": minute, "workshop_code": "W01",
         "device_id": "W01-drive", "power_kw": 20.0, "interval_seconds": 60,
         "mode": "running", "energy_code": "E01"},
    ]
    device_minutes = aggregate_device_minutes(points)
    assert len(device_minutes) == 2
    assert {row["mean_kw"] for row in device_minutes} == {10.0, 20.0}
    workshop = rollup_workshop_minutes(device_minutes)
    assert len(workshop) == 1
    # Flat mean of samples would be 15; correct workshop total is 30.
    assert workshop[0]["total_kw"] == pytest.approx(30.0)
    assert workshop[0]["total_kw"] != pytest.approx(15.0)
    assert workshop[0]["aggregation"] == "sum_of_device_minute_means"
    assert workshop[0]["unit"] == "kW"
    assert workshop[0]["window"] == "utc_minute_floor"


def test_missing_device_is_omitted_not_zero_filled():
    points = [{
        "event_id": "only",
        "event_time": "2025-06-15T02:00:00+00:00",
        "workshop_code": "W01",
        "device_id": "W01-furnace",
        "power_kw": 40.0,
        "interval_seconds": 60,
        "mode": "running",
        "energy_code": "E01",
    }]
    device_minutes = aggregate_device_minutes(points)
    workshop = rollup_workshop_minutes(device_minutes)
    assert workshop[0]["device_count"] == 1
    assert workshop[0]["total_kw"] == pytest.approx(40.0)
    assert "W01-drive" not in workshop[0]["device_ids"]


def test_duplicates_do_not_inflate_coverage_above_one():
    day = date(2025, 6, 15)
    targets = {ws: 5000.0 for ws in DEVICE_FLEET}
    rows = simulate_day(day, seed=11, interval_seconds=3600, miss_rate=0.0,
                        daily_targets_kwh=targets, calibrate=True)
    expected_w01 = expected_samples_for_workshop("W01", 3600)
    assert expected_w01 == len(DEVICE_FLEET["W01"]) * 24
    bloated = rows + rows  # full replay
    quality = sample_quality(bloated)
    assert quality["duplicate_rows"] == len(rows)
    assert quality["unique_rows"] == len(rows)
    w01 = next(item for item in quality["workshops"] if item["workshop_code"] == "W01")
    assert w01["actual_unique_samples"] == expected_w01
    assert w01["coverage"] == pytest.approx(1.0)
    assert w01["coverage"] <= 1.0 + 1e-12


def test_out_of_order_samples_match_sorted_integral():
    day = date(2025, 6, 15)
    targets = {ws: 3000.0 for ws in DEVICE_FLEET}
    rows = simulate_day(day, seed=13, interval_seconds=1800, miss_rate=0.0,
                        daily_targets_kwh=targets, calibrate=True)
    shuffled = list(reversed(rows))
    assert shuffled[0]["event_time"] >= shuffled[-1]["event_time"] or True
    sorted_energy = integrate_kwh(rows)
    shuffled_energy = integrate_kwh(shuffled)
    for ws in DEVICE_FLEET:
        assert shuffled_energy[ws]["estimated_kwh"] == pytest.approx(
            sorted_energy[ws]["estimated_kwh"], rel=1e-12)


def test_raw_and_minute_integrals_agree_on_complete_day():
    day = date(2025, 6, 15)
    # Independent daily reference constructed here (not from DUT output).
    targets = {ws: 4000.0 + 50 * i for i, ws in enumerate(sorted(DEVICE_FLEET))}
    rows = simulate_day(day, seed=17, interval_seconds=600, miss_rate=0.0,
                        daily_targets_kwh=targets, calibrate=True)
    evidence = build_day_evidence(rows, targets, business_date=day,
                                  relative_tolerance=0.01, min_coverage=0.99)
    assert evidence["success"] is True
    for row in evidence["workshops"]:
        assert row["raw_vs_minute_rel_to_daily"] <= 1e-6 + 1e-12
        assert row["daily_e01_kwh"] == pytest.approx(targets[row["workshop_code"]])
        assert row["raw_integral_kwh"] == pytest.approx(targets[row["workshop_code"]], rel=1e-6)
        assert row["minute_integral_kwh"] == pytest.approx(targets[row["workshop_code"]], rel=1e-6)


def test_minute_integral_uses_represented_seconds_not_assumed_sixty():
    """Hand-built 5-minute samples: 12 kW for 300 s => 1 kWh, not 12/60 kWh."""
    points = [{
        "event_id": "tick",
        "event_time": "2025-06-15T00:00:00+00:00",
        "workshop_code": "W08",
        "device_id": "W08-packer",
        "power_kw": 12.0,
        "interval_seconds": 300,
        "mode": "running",
        "energy_code": "E01",
    }]
    expected_kwh = 12.0 * 300 / 3600.0
    assert expected_kwh == pytest.approx(1.0)
    device_minutes = aggregate_device_minutes(points)
    workshop_minutes = rollup_workshop_minutes(device_minutes)
    got = integrate_workshop_minutes_kwh(workshop_minutes)
    assert got["W08"] == pytest.approx(expected_kwh)
    assert got["W08"] != pytest.approx(12.0 / 60.0)


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
    assert Path(evidence["device_minutes_path"]).is_file()
    assert Path(evidence["workshop_minutes_path"]).is_file()
    workshops = evidence["reconciliation"]["workshops"]
    assert len(workshops) == 8
    assert all(math.isfinite(row["coverage"]) for row in workshops)


def test_subminute_missing_samples_preserve_observed_energy():
    points = [dict(event_id=str(i), event_time=f"2025-06-15T00:00:{i*10:02d}+00:00",
                   workshop_code="W01", device_id="a", power_kw=12.0, interval_seconds=10)
              for i in range(6)]
    points.append(dict(event_id="other", event_time="2025-06-15T00:00:00+00:00",
                       workshop_code="W01", device_id="b", power_kw=24.0, interval_seconds=10))
    minutes = rollup_workshop_minutes(aggregate_device_minutes(points))
    assert integrate_workshop_minutes_kwh(minutes)["W01"] == pytest.approx((12*60+24*10)/3600)
