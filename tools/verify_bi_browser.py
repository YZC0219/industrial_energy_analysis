"""Capture the 2026-09-28 Metabase acceptance case as a read-only viewer.

Uses the local demo credentials from .env; never stores a session token in the
report or screenshot metadata. Pair this with a fresh verify_bi_runtime.py
report, which checks the database grants and API-side filter semantics. The
filter and expected numbers are intentionally fixed for this dated archive.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from playwright.sync_api import sync_playwright

from tools.verify_bi_runtime import api, credentials

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--chrome-path", type=Path,
                        default=Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"))
    parser.add_argument("--runtime-report", type=Path,
                        default=ROOT / "output" / "metabase_runtime_20260928.json")
    parser.add_argument("--screenshot", type=Path,
                        default=ROOT / "output" / "metabase_dashboard_filtered_20260928.png")
    parser.add_argument("--drill-screenshot", type=Path,
                        default=ROOT / "output" / "metabase_drillthrough_20260928.png")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "output" / "metabase_browser_20260928.json")
    args = parser.parse_args()
    if not args.chrome_path.is_file():
        raise FileNotFoundError(f"Chrome not found at {args.chrome_path}; use --chrome-path")

    runtime = json.loads(args.runtime_report.read_text(encoding="utf-8"))
    if runtime.get("result") != "pass":
        raise AssertionError("Metabase API runtime report must pass first")
    checked_at = datetime.fromisoformat(runtime["checked_at_utc"])
    age = datetime.now(timezone.utc) - checked_at
    if age < -timedelta(minutes=5) or age > timedelta(hours=1):
        raise AssertionError("Metabase API report is stale; rerun verify_bi_runtime")
    if runtime["sample_filter"] != {
        "workshop_code": "W04", "date_from": "2025-01-01",
        "date_to": "2025-01-31", "rows": 93, "cost_yuan": "375635.34",
    }:
        raise AssertionError("API report does not match this dated browser archive")
    cfg = credentials(args.env_file)
    status, session = api(args.base_url, "session", method="POST", payload={
        "username": cfg["METABASE_VIEWER_EMAIL"],
        "password": cfg["METABASE_VIEWER_PASSWORD"],
    })
    if status != 200:
        raise AssertionError(f"viewer login: HTTP {status}")
    expected = Decimal(runtime["sample_filter"]["cost_yuan"])
    dashboard_id = int(runtime["dashboard_id"])
    url = (f"{args.base_url.rstrip('/')}/dashboard/{dashboard_id}"
           "?record_date=2025-01-01~2025-01-31&workshop_code=W04")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(args.chrome_path),
            headless=True,
        )
        try:
            context = browser.new_context(viewport={"width": 1440, "height": 900},
                                          device_scale_factor=1)
            context.add_cookies([{"name": "metabase.SESSION", "value": session["id"],
                                 "url": args.base_url}])
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.get_by_text("车间能源费用排名", exact=True).wait_for(timeout=30000)
            page.get_by_text("每日能源费用趋势", exact=True).wait_for(timeout=30000)
            page.wait_for_function(
                "document.body.innerText.includes('400,000.00')", timeout=30000)
            body = page.locator("body").inner_text()
            if "W04" not in body or "2025年1月1日 - 2025年1月31日" not in body:
                raise AssertionError("workshop filter W04 not visible in dashboard")
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(args.screenshot), full_page=True)
            # Fixed viewport: the sole W04 bar occupies the center of the first card.
            page.mouse.click(520, 350)
            page.get_by_text("查看此记录", exact=True).click(timeout=10000)
            expected_rows = int(runtime["sample_filter"]["rows"])
            page.get_by_text(f"正在显示 {expected_rows} 行", exact=False).wait_for(timeout=30000)
            args.drill_screenshot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(args.drill_screenshot), full_page=False)
            report = {
                "checked_at_utc": datetime.now(timezone.utc).isoformat(),
                "dashboard_id": dashboard_id,
                "filter": runtime["sample_filter"],
                "api_verified_cost_yuan": str(expected),
                "browser_checked": ["date filter", "workshop filter", "ranking chart",
                                    "daily trend chart", "drill-through row count"],
                "screenshot": args.screenshot.name,
                "drill_screenshot": args.drill_screenshot.name,
                "drillthrough_rows": expected_rows,
                "result": "pass",
            }
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")
            print(f"Metabase browser checks passed; evidence: {args.output}")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
