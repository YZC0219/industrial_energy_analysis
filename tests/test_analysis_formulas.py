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
