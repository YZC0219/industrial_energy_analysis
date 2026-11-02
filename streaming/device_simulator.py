"""Multi-device electricity power simulator with reproducible seeds.

Emits accelerated JSONL samples (no real-time waits). Modes are simulated
labels for demo/alerting, not confirmed plant faults. Power samples are not
daily meter consumption; use streaming.reconcile_device_day for day-level
kWh comparison against warehouse E01 facts.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
SEED_DEFAULT = 20240918
ENERGY_CODE = "E01"

# Share of workshop electricity load by device. Shares within a workshop sum to 1.
DEVICE_FLEET: dict[str, list[dict[str, Any]]] = {
    "W01": [
        {"device_id": "W01-furnace", "role": "process", "share": 0.55, "idle_frac": 0.08},
        {"device_id": "W01-drive", "role": "drive", "share": 0.30, "idle_frac": 0.05},
        {"device_id": "W01-aux", "role": "aux", "share": 0.15, "idle_frac": 0.12},
    ],
    "W02": [
        {"device_id": "W02-mill", "role": "process", "share": 0.60, "idle_frac": 0.07},
        {"device_id": "W02-drive", "role": "drive", "share": 0.25, "idle_frac": 0.05},
        {"device_id": "W02-aux", "role": "aux", "share": 0.15, "idle_frac": 0.10},
    ],
    "W03": [
        {"device_id": "W03-heater", "role": "process", "share": 0.70, "idle_frac": 0.10},
        {"device_id": "W03-fan", "role": "aux", "share": 0.30, "idle_frac": 0.08},
    ],
    "W04": [
        {"device_id": "W04-cnc", "role": "process", "share": 0.65, "idle_frac": 0.06},
        {"device_id": "W04-compressor", "role": "aux", "share": 0.35, "idle_frac": 0.15},
    ],
    "W05": [
        {"device_id": "W05-line", "role": "process", "share": 0.58, "idle_frac": 0.09},
        {"device_id": "W05-pump", "role": "aux", "share": 0.42, "idle_frac": 0.11},
    ],
    "W06": [
        {"device_id": "W06-assembler", "role": "process", "share": 0.62, "idle_frac": 0.07},
        {"device_id": "W06-light", "role": "aux", "share": 0.38, "idle_frac": 0.20},
    ],
    "W07": [
        {"device_id": "W07-boiler-feed", "role": "process", "share": 0.50, "idle_frac": 0.12},
        {"device_id": "W07-pump", "role": "aux", "share": 0.30, "idle_frac": 0.10},
        {"device_id": "W07-control", "role": "aux", "share": 0.20, "idle_frac": 0.05},
    ],
    "W08": [
        {"device_id": "W08-packer", "role": "process", "share": 0.70, "idle_frac": 0.08},
        {"device_id": "W08-conveyor", "role": "drive", "share": 0.30, "idle_frac": 0.06},
    ],
}


def _validate_fleet(fleet: dict[str, list[dict[str, Any]]]) -> None:
    for workshop, devices in fleet.items():
        total = math.fsum(float(d["share"]) for d in devices)
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"{workshop} device shares must sum to 1, got {total}")
        ids = [d["device_id"] for d in devices]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{workshop} has duplicate device_id")


_validate_fleet(DEVICE_FLEET)


def load_daily_e01(path: Path, business_date: date) -> dict[str, float]:
    """Read workshop → E01 kWh for one business day from clean_energy.csv."""
    targets: dict[str, float] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("energy_code") != ENERGY_CODE:
                continue
            if row.get("record_date") != business_date.isoformat():
                continue
            workshop = row["workshop_code"]
            value = float(row["consumption"])
            if workshop in targets:
                raise ValueError(f"Duplicate E01 row for {workshop} on {business_date}")
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"Invalid E01 consumption for {workshop}")
            targets[workshop] = value
    return targets


def _mode_for(hour: int, rng: random.Random, production_day: bool) -> str:
    if not production_day:
        return "standby"
    if hour < 6 or hour >= 22:
        return "idle" if rng.random() < 0.7 else "standby"
    if rng.random() < 0.015:
        return "spike"  # simulated fault-like spike; not a verified plant event
    return "running"


def _mode_factor(mode: str, idle_frac: float) -> float:
    if mode == "running":
        return 1.0
    if mode == "idle":
        return idle_frac
    if mode == "standby":
        return max(0.02, idle_frac * 0.4)
    if mode == "spike":
        return 1.8
    raise ValueError(f"Unknown mode {mode}")


def day_bounds(business_date: date) -> tuple[datetime, datetime]:
    start = datetime(business_date.year, business_date.month, business_date.day,
                     tzinfo=SHANGHAI)
    return start, start + timedelta(days=1)


def simulate_day(
    business_date: date,
    *,
    seed: int = SEED_DEFAULT,
    interval_seconds: int = 60,
    miss_rate: float = 0.0,
    clock_drift_seconds: float = 0.0,
    daily_targets_kwh: dict[str, float] | None = None,
    calibrate: bool = True,
    production_day: bool = True,
    fleet: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Return device-level power samples for one Asia/Shanghai business day."""
    if interval_seconds < 1:
        raise ValueError("interval_seconds must be >= 1")
    if not 0.0 <= miss_rate < 1.0:
        raise ValueError("miss_rate must be in [0, 1)")
    if clock_drift_seconds < 0:
        raise ValueError("clock_drift_seconds must be >= 0")
    fleet = fleet or DEVICE_FLEET
    _validate_fleet(fleet)
    start, end = day_bounds(business_date)
    rng = random.Random(seed)
    if calibrate:
        if not daily_targets_kwh:
            raise ValueError("calibrate=True requires daily_targets_kwh")
        missing = sorted(set(fleet) - set(daily_targets_kwh))
        if missing:
            raise ValueError("Missing E01 targets for workshops: " + ", ".join(missing))

    # Unit profile: workshop mean power = 1 kW. After modes/noise, scale so the
    # left-Riemann integral matches daily E01 (or leave free-running).
    unit_mean_kw = 1.0
    steps = int((end - start).total_seconds() // interval_seconds)
    raw: list[dict[str, Any]] = []
    for step in range(steps):
        local = start + timedelta(seconds=step * interval_seconds)
        if local >= end:
            break
        for workshop, devices in fleet.items():
            for device in devices:
                if rng.random() < miss_rate:
                    continue
                mode = _mode_for(local.hour, rng, production_day)
                noise = 1.0 + rng.uniform(-0.03, 0.03)
                power = (unit_mean_kw * float(device["share"])
                         * _mode_factor(mode, float(device["idle_frac"])) * noise)
                if not math.isfinite(power) or power < 0:
                    raise ValueError("Invalid simulated power")
                drift = rng.uniform(0.0, clock_drift_seconds) if clock_drift_seconds else 0.0
                event_time = (local + timedelta(seconds=drift)).astimezone(timezone.utc)
                raw.append({
                    "event_id": f"{device['device_id']}-{event_time.strftime('%Y%m%dT%H%M%S')}-"
                                f"{step}-{seed}",
                    "event_time": event_time.isoformat(),
                    "business_date": business_date.isoformat(),
                    "workshop_code": workshop,
                    "device_id": device["device_id"],
                    "device_role": device["role"],
                    "energy_code": ENERGY_CODE,
                    "power_kw": power,
                    "mode": mode,
                    "interval_seconds": interval_seconds,
                    "seed": seed,
                    "calibrated": bool(calibrate),
                    "label_boundary": "simulated_mode_not_verified_fault",
                })

    integrals: dict[str, float] = defaultdict(float)
    for row in raw:
        integrals[row["workshop_code"]] += (
            float(row["power_kw"]) * int(row["interval_seconds"]) / 3600.0)

    if calibrate:
        assert daily_targets_kwh is not None
        factors = {}
        for workshop in fleet:
            base = integrals.get(workshop, 0.0)
            if base <= 0:
                raise ValueError(f"No samples to calibrate for {workshop}")
            factors[workshop] = float(daily_targets_kwh[workshop]) / base
    else:
        # Free-running demo load ~ plant-scale average (kWh/day ≈ mean_kw * hours).
        # Keep relative device shares; absolute level is illustrative only.
        demo_daily_kwh = {ws: (800.0 + 100.0 * idx) for idx, ws in enumerate(sorted(fleet))}
        factors = {}
        for workshop in fleet:
            base = integrals.get(workshop, 0.0)
            if base <= 0:
                raise ValueError(f"No samples for {workshop}")
            factors[workshop] = demo_daily_kwh[workshop] / base

    samples: list[dict[str, Any]] = []
    for row in raw:
        scaled = dict(row)
        scaled["power_kw"] = round(float(row["power_kw"]) * factors[row["workshop_code"]], 6)
        samples.append(scaled)
    samples.sort(key=lambda row: (row["event_time"], row["device_id"]))
    return samples


def iter_jsonl(rows: list[dict[str, Any]]) -> Iterator[str]:
    for row in rows:
        yield json.dumps(row, ensure_ascii=False, allow_nan=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="Business date YYYY-MM-DD (Asia/Shanghai)")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=SEED_DEFAULT)
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument("--miss-rate", type=float, default=0.0)
    parser.add_argument("--clock-drift-seconds", type=float, default=0.0)
    parser.add_argument("--daily-csv", type=Path,
                        help="clean_energy.csv for E01 calibration targets")
    parser.add_argument("--no-calibrate", action="store_true",
                        help="Do not scale to daily E01; report free-running load")
    parser.add_argument("--standby-day", action="store_true",
                        help="Force whole-day standby/idle profile")
    parser.add_argument("--bootstrap-servers",
                        help="Optional Kafka bootstrap; publish is opt-in only")
    args = parser.parse_args(argv)

    business_date = date.fromisoformat(args.date)
    targets = None
    calibrate = not args.no_calibrate
    if calibrate:
        if not args.daily_csv:
            parser.error("--daily-csv is required unless --no-calibrate is set")
        targets = load_daily_e01(args.daily_csv, business_date)
        if not targets:
            raise SystemExit(f"No E01 rows for {business_date} in {args.daily_csv}")

    rows = simulate_day(
        business_date,
        seed=args.seed,
        interval_seconds=args.interval_seconds,
        miss_rate=args.miss_rate,
        clock_drift_seconds=args.clock_drift_seconds,
        daily_targets_kwh=targets,
        calibrate=calibrate,
        production_day=not args.standby_day,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    producer = None
    if args.bootstrap_servers:
        from kafka import KafkaProducer
        producer = KafkaProducer(bootstrap_servers=args.bootstrap_servers, acks="all")
    try:
        with args.output.open("w", encoding="utf-8") as sink:
            for row, line in zip(rows, iter_jsonl(rows)):
                sink.write(line + "\n")
                if producer:
                    producer.send(
                        "energy-device-power-samples",
                        key=row["workshop_code"].encode(),
                        value=line.encode(),
                    ).get(timeout=30)
        if producer:
            producer.flush(timeout=30)
    finally:
        if producer:
            producer.close(timeout=30)
    print(f"wrote {len(rows)} samples -> {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
