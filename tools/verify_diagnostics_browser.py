"""Run against an isolated workbench deployment: writes a test feedback record."""
import argparse
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "tmp/diagnostics_browser")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    os.environ["TEMP"] = os.environ["TMP"] = str(args.output_dir.resolve())
    errors = []
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(args.output_dir / "chrome-profile"),
            executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            headless=True, viewport={"width": 1440, "height": 1000})
        try:
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(args.base_url + "/diagnostics")
            page.locator(".incident").first.wait_for()
            listing = context.request.get(args.base_url + "/api/v1/diagnostics").json()
            sample = listing["items"][0]
            page.locator("#workshop").select_option(sample["workshop_code"])
            with page.expect_response(lambda r: "/api/v1/diagnostics?" in r.url):
                page.get_by_role("button", name="筛选", exact=True).click()
            page.locator(".incident").first.click()
            page.get_by_role("heading", name="异常原始证据").wait_for()
            assert sample["workshop"] in page.locator("#detail h2").inner_text()
            page.get_by_placeholder("例如：本次异常有哪些证据？").fill("异常的证据是什么")
            page.get_by_role("button", name="查看证据回答").click()
            page.get_by_text("证据检索与规则守卫（非在线模型回答）", exact=True).wait_for()
            assert page.locator(".answer").count() > 0
            page.get_by_placeholder("处理人", exact=True).fill("浏览器验收")
            page.get_by_placeholder("确认原因或判断依据", exact=False).fill("测试反馈，仅用于隔离验收")
            page.get_by_placeholder("处理措施及结果").fill("已核对页面与接口")
            page.locator("#detail select").select_option("resolved")
            page.get_by_role("button", name="保存处理反馈").click()
            page.locator(".history").first.wait_for()
            page.reload()
            page.locator(".incident").first.wait_for()
            page.locator("#status").select_option("resolved")
            with page.expect_response(lambda r: "/api/v1/diagnostics?" in r.url):
                page.get_by_role("button", name="筛选", exact=True).click()
            page.locator(".incident").first.click()
            page.locator(".history").first.wait_for()
            assert "浏览器验收" in page.locator(".history").first.inner_text()
            page.screenshot(path=str(args.output_dir / "desktop.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(args.output_dir / "mobile.png"), full_page=True)
            assert not errors, errors
            (args.output_dir / "verification.json").write_text(json.dumps({
                "result": "pass", "checks": ["filter", "evidence", "scoped question", "feedback persistence", "mobile layout"],
                "page_errors": errors}, ensure_ascii=False, indent=2), encoding="utf-8")
            print("Workbench browser verification passed")
        finally:
            context.close()


if __name__ == "__main__":
    main()
