"""Read-only desktop/mobile and unavailable-state checks for the pipeline page."""
import argparse
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8010')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'tmp/pipeline-browser')
    args = parser.parse_args()
    folder = args.output_dir.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    os.environ['TEMP'] = os.environ['TMP'] = str(folder)
    errors = []
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(folder / 'chrome-profile'), headless=True,
            executable_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe',
            viewport={'width': 1440, 'height': 1080})
        try:
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(args.base_url + '/pipeline')
            page.locator('#summary .stat').first.wait_for()
            assert page.locator('#summary .stat').count() == 4
            assert page.locator('#flow .step').count() == 3
            assert page.locator('#deliveries .delivery').count() == 3
            assert page.locator('#components .component').count() == 5
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            with page.expect_response(lambda r: r.url.endswith('/api/v1/pipeline')):
                page.get_by_role('button', name='刷新记录').click()
            page.locator('#refresh:not([disabled])').wait_for()
            page.screenshot(path=str(folder / 'desktop.png'), full_page=True)
            page.screenshot(path=str(folder / 'overview.png'))
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(folder / 'mobile.png'), full_page=True)

            data = context.request.get(args.base_url + '/api/v1/pipeline').json()
            unavailable = {**data, 'current': None,
                           'snapshot': {'freshness': 'fresh', 'available': False, 'checked_at': None,
                                        'source': '<img src=x onerror=alert(1)>'},
                           'notices': ['本次无法读取调度服务；当前状态未知。']}
            page.route('**/api/v1/pipeline', lambda route: route.fulfill(json=unavailable))
            page.get_by_role('button', name='刷新记录').click()
            page.get_by_text('本次无法读取调度服务；当前状态未知。', exact=True).wait_for()
            assert '未知' in page.locator('#summary .stat').first.inner_text()
            assert '采集不可用' in page.locator('#summary .stat').first.inner_text()
            assert page.locator('#sources img').count() == 0
            assert '对应关系尚未确认' in page.locator('#deliveries').inner_text()
            page.screenshot(path=str(folder / 'unavailable-mobile.png'), full_page=True)
            page.unroute('**/api/v1/pipeline')
            page.route('**/api/v1/pipeline', lambda route: route.fulfill(status=503, body='unavailable'))
            page.get_by_role('button', name='刷新记录').click()
            page.locator('#error:not([hidden])').wait_for()
            assert '上一次读取' in page.locator('#error').inner_text()
            assert not errors, errors
            (folder / 'verification.json').write_text(json.dumps({
                'result': 'pass', 'checks': ['desktop', 'mobile overflow', 'refresh',
                'offline state', 'escaped evidence names', 'report independence', 'refresh failure'],
                'page_errors': errors}, ensure_ascii=False, indent=2), encoding='utf-8')
            print('Pipeline dashboard browser verification passed')
        finally:
            context.close()


if __name__ == '__main__':
    main()
