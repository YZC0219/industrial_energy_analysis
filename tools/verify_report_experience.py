"""Verify the grouped report, offline charts and interactive 3D overview.

Uses an already installed Chrome; it never downloads browsers or dependencies.
By default checks output/report.html and the mobile layout in docs/index.html.
Use --url https://.../ to repeat the same checks after publication.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import traceback
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
PANELS = ('overview', 'structure', 'efficiency', 'anomaly', 'evidence')
CHROME = Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe')


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', help='Published report URL; otherwise inspect local files')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'output/redesign-20261101')
    parser.add_argument('--chrome', type=Path, default=CHROME)
    return parser.parse_args()


def settle(page):
    page.evaluate('() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))')
    # Let 180ms theme/selection transitions finish before visual captures.
    page.wait_for_timeout(240)


def no_overflow(page, context):
    size = page.evaluate('''() => ({viewport: innerWidth,
        content: document.documentElement.scrollWidth})''')
    assert size['content'] <= size['viewport'] + 1, (context, size)


def no_toolbar_overlap(page, width):
    if width > 640:
        return
    page.evaluate('window.scrollTo({top: 0, behavior: "instant"})')
    settle(page)
    heading = page.locator('.workspace-head h1')
    assert heading.is_visible(), (width, 'Panel title is hidden')
    title_box = heading.bounding_box()
    toolbar = page.locator('.filterbar').bounding_box()
    assert title_box and toolbar
    assert toolbar['y'] + toolbar['height'] <= title_box['y'] + 1, (
        width, 'Filter toolbar overlaps panel title', toolbar, title_box)
    for button in page.locator('button[data-report-panel]:visible').all():
        box = button.bounding_box()
        assert box and box['x'] >= -1 and box['x'] + box['width'] <= width + 1, (
            width, 'Theme navigation falls outside viewport', button.inner_text(), box)


def switch_panel(page, name):
    page.locator(f'button[data-report-panel="{name}"]:visible').first.click()
    page.wait_for_function('(panel) => document.documentElement.dataset.reportPanel === panel', arg=name)
    settle(page)


def raw_data(page):
    return page.evaluate('''() => ({tce: REPORT.totals.tce,
        workshop_days: REPORT.ws_daily.length, energy_rows: REPORT.energy_daily.length})''')


def visible_groups(page):
    return page.locator('[data-report-group]').evaluate_all('''nodes => nodes.filter(node =>
        node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden'
        ).map(node => node.dataset.reportGroup)''')


def check_scene(page, checks, width):
    canvas = page.locator('#energy-scene')
    canvas.scroll_into_view_if_needed()
    page.wait_for_function('''() => {
      const canvas = document.querySelector('#energy-scene');
      if (!canvas || canvas.width < 20 || canvas.height < 20) return false;
      const context = canvas.getContext('2d');
      if (!context) return false;
      const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
      let drawn = 0;
      const colors = new Set();
      for (let i = 0; i < pixels.length; i += 64) {
        if (pixels[i + 3]) { drawn++; colors.add(pixels.slice(i, i + 4).join(',')); }
      }
      return drawn > 20 && colors.size > 4;
    }''')
    motion = page.locator('[data-scene-action="motion"]')
    original = motion.get_attribute('aria-pressed')
    assert original in ('true', 'false'), ('scene motion state', original)
    motion.click()
    assert motion.get_attribute('aria-pressed') != original
    page.locator('[data-scene-action="reset"]').click()
    settle(page)
    before_drag = canvas.evaluate('(canvas) => canvas.toDataURL()')
    box = canvas.bounding_box()
    assert box
    origin_x = box['x'] + box['width'] * .45
    origin_y = box['y'] + box['height'] * .6
    page.mouse.move(origin_x, origin_y)
    page.mouse.down()
    page.mouse.move(origin_x + min(60, box['width'] * .18), origin_y, steps=5)
    page.mouse.up()
    settle(page)
    assert canvas.evaluate('(canvas) => canvas.toDataURL()') != before_drag, 'Dragging did not rotate the scene'
    page.locator('[data-scene-action="reset"]').click()
    settle(page)
    assert canvas.evaluate('(canvas) => canvas.toDataURL()') == before_drag, 'Reset did not restore the initial view'
    motion.click()
    assert motion.get_attribute('aria-pressed') == original
    page.emulate_media(reduced_motion='reduce')
    page.wait_for_timeout(200)
    first = canvas.evaluate('(canvas) => canvas.toDataURL()')
    page.wait_for_timeout(250)
    second = canvas.evaluate('(canvas) => canvas.toDataURL()')
    assert first == second, 'Scene still animates with reduced motion enabled'
    page.emulate_media(reduced_motion='no-preference')
    checks.append({'width': width, 'scene_drawn': True, 'motion_controls': True,
                   'drag_changes_view': True, 'reset_restores_view': True,
                   'reduced_motion_stops_animation': True})


def configure_page(browser, url, width, errors, blocked):
    page = browser.new_page(viewport={'width': width, 'height': 1000}, color_scheme='light')
    page.on('pageerror', lambda err: errors.append({'width': width, 'message': str(err)}))
    page.on('console', lambda msg: errors.append({'width': width, 'message': msg.text})
            if msg.type == 'error' else None)
    parsed = urlparse(url)
    origin = (parsed.scheme, parsed.netloc)

    def route_request(route):
        request = urlparse(route.request.url)
        if request.scheme in ('http', 'https') and (request.scheme, request.netloc) != origin:
            blocked.add(route.request.url)
            route.abort()
        else:
            route.continue_()

    page.route('**/*', route_request)
    page.goto(url, wait_until='load')
    page.wait_for_selector('#f-ws input', state='attached')
    page.wait_for_function("document.documentElement.dataset.reportPanel === 'overview'")
    settle(page)
    no_toolbar_overlap(page, width)
    return page


def verify_main(browser, url, width, dest, errors, blocked, checks):
    page = configure_page(browser, url, width, errors, blocked)
    try:
        baseline = raw_data(page)
        assert baseline['tce'] == 63759.63 and baseline['workshop_days'] == 5848, baseline
        assert page.locator('.overview-charts .chart svg:visible').count() >= 2
        no_overflow(page, (width, 'overview'))
        assert page.locator('[data-fill="tce"]').first.inner_text().startswith('63,759.63')
        page.screenshot(path=str(dest / f'experience-overview-{width}-light.png'))
        check_scene(page, checks, width)

        for panel in PANELS:
            switch_panel(page, panel)
            groups = visible_groups(page)
            assert groups and set(groups) == {panel}, (panel, groups)
            charts = page.locator('[data-report-group] .chart svg:visible').count()
            figures = page.locator('[data-report-group] figure:visible').count()
            assert charts or figures, (panel, 'No readable chart or evidence figure')
            no_overflow(page, (width, panel))
            page.evaluate('window.scrollTo({top: 0, behavior: "instant"})')
            settle(page)
            page.screenshot(path=str(dest / f'experience-{panel}-{width}.png'))
            checks.append({'width': width, 'panel': panel, 'visible_charts': charts,
                           'visible_figures': figures, 'overflow': False})

        switch_panel(page, 'overview')
        assert page.locator('.filter-toggle').get_attribute('aria-expanded') == 'false'
        page.locator('.filter-toggle').click()
        assert page.locator('.filter-toggle').get_attribute('aria-expanded') == 'true'
        no_overflow(page, (width, 'expanded filters'))
        page.locator('#f-range button[data-k="custom"]').click()
        page.locator('#f-from').fill('2025-01-01')
        page.locator('#f-to').fill('2025-01-31')
        page.locator('#f-to').dispatch_event('change')
        settle(page)
        assert not page.locator('[data-fill="tce"]').first.inner_text().startswith('63,759.63')
        page.locator('#f-reset').click()
        assert page.locator('[data-fill="tce"]').first.inner_text().startswith('63,759.63')
        if page.locator('.filter-toggle').get_attribute('aria-expanded') == 'true':
            page.locator('.filter-toggle').click()

        switch_panel(page, 'structure')
        page.locator('[data-toggle="mix"]').click()
        assert page.locator('[data-table="mix"]').is_visible()
        assert page.locator('[data-table="mix"] tbody tr').count() == 6
        page.locator('[data-toggle="mix"]').click()
        assert not page.locator('[data-table="mix"]').is_visible()
        switch_panel(page, 'overview')

        page.locator('[data-view="dash"]').click()
        settle(page)
        assert page.locator('#dashboard').is_visible()
        assert '63,759.6' in page.locator('#dash-kpis').inner_text()
        no_overflow(page, (width, 'dashboard'))
        page.evaluate('window.scrollTo({top: 0, behavior: "instant"})')
        settle(page)
        page.screenshot(path=str(dest / f'experience-dashboard-{width}.png'))
        page.locator('[data-view="report"]').click()
        settle(page)

        page.locator('#theme-toggle').focus()
        page.keyboard.press('Enter')
        assert page.evaluate('document.documentElement.dataset.theme') == 'dark'
        settle(page)
        no_overflow(page, (width, 'dark overview'))
        page.evaluate('window.scrollTo({top: 0, behavior: "instant"})')
        settle(page)
        page.screenshot(path=str(dest / f'experience-overview-{width}-dark.png'))
        page.locator('#theme-toggle').click()
        assert page.evaluate('document.documentElement.dataset.theme') == 'light'
        assert raw_data(page) == baseline, 'Interaction changed embedded source data'
        checks.append({'width': width, 'filters_reset': True, 'table_view': True,
                       'dashboard': True, 'theme_keyboard': True, 'source_data_unchanged': True})
    finally:
        page.close()


def verify_published_layout(browser, local_url, dest, errors, blocked, checks):
    """Catch publication-only CSS that can override the standalone report."""
    for width in (390, 768):
        page = configure_page(browser, local_url, width, errors, blocked)
        try:
            no_overflow(page, (width, 'published document'))
            assert page.locator('.overview-charts .chart svg:visible').count() >= 2
            filters = page.locator('.filter-toggle')
            assert filters.get_attribute('aria-expanded') == 'false'
            filters.click()
            no_overflow(page, (width, 'published expanded filters'))
            filters.click()
            page.screenshot(path=str(dest / f'experience-published-{width}.png'))
            checks.append({'width': width, 'published_layout': True, 'overflow': False})
        finally:
            page.close()


def main():
    args = arguments()
    dest = args.output_dir.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    task_temp = dest / 'browser-temp'
    task_temp.mkdir(exist_ok=True)
    os.environ['TEMP'] = os.environ['TMP'] = str(task_temp)
    from playwright.sync_api import sync_playwright

    result = {'target': args.url or str(ROOT / 'output/report.html'),
              'checks': [], 'browser_errors': [], 'blocked_external_requests': [], 'passed': False}
    blocked = set()
    try:
        if not args.chrome.is_file():
            raise FileNotFoundError(f'Installed Chrome not found at {args.chrome}; pass --chrome')
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=str(args.chrome), headless=True)
            try:
                target = args.url or (ROOT / 'output/report.html').as_uri()
                for width in (1440, 768, 390):
                    verify_main(browser, target, width, dest, result['browser_errors'], blocked,
                                result['checks'])
                if not args.url:
                    verify_published_layout(browser, (ROOT / 'docs/index.html').as_uri(), dest,
                                            result['browser_errors'], blocked, result['checks'])
            finally:
                browser.close()
        assert not result['browser_errors'], result['browser_errors']
        result['passed'] = True
    except Exception:
        result['failure'] = traceback.format_exc()
        raise
    finally:
        result['blocked_external_requests'] = sorted(blocked)
        suffix = '-online' if args.url else ''
        (dest / f'experience-verification{suffix}.json').write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
