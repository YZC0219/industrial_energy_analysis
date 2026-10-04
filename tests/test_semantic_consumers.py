from pathlib import Path
import pytest
from governance.consumers import daily_totals, read_artifact
from governance.catalog import catalog


def test_legacy_subset_uses_semantic_fields_and_rejects_unregistered_columns(tmp_path):
    entry = next(q for q in catalog()["queries"] if q["id"] == "Q28")
    path = tmp_path / entry["artifact"]
    path.write_text("日期,车间编码,综合能耗_tce,能源费用_元,碳排放_tCO2\n2025-01-01,W01,1,10,2\n", encoding="utf-8")
    rows = read_artifact(entry["artifact"], tmp_path)
    assert daily_totals(rows)["total_cost_yuan"] == 10
    path.write_text("日期,unknown\n2025-01-01,1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown"):
        read_artifact(entry["artifact"], tmp_path)
    with pytest.raises(KeyError):
        read_artifact("../../private.csv", tmp_path)


def test_missing_scalar_field_or_invalid_date_fails_in_shared_aggregation():
    row = {"日期": "2025-01-01", "车间编码": "W01", "综合能耗_tce": "1", "能源费用_元": "10", "碳排放_tCO2": "2"}
    with pytest.raises(ValueError):
        daily_totals([{**row, "日期": "2025-1-1"}])
    with pytest.raises(ValueError):
        daily_totals([{**row, "能源费用_元": "-1"}])
    with pytest.raises(KeyError):
        daily_totals([{key: value for key, value in row.items() if key != "综合能耗_tce"}])


def test_empty_invalid_header_is_rejected_before_reading_rows(tmp_path):
    entry = next(q for q in catalog()["queries"] if q["id"] == "Q28")
    (tmp_path / entry["artifact"]).write_text("unknown\n", encoding="utf-8")
    with pytest.raises(ValueError, match="column"):
        read_artifact(entry["artifact"], tmp_path)


def test_registered_header_whitespace_remains_compatible(tmp_path):
    entry = next(q for q in catalog()["queries"] if q["id"] == "Q28")
    (tmp_path / entry["artifact"]).write_text(" 日期 , 车间编码 \n 2025-01-01 , W01 \n", encoding="utf-8")
    assert read_artifact(entry["artifact"], tmp_path) == [{"日期": "2025-01-01", "车间编码": "W01"}]
