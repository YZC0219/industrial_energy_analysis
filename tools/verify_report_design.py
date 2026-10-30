"""Capture the report redesign and verify real responsive interactions offline."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'output/design-review-20261030'
DEST.mkdir(parents=True, exist_ok=True)
os.environ['TEMP'] = os.environ['TMP'] = str(DEST)

from playwright.sync_api import sync_playwright


def run():
    errors, checks = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe',
            headless=True,
        )
        try:
            for width in (1440, 768, 390):
                page = browser.new_page(viewport={'width': width, 'height': 1000}, color_scheme='light')
                page.on('pageerror', lambda err: errors.append(str(err)))
                page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
                page.route('http**://**', lambda route: route.abort())
                page.goto((ROOT / 'output/report.html').as_uri())
                page.wait_for_selector('#f-ws input', state='attached')
                page.wait_for_timeout(250)
                assert page.locator('[data-fill="tce"]').first.inner_text().startswith('63,759.63')
                for theme in ('light', 'dark'):
                    page.evaluate('(theme) => document.documentElement.dataset.theme = theme', theme)
                    page.wait_for_timeout(150)
                    overflow = page.evaluate('document.documentElement.scrollWidth > innerWidth')
                    assert not overflow, (width, theme, 'report overflow')
                    page.screenshot(path=str(DEST / f'report-{width}-{theme}.png'))
                    checks.append({'viewport': width, 'theme': theme, 'view': 'report', 'overflow': overflow})
                page.locator('#theme-toggle').click()
                assert page.evaluate('document.documentElement.dataset.theme') == 'light'
                # Keyboard access to the skip link and theme switch.
                page.locator('#theme-toggle').focus()
                page.keyboard.press('Enter')
                assert page.evaluate('document.documentElement.dataset.theme') == 'dark'
                page.evaluate("document.documentElement.dataset.theme = 'light'")
                if width <= 640:
                    page.locator('.filter-toggle').click()
                    assert page.locator('.filter-toggle').get_attribute('aria-expanded') == 'true'
                page.locator('#f-range button[data-k="custom"]').click()
                page.locator('#f-from').fill('2025-01-01')
                page.locator('#f-to').fill('2025-01-31')
                page.locator('#f-to').dispatch_event('change')
                assert not page.locator('[data-fill="tce"]').first.inner_text().startswith('63,759.63')
                page.locator('[data-toggle="mix"]').click()
                assert page.locator('[data-table="mix"]').is_visible()
                page.locator('#f-reset').click()
                assert page.locator('[data-fill="tce"]').first.inner_text().startswith('63,759.63')
                page.locator('[data-view="dash"]').click()
                page.wait_for_timeout(200)
                assert page.locator('#dashboard').is_visible()
                assert page.locator('#dash-kpis').inner_text().find('63,759.6') >= 0
                assert not page.evaluate('document.documentElement.scrollWidth > innerWidth'), (width, 'dashboard overflow')
                page.evaluate('scrollTo(0,0)')
                page.screenshot(path=str(DEST / f'dashboard-{width}.png'))
                checks.append({'viewport': width, 'view': 'dashboard', 'filters_and_reset': True})
                page.locator('[data-view="report"]').click()
                page.emulate_media(reduced_motion='reduce')
                assert page.locator('.trace-orbit').evaluate('(el) => getComputedStyle(el).animationName') == 'none'
                for target in ('energy-overview', 'energy-structure', 'energy-trend', 'energy-efficiency', 'energy-anomaly'):
                    assert page.locator('#' + target).count() == 1
                page.close()
        finally:
            browser.close()
    result = {'checks': checks, 'browser_errors': errors, 'passed': not errors}
    (DEST / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    assert not errors, errors
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    run()
