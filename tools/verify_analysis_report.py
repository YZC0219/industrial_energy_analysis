"""Browser/Python reconciliation for isolated analytical-formula report."""
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from playwright.sync_api import sync_playwright
from governance.analysis_formulas import month_change, calendar_average, pareto
from tools.build_analysis_report import build, hashes, ROOT


def close(left,right,tolerance=1e-8):
    if left is None or right is None:
        assert left is right,(left,right)
    else:
        assert math.isclose(left,right,rel_tol=1e-12,abs_tol=tolerance),(left,right)


def verify():
    verifier_sha=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    folder,data,manifest=build()
    errors=[]
    cases=[]
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch_persistent_context(str(folder/'browser-profile'),
            executable_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe',headless=True,
            viewport={'width':1440,'height':1000})
        try:
            page=browser.new_page()
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.on('console',lambda msg:errors.append(msg.text) if msg.type=='error' else None)
            page.goto((folder/'report.html').as_uri(),wait_until='load')
            page.wait_for_selector('#f-ws input')
            scopes=[{}, {'from':'2025-01-01','to':'2025-03-31'},
                    {'code':'W04','from':'2025-01-15','to':'2025-03-10'},
                    {'day':'周末','from':'2025-01-01','to':'2025-03-31'},
                    {'from':'2030-01-01','to':'2030-01-02'}]
            for scope in scopes:
                rows=[r for r in data['ws_daily'] if (not scope.get('code') or r['code']==scope['code'])
                      and (not scope.get('day') or r['day']==scope['day'])
                      and (not scope.get('from') or r['d']>=scope['from']) and (not scope.get('to') or r['d']<=scope['to'])]
                by_day={}
                for r in rows:by_day.setdefault(r['d'],[]).append(r['tce'])
                days=sorted(by_day)
                values=[math.fsum(by_day[d]) for d in days]
                by_month={}
                for d,v in zip(days,values):by_month.setdefault(d[:7],[]).append(v)
                monthly=[{'ym':m,'tce':math.fsum(by_month[m])} for m in sorted(by_month)]
                allowed={(r['code'],r['d']) for r in rows}
                by_energy={}
                for r in data['energy_daily']:
                    if (r['code'],r['d']) in allowed:by_energy.setdefault(r['e'],[]).append(r['cost'])
                energy=[{'name':code,'cost':math.fsum(vals)} for code,vals in sorted(by_energy.items())]
                expected={'months':month_change(monthly),'average':calendar_average(days,values),'pareto':pareto(energy)}
                observed=page.evaluate('''input => ({months:EnergyAnalysis.months(input.monthly),
                  average:EnergyAnalysis.average(input.days,input.values),pareto:EnergyAnalysis.pareto(input.energy)})''',
                  {'monthly':monthly,'days':days,'values':values,'energy':energy})
                assert all(len(expected[k])==len(observed[k]) for k in ('months','average'))
                for a,b in zip(expected['months'],observed['months']):close(a,b)
                for a,b in zip(expected['average'],observed['average']):close(a,b)
                for a,b in zip(expected['pareto']['cumulative'],observed['pareto']['cumulative']):close(a,b)
                assert expected['pareto']['rows']==observed['pareto']['rows']
                assert expected['pareto']['threshold_index']==observed['pareto']['threshold_index']
                cases.append({'scope':scope,'monthly_points':len(monthly),'daily_points':len(days),
                              'energy_groups':len(energy),'passed':True})
            # Synthetic boundaries differentiate calendar semantics from adjacent observations.
            boundaries=page.evaluate('''() => ({average:EnergyAnalysis.average(
              ['2025-01-04','2025-01-05','2025-01-11','2025-01-12'],[2,4,20,40]),
              months:EnergyAnalysis.months([{ym:'2024-12',tce:100},{ym:'2025-01',tce:120},{ym:'2025-03',tce:30}]),
              pareto:EnergyAnalysis.pareto([{id:'a',cost:4},{id:'b',cost:4},{id:'c',cost:2}]),
              zero:EnergyAnalysis.pareto([{cost:0}])})''')
            assert boundaries['average']==[2,3,12,30]
            close(boundaries['months'][1],20)
            assert boundaries['months'][2] is None and boundaries['pareto']['threshold_index']==1
            assert boundaries['zero']['threshold_index'] is None
            invalid=page.evaluate('''() => {
              let rejected=0;
              for(const v of [-1,NaN,Infinity,true]){try{EnergyAnalysis.average(['2025-01-01'],[v]);}catch(e){rejected++;}}
              for(const ds of [['2025-01-01','2025-01-01'],['2025-01-02','2025-01-01'],['2025-02-30'],['20250101'],['0000-01-01']]){
                try{EnergyAnalysis.average(ds,ds.map(()=>1));}catch(e){rejected++;}}
              try{EnergyAnalysis.months([{ym:'2025-01',tce:1e-320},{ym:'2025-02',tce:1}]);}catch(e){rejected++;}
              return rejected;
            }''')
            assert invalid==10
            # Exercise the report's actual filter and chart handlers using the shared interpreter.
            page.locator('#f-range button[data-k="custom"]').click()
            page.locator('#f-from').fill('2025-01-01')
            page.locator('#f-to').fill('2025-03-31')
            page.locator('#f-to').dispatch_event('change')
            page.locator('[data-toggle="monthly"]').click()
            month_table=page.locator('[data-table="monthly"] tbody tr').evaluate_all('rows=>rows.map(r=>Array.from(r.cells).map(c=>c.textContent))')
            assert month_table[0][-1]=='—'
            for row in month_table[1:]:
                assert row[-1].endswith('%')
            page.locator('[data-view="dash"]').click()
            page.locator('#f-day button[data-k="工作日"]').click()
            page.locator('#f-day button[data-k="节假日"]').click()
            page.wait_for_selector('[data-dchart="ddaily"] svg')
            assert '日历日' in page.locator('#dlg-daily').inner_text()
            assert '相邻月' in page.locator('#dlg-mom').inner_text()
            assert page.locator('[data-dchart="dpareto"] svg').count()==1
            weekend={}
            for row in data['ws_daily']:
                if row['day']=='周末' and '2025-01-01'<=row['d']<='2025-03-31':
                    weekend.setdefault(row['d'],[]).append(row['tce'])
            calendar_days=sorted(weekend)
            expected_last=calendar_average(calendar_days,[math.fsum(weekend[d]) for d in calendar_days])[-1]
            assert page.locator('[data-dchart="ddaily"] .c-value').last.text_content()==f'{expected_last:.1f}'
            page.screenshot(path=str(folder/'dashboard.png'),full_page=False)
            page.locator('#f-reset').click()
            assert '未筛选' in page.locator('#f-scope').inner_text()
            assert not errors,errors
        finally:
            browser.close()
    assert hashes()==manifest['source_hashes'],'Concurrent source changes detected during browser verification'
    assert verifier_sha==hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'Concurrent verifier edits detected'
    report={'success':True,'checked_at_utc':datetime.now(timezone.utc).isoformat(),
            'scope':'canonical report renderer with separate artifacts; no main warehouse runs or deployed CDC changes',
            'source_hashes':manifest['source_hashes'], 'contract_sha256':manifest['contract_sha256'],
            'verification_source_sha256':verifier_sha,
            'html_sha256':manifest['html_sha256'],'cases':cases,'boundary_and_invalid_cases':True,
            'actual_filters_charts_and_reset':True,'browser_errors':errors,
            'evidence_directory':str(folder.relative_to(ROOT))}
    (folder/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'success':True,'report':str(folder/'report.html'),'verification':str(folder/'verification.json')},ensure_ascii=False))
    return report


if __name__=='__main__':
    verify()
