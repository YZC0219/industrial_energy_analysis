"""Browser/Python reconciliation for isolated analytical-formula report."""
from datetime import datetime, timezone
from copy import deepcopy
import csv
import hashlib
import json
import math
from pathlib import Path
from playwright.sync_api import sync_playwright
from governance.analysis_formulas import (month_change, calendar_average, pareto,
                                         dispersion, intensity_difference, convert_difference,
                                         residual_band, cusum_summary, proportional_scenario)
from tools.build_analysis_report import build, hashes, ROOT
from src.make_report import render


def verify_remaining(page,data,folder):
    page.locator('[data-view="report"]').click()
    for ident,groups in [('baseline',data['baseline']),('cusum',data['cusum'])]:
        for index,group in enumerate(groups):
            page.locator(f'#{"base" if ident=="baseline" else "cusum"}-seg button[data-i="{index}"]').click()
            svg=page.locator(f'[data-chart="{ident}"] svg')
            if ident=='baseline':
                expected=residual_band(group['mu_r'],group['sd_r'])
                observed=page.evaluate('g=>EnergyAnalysis.residualBand(g.mu_r,g.sd_r)',group)
                for key,value in expected.items():close(value,observed[key])
                close(expected['upper'],float(svg.get_attribute('data-band-upper')))
                close(expected['lower'],float(svg.get_attribute('data-band-lower')))
            else:
                expected=cusum_summary(group['points'],group['h'])
                assert page.evaluate('g=>EnergyAnalysis.cusumSummary(g.points,g.h)',group)==expected
                close(group['h'],float(svg.get_attribute('data-cusum-threshold')))
                assert f"已有 {len(group['points'])} 条日记录" in page.locator('#cusum-cap').inner_text()
    stored=page.evaluate('() => ({baseline:REPORT.baseline,cusum:REPORT.cusum})')
    assert stored=={'baseline':data['baseline'],'cusum':data['cusum']}
    # Validate the existing producer's archived scenarios without regenerating its outputs.
    archived=ROOT/'output/phase5_assessment/energy_savings_scenarios.csv'
    archived_bytes=archived.read_bytes()
    with archived.open(encoding='utf-8-sig',newline='') as stream:archived_rows=list(csv.DictReader(stream))
    for row in archived_rows:
        expected=proportional_scenario(float(row['energy_tce']),float(row['cost_yuan']),float(row['co2_t']),float(row['reduction_rate_pct']))
        for field,key in [('scenario_saving_tce','tce'),('scenario_cost_saving_yuan','cost'),('scenario_co2_reduction_t','co2')]:
            close(float(row[field]),expected[key])
    assert archived.read_bytes()==archived_bytes
    scopes=[{}, {'code':'W04','from':'2025-01-15','to':'2025-03-10'},
            {'day':'周末','from':'2025-01-01','to':'2025-03-31'},
            {'code':'W07'}, {'from':'2024-01-01','to':'2024-12-31'}]
    scenario_checks=[]
    for scope in scopes:
        page.locator('#f-reset').click()
        if scope.get('from'):
            page.locator('#f-range button[data-k="custom"]').click()
            page.locator('#f-from').fill(scope['from']);page.locator('#f-to').fill(scope['to'])
            page.locator('#f-to').dispatch_event('change')
        if scope.get('code'):
            for code in sorted(data['ws_meta']):
                page.locator(f'#f-ws input[data-ws="{code}"]').set_checked(code==scope['code'])
        if scope.get('day'):
            for day in ['工作日','节假日']:page.locator(f'#f-day button[data-k="{day}"]').click()
        eligible=[r for r in data['ws_daily'] if r['d'].startswith('2025-') and r['code']!='W07'
                  and (not scope.get('code') or r['code']==scope['code'])
                  and (not scope.get('from') or scope['from']<=r['d']<=scope['to'])
                  and (not scope.get('day') or r['day']==scope['day'])]
        allowed={(r['code'],r['d']) for r in eligible}
        energies={}
        for row in data['energy_daily']:
            if (row['code'],row['d']) in allowed:energies.setdefault(row['e'],[]).append(row)
        for rate in [3,5,10]:
            page.locator('#scenario-rate').select_option(str(rate))
            if not energies:
                assert page.locator('#scenario-table table').count()==0
                assert '没有可用记录' in page.locator('#scenario-table').inner_text()
            else:
                rows=[];totals=[]
                for code,records in sorted(energies.items()):
                    values=[proportional_scenario(r['tce'],r['cost'],r['co2'],rate) for r in records]
                    total={k:math.fsum(v[k] for v in values) for k in ('tce','cost','co2')}
                    observed=page.evaluate('rows=>EnergyAnalysis.sumScenario(rows)',values)
                    for key in total:close(total[key],observed[key])
                    totals.append(total)
                    rows.append([data['en_meta'][code]['name']]+[f'{total[k]:,.2f}' for k in ('tce','cost','co2')])
                total={k:math.fsum(v[k] for v in totals) for k in ('tce','cost','co2')}
                rows.append(['合计']+[f'{total[k]:,.2f}' for k in ('tce','cost','co2')])
                observed=page.locator('#scenario-table tbody tr').evaluate_all('rows=>rows.map(r=>Array.from(r.cells).map(c=>c.textContent))')
                assert observed==rows,(scope,rate,observed,rows)
            assert f'{len({r["d"] for r in eligible})} 个有数据日期' in page.locator('#scenario-scope').inner_text()
            scenario_checks.append({'scope':scope,'rate_percent':rate,'passed':True})
    page.locator('#f-reset').click();page.locator('#scenario-rate').select_option('3')
    page.locator('#energy-scenario').screenshot(path=str(folder/'scenarios.png'))
    # Boundaries preserve signed residuals and flags at rounded CUSUM limits.
    point={'d':'2025-01-01','z':2,'hi':9.9,'lo':0,'hi_raw':0,'lo_raw':0,'alarm':True,'side':'偏高'}
    boundary=page.evaluate('p=>({band:EnergyAnalysis.residualBand(-1,2),cusum:EnergyAnalysis.cusumSummary([p],9.9)})',point)
    assert boundary['band']==residual_band(-1,2) and boundary['cusum']['n_alarm']==1
    return {'residual_workshops':len(data['baseline']),'cusum_workshops':len(data['cusum']),
            'stored_series_unchanged':True,'archived_scenario_rows':len(archived_rows),
            'archived_scenario_sha256':hashlib.sha256(archived_bytes).hexdigest(),
            'actual_scenario_filters':scenario_checks}


def verify_industrial(page, data, folder):
    """Read real report tables plus separate synthetic signed/zero fixtures."""
    page.locator('[data-view="report"]').click()
    page.locator('[data-toggle="savechain"]').click()
    table=page.locator('[data-table="savechain"] tbody tr').evaluate_all(
        'rows=>rows.map(r=>Array.from(r.cells).map(c=>c.textContent))')
    expected_rows=[]
    for row in data['savings']:
        value=convert_difference(row['save'],data['totals'])
        expected_rows.append([row['name'],f"{row['save']:,.1f}",f"{value['co2']:,.1f}",
                              f"{value['cost']/1e4:,.1f}",f"{row['pct']:.2f}%"])
    total=convert_difference(data['save_total'],data['totals'])
    expected_rows.append(['合计',f"{data['save_total']:,.1f}",f"{total['co2']:,.1f}",f"{total['cost']/1e4:,.1f}",'—'])
    assert table==expected_rows,(table,expected_rows)
    stored=page.evaluate('() => REPORT.unit_series.map(g=>g.points.map(p=>({z:p.z,out:p.out})))')
    assert stored==[[{'z':p['z'],'out':p['out']} for p in g['points']] for g in data['unit_series']]
    assert page.locator('[data-fill="cost-all"]').inner_text()==f"{total['cost_factor']:,.0f}"
    page.locator('[data-chart="savechain"] .c-hit').first.hover()
    tip=page.locator('[data-chart="savechain"] .tip').inner_text()
    assert f"{convert_difference(data['savings'][0]['save'],data['totals'])['cost']/1e4:,.1f}" in tip
    # Check every fixed baseline without changing its stored SQL flags or Z values.
    for group in data['unit_series']:
        observed=page.evaluate('g=>EnergyAnalysis.dispersion(g.mu,g.sd)',group)
        for key,value in dispersion(group['mu'],group['sd']).items():close(value,observed[key])
    formula_cases=[{'qty':2000,'baseline':12,'current':10},
                   {'qty':2000,'baseline':10,'current':12},{'qty':0,'baseline':10,'current':12}]
    for case in formula_cases:
        close(intensity_difference(case['qty'],case['baseline'],case['current']),
              page.evaluate('r=>EnergyAnalysis.baselineDifference(r.qty,r.baseline,r.current)',case))
    # Fixtures reuse the canonical HTML renderer; they are deliberately synthetic, never exported as business results.
    for name,totals in [('cancelled',{'tce':100,'co2':25,'cost':333}),
                        ('zero',{'tce':0,'co2':0,'cost':0})]:
        sample=deepcopy(data)
        sample['totals'].update(totals)
        sample['savings']=[{'name':'合成正差额','save':1,'pct':-1},
                           {'name':'合成负差额','save':-1,'pct':1},
                           {'name':'合成零差额','save':0,'pct':0}]
        sample['save_total']=sample['save_co2']=sample['save_cost']=0
        sample['unit_series'][0].update(mu=0,sd=2,cv=None)
        fixture=folder/f'boundary_{name}.html'
        fixture.write_text(render(sample),encoding='utf-8')
        page.goto(fixture.as_uri(),wait_until='load')
        page.locator('[data-toggle="savechain"]').click()
        observed=page.locator('[data-table="savechain"] tbody tr').evaluate_all(
            'rows=>rows.map(r=>Array.from(r.cells).map(c=>c.textContent))')
        assert observed[0][1]=='1.0' and observed[1][1]=='-1.0' and observed[2][1]=='0.0'
        if name=='zero':
            assert all(row[2:4]==['—','—'] for row in observed)
            assert page.locator('[data-fill="save-co2"]').inner_text()=='—'
            assert page.locator('[data-fill="save-cost"]').inner_text()=='—'
        else:
            assert observed[0][2]=='0.3' and observed[1][2]=='-0.3'
        svg=page.locator('[data-chart="savechain"] svg')
        zero_x=float(svg.locator('.c-axis').get_attribute('x1'))
        positive=svg.locator('[data-energy-difference="1"]')
        negative=svg.locator('[data-energy-difference="-1"]')
        close(float(positive.get_attribute('x')),zero_x)
        close(float(negative.get_attribute('x'))+float(negative.get_attribute('width')),zero_x)
        assert float(svg.locator('[data-energy-difference="0"]').get_attribute('width'))==0
        assert 'σ/μ = —' in page.locator('#anom-cap').inner_text()
        assert page.locator('[data-chart="anomaly"] svg').count()==1
        page.locator('[data-chart="savechain"] .c-hit').first.hover()
        tooltip=page.locator('[data-chart="savechain"] .tip').inner_text()
        assert ('— tCO₂' if name=='zero' else '0.3 tCO₂') in tooltip
        page.locator('[data-chart="savechain"]').screenshot(path=str(folder/f'boundary_{name}.png'))
    return {'real_savings_table_and_tooltip':True,'fixed_baselines_compared':len(data['unit_series']),
            'signed_and_zero_fixtures':True,'historical_sql_flags_unchanged':True}


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
            remaining=verify_remaining(page,data,folder)
            industrial=verify_industrial(page,data,folder)
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
            'industrial_formulas':industrial,
            'remaining_formulas':remaining,
            'evidence_directory':str(folder.relative_to(ROOT))}
    (folder/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (folder/'verification.md').write_text(
        '# 分析公式浏览器验收\n\n结论：通过。\n\n'
        '- 月环比、七日历日均值和帕累托在五种范围下与 Python 一致。\n'
        '- 车间固定基线控制带与变异系数核对通过。\n'
        '- 实际报告的基线差额表格、折算系数和鼠标提示核对通过。\n'
        '- 七个车间的残差控制带、CUSUM 阈值和统计与 Python 一致，导出轨迹及标记未改变。\n'
        '- 原有阶段五归档情景逐行核对通过，没有重写归档文件。\n'
        '- 3%／5%／10% 情景在全量、部分月、周末、公用工程及空年度五种筛选下通过。\n'
        '- 合成正负差额、合计抵消、零能源分母和零均值场景通过。\n'
        '- 负差额在零轴左侧，零差额条形宽度为零；无分母的折算显示为“—”。\n'
        '- 历史 SQL 异常标记保持不变，浏览器脚本错误为零。\n\n'
        '边界页面为合成测试产物，不能作为实际业务结果。未运行数仓更新或替换 CDC 部署。\n',encoding='utf-8')
    print(json.dumps({'success':True,'report':str(folder/'report.html'),'verification':str(folder/'verification.json')},ensure_ascii=False))
    return report


if __name__=='__main__':
    verify()
