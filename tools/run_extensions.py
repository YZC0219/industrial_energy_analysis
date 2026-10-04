"""Run the locally verifiable extension POCs, fail rather than publish partial success."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from optimization.tou import demo as optimize_demo
from optimization.tou import optimize
from optimization.forecast import demo as forecast_demo
from streaming.baseline_alert import replay
from streaming.minute_store import aggregate
from governance.catalog import catalog, query


def run(artifact_dir):
    start = datetime(2026, 9, 27, tzinfo=timezone.utc)
    points = [{"event_id": f"sample-{i}", "event_time": (start + timedelta(minutes=i)).isoformat(),
               "workshop_code": "W01", "power_kw": 100 if i < 20 else 160}
              for i in range(45)]
    alerts = replay(points, 100, 10)
    assert alerts and alerts[0]["event_time"] == (start + timedelta(minutes=25)).isoformat()
    entries = catalog()["queries"]
    for entry in entries:
        query(entry["id"], artifact_dir)
    forecast = forecast_demo()
    tariff_demo = optimize_demo()
    plan = forecast["planned_production_units"][:24]
    base = [max(0, load - units * 20) for load, units in zip(forecast["forecast_kw"][:24], plan)]
    closed_loop = optimize(tariff_demo["prices_yuan_per_kwh"], plan, [15.] * 24, base, 20., 450.)
    return {"data_source": "synthetic_except_query_artifacts",
            "streaming": {"mode": "bounded_event_time_replay", "alerts": alerts,
                          "minute_rows": aggregate(points),
                          "live_redis_clickhouse_webhook": "not_verified"},
            "forecast": forecast, "optimization": tariff_demo,
            "forecast_to_optimization": closed_loop,
            "governance": {"validated_query_artifacts": len(entries)},
            "iceberg": {"status": "requires_spark_runtime", "entrypoint": "lakehouse/iceberg_poc.py"}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, default=ROOT / "output")
    parser.add_argument("--output", type=Path, default=ROOT / "output/extensions/poc_report.json")
    args = parser.parse_args()
    report = run(args.artifact_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"POC report: {args.output}")
