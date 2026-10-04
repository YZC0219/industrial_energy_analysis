from datetime import datetime, timedelta, timezone
from pathlib import Path
import math

import pytest
from fastapi.testclient import TestClient

from governance.catalog import catalog, metric
from optimization.tou import optimize, demo
from streaming.baseline_alert import replay
from tools.run_extensions import run
from src.api import app
from tools.check_energy_coverage import validate
from streaming.minute_store import aggregate
from streaming.dispatch import deliver
from tools.verify_power_runtime import expected
from streaming.simulate_power import samples
from tools.summarize_extension_runtime import validate as validate_runtime
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def test_optimization_conserves_production_and_limits():
    result = demo()
    assert sum(result["optimized_units"]) == pytest.approx(result["production_units"])
    assert max(result["optimized_kw"]) <= 400 + 1e-6
    assert result["optimized_cost_yuan"] <= result["baseline_cost_yuan"]
    assert result["scenario_monthly_saving_yuan"] == pytest.approx(30 * result["daily_saving_yuan"])


def test_reject_invalid_and_infeasible_baseline():
    with pytest.raises(ValueError):
        optimize([.7] * 24, [20.] * 24, [10.] * 24, [100.] * 24, 20, 400)
    with pytest.raises(ValueError):
        optimize([math.nan] * 24, [5.] * 24, [10.] * 24, [100.] * 24, 20, 400)


def test_replay_orders_event_time_and_isolates_workshops():
    start = datetime(2026, 9, 27, tzinfo=timezone.utc)
    points = [{"event_id": str(i), "event_time": (start + timedelta(minutes=i)).isoformat(),
               "workshop_code": "W01", "power_kw": 150} for i in range(15)]
    other = {**points[0], "workshop_code": "W02", "power_kw": 10}
    assert len(replay(list(reversed(points)) + [other], 100, 10)) == 1
    with pytest.raises(ValueError):
        replay(points + [points[0]], 100, 10)


def test_semantic_ratio_uses_sums_and_excludes_utilities(tmp_path):
    filename = next(q["artifact"] for q in catalog()["queries"] if q["id"] == "Q28")
    (tmp_path / filename).write_text("日期,车间编码,工序,综合能耗_tce,产量\n"
        "2026-09-27,W01,熔炼,1,10\n2026-09-28,W01,熔炼,3,90\n"
        "2026-09-27,W07,公用工程,99,0\n", encoding="utf-8")
    assert metric("energy_intensity_kgce", tmp_path)["value"] == 40
    assert metric("net_energy_tce", tmp_path)["value"] == 4
    assert metric("total_energy_tce", tmp_path)["value"] == 103


def test_semantic_api_unknown_and_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("ENERGY_OUTPUT_DIR", str(tmp_path))
    client = TestClient(app)
    assert len(client.get("/api/v1/semantic/catalog").json()["queries"]) == 29
    assert client.get("/api/v1/semantic/queries/Q99").status_code == 404
    assert client.get("/api/v1/semantic/queries/Q28").status_code == 503
    assert client.get("/api/v1/semantic/metrics/net_energy_tce?date_from=2026-09-28&date_to=2026-09-27").status_code == 422


def test_all_local_pocs():
    result = run(ROOT / "tests/baseline")
    assert result["governance"]["validated_query_artifacts"] == 29
    assert result["forecast"]["forecast_horizon_hours"] == 72
    assert result["forecast_to_optimization"]["daily_saving_yuan"] >= -1e-6
    assert result["iceberg"]["status"] == "requires_spark_runtime"


def test_coverage_detects_missing_entire_workshop_day():
    energy = pd.DataFrame([{"record_date": "2026-09-27", "workshop_code": "W01", "energy_code": "E01"}])
    production = pd.DataFrame([{"record_date": "2026-09-27", "workshop_code": w} for w in ["W01", "W02"]])
    result = validate(energy, production, {"version": 1, "workshops": {"W01": ["E01"], "W02": ["E01"]}})
    assert not result["success"]
    assert result["failed_workshop_days"] == 1
    assert result["samples"][0]["workshop_code"] == "W02"


def test_minute_downsampling_utc_and_local_dispatch():
    points = [{"event_id": str(i), "workshop_code": "W01",
               "event_time": f"2026-09-27T08:00:{i * 30:02d}+08:00", "power_kw": v}
              for i, v in enumerate([100, 200])]
    assert aggregate(points) == [{"workshop_code": "W01", "minute": "2026-09-27 00:00:00.000",
                                  "mean_kw": 150., "sample_count": 2}]
    assert deliver({"x": 1, "y": 2})["alert_id"] == deliver({"y": 2, "x": 1})["alert_id"]


def test_flink_reference_uses_half_open_aligned_windows():
    windows = expected(list(samples(datetime(2026, 9, 27, tzinfo=timezone.utc))))
    assert len(windows) == 20
    assert windows["2026-09-27T00:26:00.000"][0] == 124
    assert windows["2026-09-27T00:45:00.000"] == (160., 0., 15)


def test_runtime_summary_rejects_stale_sql_and_missing_gate_evidence():
    power = {"success": True, "sql_sha256": "sql", "expected_windows": 20,
             "observed_windows": 20, "mean_slope_count_reconciled": True, "cross_workshop_isolation": True}
    iceberg = {"status": "passed", "source_sha256": "ice", "current_consumption": 120,
               "historical_consumption": 100, "replay_count": 1, "snapshots": [1]}
    airflow = {"success": True, "cases": [
        {"mode": "good", "task_states": {"coverage_gate": "success", "downstream_report": "success"}, "downstream_file_exists": True},
        {"mode": "missing_energy", "task_states": {"coverage_gate": "failed", "downstream_report": "upstream_failed"}, "downstream_file_exists": False}]}
    validate_runtime(power, iceberg, airflow, "sql", "ice")
    with pytest.raises(ValueError, match="stale"):
        validate_runtime(power, iceberg, airflow, "new-sql", "ice")
    airflow["cases"].pop()
    with pytest.raises(ValueError, match="incomplete"):
        validate_runtime(power, iceberg, airflow, "sql", "ice")
