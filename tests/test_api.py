"""Contract tests for the read-only FastAPI analytics facade."""
from fastapi.testclient import TestClient

from src.api import app


def write_csv(folder, filename, content):
    (folder / filename).write_text(content, encoding="utf-8-sig")


def seed_outputs(folder):
    write_csv(folder, "Q28_各车间日度能耗与产量.csv",
              "日期,车间编码,车间,综合能耗_tce,能源费用_元,碳排放_tCO2,产量\n"
              "2025-01-01,W01,熔炼车间,10.5,100,2.5,20\n"
              "2025-01-01,W02,装配车间,3.5,50,1.5,10\n"
              "2025-01-02,W01,熔炼车间,8,80,2,16\n")
    write_csv(folder, "Q03_各车间综合能耗排名.csv",
              "车间编码,车间,工序,综合能耗_tce,能源费用_元,碳排放_tCO2\n"
              "W01,熔炼车间,熔炼,18.5,180,4.5\n"
              "W02,装配车间,装配,3.5,50,1.5\n")
    write_csv(folder, "Q16_单耗异常日检测_2sigma.csv",
              "日期,车间,产量,单位产品能耗_kgce,车间均值,标准差,Z值\n"
              "2025-01-02,熔炼车间,16,0.5,0.3,0.1,2.0\n")
    write_csv(folder, "Q22_能耗突增预警_环比超25pct.csv",
              "年月,车间,当月能耗_tce,上月能耗_tce,环比_pct\n"
              "2025-01,装配车间,30,20,50\n")
    write_csv(folder, "Q01_能源消费总览.csv",
              "统计天数,车间数,能源品种数,综合能耗_tce,能源费用_元,碳排放_tCO2\n"
              "2,2,3,22,230,6\n")
    write_csv(folder, "Q02_剔除公用工程后的能耗总览.csv",
              "综合能耗_tce\n18\n")


def test_metrics_filters_dates_and_workshops(tmp_path, monkeypatch):
    seed_outputs(tmp_path)
    monkeypatch.setenv("ENERGY_OUTPUT_DIR", str(tmp_path))
    client = TestClient(app)

    response = client.get("/api/v1/metrics", params={
        "date_from": "2025-01-01", "date_to": "2025-01-01",
        "workshop_code": "W01",
    })

    assert response.status_code == 200
    payload = response.json()
    assert payload["workshop_days"] == 1
    assert payload["record_days"] == 1
    assert payload["tce"] == 10.5
    assert payload["cost_yuan"] == 100
    assert payload["data_as_of"] == "2025-01-01"
    assert "output_qty" not in payload  # 各车间产量单位不同，不能做全车间总和。


def test_anomaly_endpoint_returns_separate_daily_and_monthly_evidence(tmp_path, monkeypatch):
    seed_outputs(tmp_path)
    monkeypatch.setenv("ENERGY_OUTPUT_DIR", str(tmp_path))
    client = TestClient(app)

    response = client.get("/api/v1/anomalies")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert {row["alert_type"] for row in payload["items"]} == {
        "unit_energy_2sigma", "monthly_energy_mom"
    }
    daily = next(row for row in payload["items"] if row["period"] == "day")
    assert daily["workshop_code"] == "W01"
    assert daily["evidence_source"] == "Q16_单耗异常日检测_2sigma.csv"
    monthly = next(row for row in payload["items"] if row["period"] == "month")
    assert monthly["workshop_code"] == "W02"
    assert client.get("/api/v1/anomalies", params={"workshop_code": "W02"}).json()["total"] == 1


def test_report_summary_and_openapi_are_available(tmp_path, monkeypatch):
    seed_outputs(tmp_path)
    monkeypatch.setenv("ENERGY_OUTPUT_DIR", str(tmp_path))
    client = TestClient(app)

    response = client.get("/api/v1/report-summary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["totals"]["tce"] == 22
    assert payload["totals"]["tce_excluding_utility"] == 18
    assert payload["anomaly_count"] == 1
    assert payload["monthly_spike_count"] == 1
    spec = client.get("/openapi.json").json()
    assert "/api/v1/metrics" in spec["paths"]
    assert "/api/v1/anomalies" in spec["paths"]
    assert "/api/v1/report-summary" in spec["paths"]


def test_missing_analysis_file_fails_closed_with_service_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("ENERGY_OUTPUT_DIR", str(tmp_path))
    response = TestClient(app).get("/api/v1/metrics")

    assert response.status_code == 503
    assert "分析产物缺失" in response.json()["detail"]


def test_reversed_date_range_is_rejected(tmp_path, monkeypatch):
    seed_outputs(tmp_path)
    monkeypatch.setenv("ENERGY_OUTPUT_DIR", str(tmp_path))
    response = TestClient(app).get("/api/v1/metrics", params={
        "date_from": "2025-01-02", "date_to": "2025-01-01",
    })

    assert response.status_code == 422


def test_anomaly_pagination_is_stable(tmp_path, monkeypatch):
    seed_outputs(tmp_path)
    monkeypatch.setenv("ENERGY_OUTPUT_DIR", str(tmp_path))
    response = TestClient(app).get("/api/v1/anomalies", params={"limit": 1, "offset": 1})

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert payload["offset"] == 1
    assert len(payload["items"]) == 1


def test_health_checks_all_summary_artifacts(tmp_path, monkeypatch):
    seed_outputs(tmp_path)
    (tmp_path / "Q02_剔除公用工程后的能耗总览.csv").unlink()
    monkeypatch.setenv("ENERGY_OUTPUT_DIR", str(tmp_path))

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["missing_artifacts"] == ["Q02_剔除公用工程后的能耗总览.csv"]


def test_non_finite_metric_fails_closed(tmp_path, monkeypatch):
    seed_outputs(tmp_path)
    q28 = tmp_path / "Q28_各车间日度能耗与产量.csv"
    q28.write_text(q28.read_text(encoding="utf-8-sig").replace("10.5", "NaN"),
                   encoding="utf-8-sig")
    monkeypatch.setenv("ENERGY_OUTPUT_DIR", str(tmp_path))

    response = TestClient(app).get("/api/v1/metrics")

    assert response.status_code == 503
    assert "无效" in response.json()["detail"]
