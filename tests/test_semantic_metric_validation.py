"""Regression coverage for scalar metrics on compatible daily CSV subsets."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from governance.catalog import catalog, metric
from src.api import app


def write_daily(directory, day="2026-10-01", include_process=True):
    entry = next(q for q in catalog()["queries"] if q["id"] == "Q28")
    header = "日期,车间编码,综合能耗_tce,能源费用_元,碳排放_tCO2"
    row = f"{day},W01,1,10,2"
    if include_process:
        header += ",工序"
        row += ",熔炼"
    (directory / entry["artifact"]).write_text(header + "\n" + row + "\n", encoding="utf-8")


@pytest.mark.parametrize("day", ["20261001", "2026-W40-4", "2026-W40"])
@pytest.mark.parametrize("filtered", [False, True])
def test_scalar_metric_rejects_noncanonical_dates(tmp_path, monkeypatch, day, filtered):
    write_daily(tmp_path, day)
    monkeypatch.setenv("ENERGY_OUTPUT_DIR", str(tmp_path))
    params = {"date_from": "2026-10-02"} if filtered else {}
    response = TestClient(app).get("/api/v1/semantic/metrics/total_energy_tce", params=params)
    assert response.status_code == 503


@pytest.mark.parametrize("metric_id,expected", [
    ("total_energy_tce", 1), ("total_cost_yuan", 10), ("total_carbon_tco2", 2),
])
def test_whole_factory_metric_does_not_require_unused_process_column(
    tmp_path, monkeypatch, metric_id, expected,
):
    write_daily(tmp_path, include_process=False)
    monkeypatch.setenv("ENERGY_OUTPUT_DIR", str(tmp_path))
    response = TestClient(app).get(f"/api/v1/semantic/metrics/{metric_id}", params={
        "date_from": "2026-10-01", "date_to": "2026-10-01", "workshop_code": "W01",
    })
    assert response.status_code == 200
    assert response.json()["value"] == expected
    assert response.json()["row_count"] == 1


@pytest.mark.parametrize("metric_id", ["net_energy_tce", "net_cost_yuan", "energy_intensity_kgce"])
def test_excluding_utilities_still_requires_process_column(tmp_path, metric_id):
    write_daily(tmp_path, include_process=False)
    with pytest.raises(KeyError, match="工序"):
        metric(metric_id, tmp_path, date_from=date(2026, 10, 1))
