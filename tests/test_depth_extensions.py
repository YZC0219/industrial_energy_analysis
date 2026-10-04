import copy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
import numpy as np
from fastapi.testclient import TestClient
from governance.catalog import catalog, metric, query
from optimization.equipment_schedule import optimize_equipment, demo_inputs
from streaming.alert_outbox import Outbox
from streaming.alert_quarantine import Quarantine, persist_or_quarantine
from src.api import app

ROOT = Path(__file__).resolve().parents[1]


def test_all_analytical_definitions_and_api(monkeypatch):
    c = catalog()
    assert len([m for m in c["metrics"] if m["aggregation"] == "sql_result"]) == 29
    monkeypatch.setenv("ENERGY_OUTPUT_DIR", str(ROOT / "tests/baseline"))
    client = TestClient(app)
    for entry in c["queries"]:
        response = client.get("/api/v1/semantic/metrics/analysis_" + entry["id"])
        assert response.status_code == 200
        assert response.json()["rows"] == query(entry["id"], ROOT / "tests/baseline")["rows"]
    assert client.get("/api/v1/semantic/metrics/analysis_Q03?workshop_code=W01").status_code == 422


def test_semantic_schema_drift_and_nonfinite_rejected(tmp_path):
    entry = next(q for q in catalog()["queries"] if q["id"] == "Q01")
    path = tmp_path / entry["artifact"]
    path.write_text("wrong\n1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="schema"):
        query("Q01", tmp_path)
    import csv
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow([c["name"] for c in entry["columns"]])
        writer.writerow(["nan"] * len(entry["columns"]))
    with pytest.raises(ValueError):
        query("Q01", tmp_path)


def test_derived_cost_and_carbon_metrics_use_filtered_sum(tmp_path):
    entry = next(q for q in catalog()["queries"] if q["id"] == "Q28")
    (tmp_path / entry["artifact"]).write_text("日期,车间编码,工序,能源费用_元,碳排放_tCO2\n2026-10-01,W01,熔炼,10,2\n2026-10-01,W07,公用工程,90,8\n", encoding="utf-8")
    assert metric("net_cost_yuan", tmp_path)["value"] == 10
    assert metric("total_carbon_tco2", tmp_path)["value"] == 10


def test_intensity_cannot_add_incompatible_workshop_production(tmp_path):
    entry = next(q for q in catalog()["queries"] if q["id"] == "Q28")
    (tmp_path / entry["artifact"]).write_text("日期,车间编码,工序,综合能耗_tce,产量\n2026-10-01,W01,熔炼,1,10\n2026-10-01,W02,加工,1,100\n", encoding="utf-8")
    with pytest.raises(ValueError, match="one workshop"):
        metric("energy_intensity_kgce", tmp_path)
    assert metric("energy_intensity_kgce", tmp_path, workshop_code="W01")["value"] == 100


def test_quarantine_commits_after_durable_write_and_repair_is_idempotent(tmp_path):
    queue = Outbox(tmp_path / "queue.sqlite", "http://localhost")
    fingerprint = queue.db.execute("SELECT fingerprint FROM route").fetchone()[0]
    quarantine = Quarantine(tmp_path / "bad.sqlite", fingerprint)
    message = SimpleNamespace(topic="alerts", partition=0, offset=4, value=b"bad-json")
    commits = []
    def commit(offsets):
        second = Quarantine(tmp_path / "bad.sqlite", fingerprint)
        assert second.db.execute("SELECT count(*) FROM rejected").fetchone()[0] == 1
        second.db.close()
        commits.append(offsets)
    factory = lambda *a: tuple(a)
    outcome, ident = persist_or_quarantine(queue, quarantine, "test-group", SimpleNamespace(commit=commit), message, factory, factory)
    assert outcome == "quarantined" and commits == [{("alerts", 0): (5, "", -1)}]
    assert quarantine.replay(ident, {"power": 100}, queue) == quarantine.replay(ident, {"power": 100}, queue)
    assert queue.stats() == {"pending": 1}
    assert quarantine.db.execute("SELECT raw,state FROM rejected").fetchone()[:] == (b"bad-json", "replayed")
    with pytest.raises(ValueError, match="different repair"):
        quarantine.replay(ident, {"power": 200}, queue)
    other = Outbox(tmp_path / "other.sqlite", "http://other")
    with pytest.raises(ValueError, match="target"):
        quarantine.replay(ident, {"power": 100}, other)
    other.db.close()
    quarantine.db.close()
    queue.db.close()


def test_failed_quarantine_write_never_commits_offset(tmp_path, monkeypatch):
    queue = Outbox(tmp_path / "queue.sqlite", "http://localhost")
    quarantine = Quarantine(tmp_path / "bad.sqlite", queue.db.execute("SELECT fingerprint FROM route").fetchone()[0])
    def broken(*args):
        raise OSError("disk full")
    monkeypatch.setattr(quarantine, "put", broken)
    commits = []
    with pytest.raises(OSError):
        persist_or_quarantine(queue, quarantine, "group", SimpleNamespace(commit=commits.append),
                              SimpleNamespace(value=b"\xff", topic="alerts", partition=0, offset=0), tuple, tuple)
    assert commits == []
    quarantine.db.close()
    queue.db.close()


def test_repair_intent_survives_partial_enqueue_failure(tmp_path, monkeypatch):
    queue = Outbox(tmp_path / "queue.sqlite", "http://localhost")
    quarantine = Quarantine(tmp_path / "bad.sqlite", queue.db.execute("SELECT fingerprint FROM route").fetchone()[0])
    ident = quarantine.put("group", SimpleNamespace(topic="a", partition=0, offset=0, value=b"bad"), "JSONDecodeError")
    original = queue.enqueue
    def crash_after_write(payload):
        original(payload)
        raise OSError("simulated crash after outbox commit")
    monkeypatch.setattr(queue, "enqueue", crash_after_write)
    with pytest.raises(OSError):
        quarantine.replay(ident, {"power": 100}, queue)
    assert quarantine.db.execute("SELECT state FROM rejected").fetchone()[0] == "replay_pending"
    with pytest.raises(ValueError, match="different repair"):
        quarantine.replay(ident, {"power": 200}, queue)
    monkeypatch.setattr(queue, "enqueue", original)
    quarantine.replay(ident, {"power": 100}, queue)
    assert queue.stats() == {"pending": 1}
    quarantine.db.close()
    queue.db.close()


def test_milp_known_optimum_and_minimum_run():
    equipment = [{"id": "test", "rate": 1, "kwh_per_unit": 1, "on_kw": 0,
                  "startup_cost": 0, "demand": 2, "ramp": 1, "min_up": 2, "max_starts": 1,
                  "baseline_units": [0]*20+[1,1,0,0], "baseline_on": [0]*20+[1,1,0,0]}]
    result = optimize_equipment([.1,.1]+[1]*22, [0]*24, 1, equipment)
    assert result["optimized_cost_yuan"] == pytest.approx(.2)
    assert sum(result["schedules"][0]["units"]) == pytest.approx(2)
    assert sum(result["schedules"][0]["starts"]) == 1


def test_milp_reconciles_process_materials_and_site_limit():
    inputs = demo_inputs()
    result = optimize_equipment(**inputs)
    power = np.array(inputs["base_kw"], float)
    for e, schedule in zip(inputs["equipment"], result["schedules"]):
        units, on = np.array(schedule["units"]), np.array(schedule["on"])
        assert sum(units) == pytest.approx(e["demand"])
        assert np.max(np.abs(np.diff(np.r_[0, units]))) <= e["ramp"] + 1e-6
        assert np.all(units <= e["rate"] * on + 1e-6)
        for t, start in enumerate(schedule["starts"]):
            if start:
                assert sum(on[t:t+e["min_up"]]) == e["min_up"]
        power += units * e["kwh_per_unit"] + on * e["on_kw"]
    assert max(power) <= inputs["site_limit_kw"] + 1e-6
    upstream, downstream = [np.array(s["units"]) for s in result["schedules"]]
    assert np.all(np.cumsum(downstream) <= np.r_[0, np.cumsum(upstream)[:-1]] + 1e-6)
    assert result["optimized_cost_yuan"] < result["baseline_cost_yuan"]
    invalid = copy.deepcopy(inputs)
    invalid["equipment"][0]["min_up"] = 20
    with pytest.raises(ValueError, match="violates"):
        optimize_equipment(**invalid)
