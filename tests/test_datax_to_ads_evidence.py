"""Check the committed real-DataX-to-ADS integration evidence."""

import json
from decimal import Decimal
from pathlib import Path


REPORT = Path(__file__).resolve().parents[1] / "output" / "datax_to_ads_20260927.json"


def test_real_datax_ods_reaches_all_downstream_layers():
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    assert report["success"] is True
    assert report["database"] == "energy_datax_probe_20260927"
    assert report["spark_version"] == "3.5.1"
    assert report["master"] == "local[2]"
    assert report["sql_mode"] == "incremental by ODS partition, including initial snapshot"
    assert list(report["phases"]) == ["snapshot", "corrected", "deleted"]
    for name, cost, deleted, e01_cost in (
        ("snapshot", "30.00", 0, "10.00"),
        ("corrected", "37.00", 0, "17.00"),
        ("deleted", "20.00", 1, "17.00"),
    ):
        phase = report["phases"][name]
        assert phase["success"] is True
        assert phase["business_date"] == "2024-03-15"
        assert phase["dwd_rows"] == phase["stage_rows"] == 2
        assert phase["e01_is_deleted"] == deleted
        assert Decimal(phase["e01_cost"]) == Decimal(e01_cost)
        for layer in ("dws_day_cost", "dws_month_cost", "ads_day_cost"):
            assert Decimal(phase[layer]) == Decimal(cost)
    assert report["phases"]["corrected"]["e01_source_updated_at"] == "2026-09-27 11:00:00"
    assert report["phases"]["deleted"]["e01_source_updated_at"] == "2026-09-28 09:00:00"
