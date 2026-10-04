"""Verify offline browser metrics against all seven canonical scalar definitions."""
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
from uuid import uuid4
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from src import make_report
from governance.browser import contract
from governance.catalog import metric, UnsupportedFilter, query


def run():
    evidence = json.loads((ROOT / 'output/extensions/semantic_runtime.json').read_text(encoding='utf-8'))
    source = ROOT / evidence['evidence_directory']
    make_report.OUT_DIR = str(source)
    data = make_report.build()
    folder = ROOT / 'output/extensions' / ('browser_semantic_' + uuid4().hex[:12])
    folder.mkdir(parents=True)
    html_path = folder / 'report.html'
    html_path.write_text(make_report.render(data), encoding='utf-8')
    checked = []
    errors = []
    external_requests = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe', headless=True)
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            def offline_only(route):
                if route.request.url.startswith(('http://', 'https://')):
                    external_requests.append(route.request.url)
                    route.abort()
                else:
                    route.continue_()
            page.route('**/*', offline_only)
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
            page.goto(html_path.as_uri(), wait_until='load')
            page.wait_for_selector('#f-ws input')
            scopes = [{}] + [{'workshop_code': f'W{i:02d}', 'date_from': '2025-01-01', 'date_to': '2025-01-31'} for i in range(1, 9)]
            scopes += [{'date_from': '2030-01-01', 'date_to': '2030-01-02'}]
            for filters in scopes:
                result = page.evaluate('''filters => {
                  const rows = REPORT.ws_daily.filter(r => (!filters.workshop_code || r.code === filters.workshop_code)
                      && (!filters.date_from || r.d >= filters.date_from) && (!filters.date_to || r.d <= filters.date_to));
                  return EnergySemantic.evaluate(ENERGY_SEMANTIC_CONTRACT, rows, REPORT.ws_meta);
                }''', filters)
                parsed = {k: date.fromisoformat(v) if k.startswith('date_') else v for k,v in filters.items()}
                for definition in contract()['metrics']:
                    ident = definition['id']
                    try:
                        expected = metric(ident, source, **parsed)['value']
                    except UnsupportedFilter:
                        assert result[ident]['status'] == 'unsupported_scope'
                    else:
                        observed = result[ident]['value']
                        assert (expected is None and observed is None) or (expected is not None and observed is not None and math.isclose(observed, expected, rel_tol=1e-12, abs_tol=1e-8)), (ident, filters, expected, observed)
                checked.append({'filters': filters, 'metrics': len(result), 'passed': True})
            # Exercise actual controls and render paths, not just the interpreter.
            page.locator('#f-range button[data-k="custom"]').click()
            page.locator('#f-from').fill('2025-01-01')
            page.locator('#f-to').fill('2025-01-31')
            page.locator('#f-to').dispatch_event('change')
            for table, cost_index, pct_index in [('mix', 5, 6), ('workshops', 4, 5)]:
                page.locator(f'[data-toggle="{table}"]').click()
                rows = page.locator(f'[data-table="{table}"] tbody tr').evaluate_all('(rows) => rows.map(r => Array.from(r.cells).map(c => c.textContent))')
                costs = [float(r[cost_index].replace(',', '')) for r in rows]
                total = math.fsum(costs)
                for r, cost in zip(rows, costs):
                    assert abs(float(r[pct_index].rstrip('%')) - cost / total * 100) < .0051
            page.locator('[data-view="dash"]').click()
            page.evaluate('''() => {
              document.querySelectorAll('#f-ws input').forEach(b => b.checked = b.dataset.ws === 'W04');
              document.querySelector('#f-ws input').dispatchEvent(new Event('change', {bubbles:true}));
            }''')
            expected_intensity = metric('energy_intensity_kgce', source, date_from=date(2025,1,1), date_to=date(2025,1,31), workshop_code='W04')['value']
            tiles = page.locator('#dash-kpis').inner_text()
            assert f'{expected_intensity:.1f}' in tiles and 'kgce/产量单位' in tiles and '生产日' not in tiles
            assert page.locator('[data-fill="tce_ex-pct"]').text_content() == '100.0%'
            page.screenshot(path=str(folder / 'dashboard.png'), full_page=False)
            page.locator('#f-reset').click()
            assert '请选择' in page.locator('#dash-kpis').inner_text() or '请只选一个车间' in page.locator('#dash-kpis').inner_text()
            # Day-type selection supports offline scopes even though the API has no day filter.
            selected = [r for r in query('Q28', source)['rows'] if r['日型'] == '周末']
            day_values = page.evaluate('''() => EnergySemantic.evaluate(ENERGY_SEMANTIC_CONTRACT,
                REPORT.ws_daily.filter(r => r.day === '周末'), REPORT.ws_meta)''')
            for ident, column in [('total_energy_tce','综合能耗_tce'),('total_cost_yuan','能源费用_元'),('total_carbon_tco2','碳排放_tCO2')]:
                assert math.isclose(day_values[ident]['value'], math.fsum(float(r[column]) for r in selected), rel_tol=1e-12)
            edge = page.evaluate('''() => {
                const one = {d:'2025-01-01',code:'W01',prod:false,tce:2,cost:10,co2:1,qty:4};
                const metrics = EnergySemantic.evaluate(ENERGY_SEMANTIC_CONTRACT,[one],REPORT.ws_meta);
                const zero = EnergySemantic.evaluate(ENERGY_SEMANTIC_CONTRACT,[{...one,qty:0}],REPORT.ws_meta);
                let rejected = 0;
                for(const cost of [-1,NaN,Infinity]){ try{ EnergySemantic.evaluate(ENERGY_SEMANTIC_CONTRACT,[{...one,cost}],REPORT.ws_meta); } catch(e){rejected++;} }
                return {intensity:metrics.energy_intensity_kgce.value, zero:zero.energy_intensity_kgce.value, rejected};
            }''')
            assert edge == {'intensity': 500, 'zero': None, 'rejected': 3}
            assert not errors, errors
            assert not external_requests, external_requests
        finally:
            browser.close()
    report = {'success': True, 'checked_at_utc': datetime.now(timezone.utc).isoformat(),
              'contract_sha256': contract()['sha256'], 'scalar_cases': checked,
              'cost_shares_reconciled': True, 'actual_ui_single_workshop_and_reset': True,
              'day_type_totals_reconciled': True, 'net_energy_share_follows_filter': True,
              'zero_empty_nonproduction_and_invalid_values_checked': True,
              'browser_errors': errors, 'evidence_directory': str(folder.relative_to(ROOT)),
              'external_requests': external_requests,
              'source_semantic_report_sha256': hashlib.sha256((ROOT / 'output/extensions/semantic_runtime.json').read_bytes()).hexdigest(),
              'source_hashes': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in
                ('governance/browser.py', 'governance/metrics.yaml', 'src/semantic_metrics.js', 'src/report_template.html', 'src/make_report.py', 'tools/verify_browser_semantics.py')}}
    (folder / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (ROOT / 'output/extensions/browser_semantic_runtime.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    run()
