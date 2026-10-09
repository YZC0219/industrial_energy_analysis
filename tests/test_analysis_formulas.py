from copy import deepcopy
import math
import pytest
from governance import analysis_formulas as formulas
from governance.analysis_report import integrate


def test_months_require_calendar_adjacency_and_positive_previous():
    assert formulas.month_change([{'ym':'2024-12','tce':100},{'ym':'2025-01','tce':120},
        {'ym':'2025-03','tce':30},{'ym':'2025-04','tce':0},{'ym':'2025-05','tce':4}]) == pytest.approx([None,20,None,-100,None])


def test_weekend_average_is_calendar_window_not_seven_weekends():
    days=['2025-01-04','2025-01-05','2025-01-11','2025-01-12']
    assert formulas.calendar_average(days,[2,4,20,40])==pytest.approx([2,3,12,30])
    assert formulas.calendar_average([],[])==[]


def test_pareto_tie_stability_zero_and_threshold():
    result=formulas.pareto([{'id':'a','cost':4},{'id':'b','cost':4},{'id':'c','cost':2}])
    assert [r['id'] for r in result['rows']]==['a','b','c']
    assert result['shares']==[40,40,20] and result['cumulative']==[40,80,100]
    assert result['threshold_index']==1
    zero=formulas.pareto([{'cost':0}])
    assert zero['cumulative']==[0] and zero['threshold_index'] is None


@pytest.mark.parametrize('values', [[-1],[math.nan],[math.inf],[True]])
def test_analytical_measure_rejects_invalid_values(values):
    with pytest.raises(ValueError):
        formulas.calendar_average(['2025-01-01'],values)


@pytest.mark.parametrize('days', [['2025-01-01','2025-01-01'],['2025-01-02','2025-01-01'],['2025-02-30'],['20250101'],['0000-01-01']])
def test_analytical_dates_reject_duplicates_disorder_and_invalid_dates(days):
    with pytest.raises(ValueError):
        formulas.calendar_average(days,[1]*len(days))


def test_template_drift_is_rejected_without_silently_generating_old_formulas():
    with pytest.raises(ValueError,match='template changed'):
        integrate('<html>different template</html>')


def test_changed_missing_day_policy_requires_explicit_review(monkeypatch):
    value=deepcopy(formulas.contract())
    value['formulas']['daily_energy_average']['missing_days']='zero_fill'
    monkeypatch.setattr(formulas.yaml,'safe_load',lambda text:value)
    with pytest.raises(ValueError,match='requires review'):
        formulas.contract()


def test_monthly_change_overflow_cannot_become_missing_data():
    with pytest.raises(ValueError,match='overflow'):
        formulas.month_change([{'ym':'2025-01','tce':1e-320},{'ym':'2025-02','tce':1}])


def test_canonical_renderer_includes_all_three_formulas():
    from src.make_report import render
    html=render({'totals':{}})
    assert 'window.ENERGY_ANALYSIS_CONTRACT=' in html
    assert 'window.EnergyAnalysis.months(monthly)' in html
    assert 'window.EnergyAnalysis.average(days,vals)' in html
    assert 'window.EnergyAnalysis.pareto(VIEW.energy_mix)' in html
    assert 'window.EnergyAnalysis.dispersion(g.mu,g.sd)' in html
    assert 'window.EnergyAnalysis.conversion(r.save,D.totals)' in html
    assert 'window.EnergyAnalysis.residualBand(g.mu_r,g.sd_r)' in html
    assert 'window.EnergyAnalysis.cusumSummary(pts,g.h)' in html
    assert 'id="scenario-rate"' in html
    assert 'window.EnergyAnalysis.scenario(tce,g.cost[i],g.co2[i],rate)' in html
    assert '¥3,378/tce' not in html
    assert '可归因于能效提升' not in html
    assert 'σ 越大，Z 的绝对值越小' in html
    assert 'fonts.googleapis.com' not in html


def test_failed_report_render_keeps_previously_accepted_output(tmp_path,monkeypatch):
    from src import make_report
    template=tmp_path/'invalid.html'
    template.write_text('missing report markers',encoding='utf-8')
    target=tmp_path/'accepted.html'
    target.write_text('previous accepted report',encoding='utf-8')
    monkeypatch.setattr(make_report,'TEMPLATE',str(template))
    monkeypatch.setattr(make_report,'OUT_DIR',str(tmp_path))
    monkeypatch.setattr(make_report,'build',lambda:{'totals':{}})
    with pytest.raises(SystemExit,match='缺少'):
        make_report.main(['--input-dir',str(tmp_path),'--output',str(target)])
    assert target.read_text(encoding='utf-8')=='previous accepted report'


def test_dispersion_separates_relative_variation_from_absolute_z():
    assert formulas.dispersion(10,2)=={'upper':14,'lower':6,'cv':20}
    assert formulas.dispersion(0,2)=={'upper':4,'lower':0,'cv':None}
    assert formulas.dispersion(10,0)=={'upper':10,'lower':10,'cv':0}


def test_intensity_difference_preserves_adverse_change_and_units():
    assert formulas.intensity_difference(2000,12,10)==4
    assert formulas.intensity_difference(2000,10,12)==-4
    assert formulas.intensity_difference(0,10,12)==0
    # Two huge equal counterfactual totals must cancel without overflowing.
    assert formulas.intensity_difference(1e308,1e308,1e308)==0


def test_signed_conversion_does_not_infer_factors_from_cancelled_savings():
    totals={'tce':100,'co2':25,'cost':333}
    positive=formulas.convert_difference(1,totals)
    negative=formulas.convert_difference(-1,totals)
    assert positive=={'carbon_factor':0.25,'cost_factor':3.33,'co2':0.25,'cost':3.33}
    assert negative['co2']==-0.25 and negative['cost']==-3.33
    assert formulas.convert_difference(0,totals)['cost_factor']==3.33
    assert formulas.convert_difference(1,{'tce':0,'co2':0,'cost':12})=={
        'carbon_factor':None,'cost_factor':None,'co2':None,'cost':None}


@pytest.mark.parametrize('callback',[
    lambda:formulas.dispersion(True,1),lambda:formulas.dispersion(1,-1),
    lambda:formulas.intensity_difference(1,math.nan,1),
    lambda:formulas.convert_difference(math.inf,{'tce':1,'co2':1,'cost':1}),
    lambda:formulas.dispersion(1,1e308),
    lambda:formulas.intensity_difference(1e308,1e308,0),
    lambda:formulas.convert_difference(1e308,{'tce':1,'co2':2,'cost':1}),
])
def test_industrial_formulas_reject_invalid_inputs_and_overflow(callback):
    with pytest.raises(ValueError):callback()


def test_changed_industrial_conversion_policy_requires_review(monkeypatch):
    value=deepcopy(formulas.contract())
    value['formulas']['energy_difference_conversion']['coefficient_source']='rounded_savings'
    monkeypatch.setattr(formulas.yaml,'safe_load',lambda text:value)
    with pytest.raises(ValueError,match='requires review'):formulas.contract()


def test_signed_residual_band_never_uses_nonnegative_energy_clamp():
    assert formulas.residual_band(-1,2)=={'upper':3,'lower':-5,'half_width':4}
    assert formulas.residual_band(-1,0)=={'upper':-1,'lower':-1,'half_width':0}


def cusum_point(day, alarm=False, side='', hi=0, lo=0):
    return {'d':day,'z':0,'alarm':alarm,'side':side,'hi':hi,'lo':lo,'hi_raw':0,'lo_raw':0}


def test_cusum_summary_retains_sql_alarm_at_rounded_threshold():
    points=[cusum_point('2025-01-01',True,'偏高',hi=9.9),
            cusum_point('2025-01-02',True,'偏高',hi=10),cusum_point('2025-01-03'),
            cusum_point('2025-01-05',True,'偏低',lo=11)]
    result=formulas.cusum_summary(points,9.9)
    assert result['n_alarm']==3 and result['n_seg']==2
    assert result['n_hi']==2 and result['n_lo']==1 and result['observed_points']==4
    assert result['threshold']==9.9 and result['max_lo']==11
    # Even a rounded value equal to the line must not overwrite SQL's classification.
    assert formulas.cusum_summary([cusum_point('2025-01-01',hi=9.9)],9.9)['n_alarm']==0
    assert formulas.cusum_summary([],7)['n_seg']==0


@pytest.mark.parametrize('points,threshold',[
    ([cusum_point('2025-01-01')],0),
    ([cusum_point('2025-01-01'),cusum_point('2025-01-01')],9.9),
    ([cusum_point('2025-01-02'),cusum_point('2025-01-01')],9.9),
    ([cusum_point('2025-01-01',True,'')],9.9),
    ([cusum_point('2025-01-01',False,'偏高')],9.9),
    ([cusum_point('2025-01-01',hi=-1)],9.9),
])
def test_cusum_rejects_ambiguous_input_without_reclassifying(points,threshold):
    with pytest.raises(ValueError):formulas.cusum_summary(points,threshold)


def test_scenario_scales_original_energy_specific_cost_and_carbon():
    assert formulas.proportional_scenario(100,80,20,5)=={'tce':5,'cost':4,'co2':1}
    assert formulas.proportional_scenario(0,80,0,5)=={'tce':0,'cost':4,'co2':0}
    assert formulas.proportional_scenario(100,80,20,0)=={'tce':0,'cost':0,'co2':0}
    assert formulas.proportional_scenario(100,80,20,100)=={'tce':100,'cost':80,'co2':20}


@pytest.mark.parametrize('rate',[-1,101,math.nan,math.inf,True])
def test_scenario_rejects_unreviewed_or_invalid_reductions(rate):
    with pytest.raises(ValueError):formulas.proportional_scenario(100,80,20,rate)


def test_existing_phase5_producer_uses_the_same_scenario_definition(tmp_path):
    import pandas as pd
    from tools.phase5_energy_assessment import build_assessment
    energy=tmp_path/'energy.csv'
    production=tmp_path/'production.csv'
    pd.DataFrame([
        {'record_date':'2024-01-01','workshop_code':'W01','energy_code':'E01','consumption':1000,'cost':100},
        {'record_date':'2025-01-01','workshop_code':'W01','energy_code':'E01','consumption':1000,'cost':100},
        {'record_date':'2025-01-01','workshop_code':'W01','energy_code':'E02','consumption':1000,'cost':200},
        {'record_date':'2025-01-01','workshop_code':'W07','energy_code':'E01','consumption':1000000,'cost':1000000},
    ]).to_csv(energy,index=False)
    pd.DataFrame([
        {'record_date':'2024-01-01','workshop_code':'W01','output_qty':10,'output_unit':'吨'},
        {'record_date':'2025-01-01','workshop_code':'W01','output_qty':10,'output_unit':'吨'},
        {'record_date':'2025-01-01','workshop_code':'W07','output_qty':10,'output_unit':'吨蒸汽'},
    ]).to_csv(production,index=False)
    folder=tmp_path/'assessment'
    build_assessment(energy,production,folder)
    rows=pd.read_csv(folder/'energy_savings_scenarios.csv')
    assert len(rows)==6 and set(rows.workshop_code)=={'W01'}
    five=rows.loc[rows.reduction_rate_pct==5]
    assert five.scenario_saving_tce.sum()==pytest.approx(0.072645)
    assert five.scenario_cost_saving_yuan.sum()==pytest.approx(15)
    assert five.scenario_co2_reduction_t.sum()==pytest.approx(0.136625)


def test_report_rejects_conflicting_exported_cusum_thresholds(monkeypatch):
    from src import make_report
    monkeypatch.setattr(make_report,'OUT_DIR',str(formulas.ROOT/'tests/baseline'))
    original_read=make_report.read
    def changed_read(filename):
        rows=original_read(filename)
        if filename==make_report.Q['cusum']:
            rows[1]['判定限']='7.0'
        return rows
    monkeypatch.setattr(make_report,'read',changed_read)
    with pytest.raises(ValueError,match='stable within each workshop'):make_report.build()
