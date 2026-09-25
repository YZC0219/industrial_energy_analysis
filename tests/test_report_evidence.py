"""Keep the public report from combining unrelated warehouse and SQL snapshots."""
import json

from src import make_report


def test_recent_evidence_requires_same_q01_warehouse_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(make_report, "OUT_DIR", str(tmp_path))
    (tmp_path / make_report.Q["totals"]).write_text(
        "统计天数,综合能耗_tce,能源费用_元,碳排放_tCO2\n1,3.00,30.00,1.20\n",
        encoding="utf-8-sig",
    )
    (tmp_path / "pandas_daily.csv").write_text(
        "record_date,workshop_code,tce,cost_yuan,co2_t\n"
        "2025-01-01,W01,1.00,10.00,0.40\n"
        "2025-01-01,W02,2.00,20.00,0.80\n",
        encoding="utf-8",
    )
    samples = {
        "ml_model_metrics.json": {"models": [{"model": "seasonal_naive_7d",
                                                "fold_metrics": []}]},
        "engine_comparison.json": {"rows": {"pandas": 2}},
        "lakehouse_validation.json": {"full_load": {
            "workshop_day_rows": 2, "factory_day_rows": 1,
        }},
        "attribution_eval.json": {"passed": 0, "total": 0, "cases": []},
    }
    for filename, payload in samples.items():
        (tmp_path / filename).write_text(json.dumps(payload), encoding="utf-8")

    assert make_report._recent_evidence()["available"] is True
    (tmp_path / make_report.Q["totals"]).write_text(
        "统计天数,综合能耗_tce,能源费用_元,碳排放_tCO2\n1,3.00,31.00,1.20\n",
        encoding="utf-8-sig",
    )
    assert make_report._recent_evidence()["available"] is False


def test_recent_evidence_fails_closed_without_comparison_csv(tmp_path, monkeypatch):
    monkeypatch.setattr(make_report, "OUT_DIR", str(tmp_path))
    assert make_report._warehouse_evidence_matches_report(
        {"rows": {"pandas": 1}},
        {"full_load": {"workshop_day_rows": 1, "factory_day_rows": 1}},
    ) is False
