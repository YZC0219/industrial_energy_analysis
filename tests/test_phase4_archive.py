"""Offline integrity checks for the dated phase-four runtime archive."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output"


def _evidence(name: str) -> dict:
    return json.loads((OUTPUT / name).read_text(encoding="utf-8"))


def test_phase4_api_and_browser_archive_agree() -> None:
    runtime = _evidence("metabase_runtime_20260928.json")
    browser = _evidence("metabase_browser_20260928.json")

    assert runtime["result"] == browser["result"] == "pass"
    assert runtime["view_rows"] == 19006
    assert runtime["visible_tables"] == ["v_energy_enriched"]
    assert runtime["sample_filter"] == browser["filter"] == {
        "workshop_code": "W04", "date_from": "2025-01-01",
        "date_to": "2025-01-31", "rows": 93, "cost_yuan": "375635.34",
    }
    assert browser["drillthrough_rows"] == runtime["sample_filter"]["rows"]
    assert Decimal(runtime["metabase_cost_yuan"]) == Decimal(
        runtime["sample_filter"]["cost_yuan"]
    ) == Decimal(browser["api_verified_cost_yuan"])
    assert runtime["dashboard_cards_with_both_filters"] == 2
    assert runtime["native_sql_http_status"] == 403
    assert runtime["dashboard_edit_http_status"] == 403
    assert runtime["fact_table_read_denied"] is True
    assert runtime["mysql_write_denied"] is True

    api_time = datetime.fromisoformat(runtime["checked_at_utc"])
    browser_time = datetime.fromisoformat(browser["checked_at_utc"])
    assert timedelta(0) <= browser_time - api_time <= timedelta(hours=1)


def test_phase4_archive_screenshots_are_real_pngs() -> None:
    browser = _evidence("metabase_browser_20260928.json")
    for key in ("screenshot", "drill_screenshot"):
        path = OUTPUT / browser[key]
        assert path.is_file() and path.stat().st_size > 10000
        assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
