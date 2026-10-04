"""Apply reviewed analytical formulas to the canonical report renderer."""
import json
import re
from pathlib import Path
from governance.analysis_formulas import contract

ROOT = Path(__file__).resolve().parents[1]


def once(html, old, new):
    if html.count(old)!=1:
        raise ValueError('Shared template changed; analytical integration needs review')
    return html.replace(old,new,1)


def integrate(html):
    # Keep generated reports offline; use the existing system-font fallback.
    html=re.sub(r'^<link[^>\n]+https://fonts\.(?:googleapis|gstatic)\.com[^>\n]*>\n?', '', html, flags=re.MULTILINE)
    engine=(ROOT/'src/analysis_formulas.js').read_text(encoding='utf-8')
    value=json.dumps(contract(),ensure_ascii=False,allow_nan=False).replace('<','\\u003c')
    html=once(html,'<script>\n(function(){','<script>\nwindow.ENERGY_ANALYSIS_CONTRACT='+value+';\n'+engine+'\n</script>\n<script>\n(function(){')
    old='''  for (var i = 0; i < monthly.length; i++){
    if (i > 0 && monthly[i-1].tce > 0){
      monthly[i].mom = (monthly[i].tce - monthly[i-1].tce) / monthly[i-1].tce * 100;
    }
  }'''
    html=once(html,old,'''  var monthChanges=window.EnergyAnalysis.months(monthly);
  monthly.forEach(function(row,i){row.mom=monthChanges[i];});''')
    old='''  var ma = [], win = 7;
  for (var j = 0; j < vals.length; j++){
    var lo = Math.max(0, j - win + 1);
    var s = 0; for (var q = lo; q <= j; q++) s += vals[q];
    ma.push(s / (j - lo + 1));
  }'''
    html=once(html,old,'  var ma=window.EnergyAnalysis.average(days,vals);')
    old='''  var rows = VIEW.energy_mix.slice().sort(function(a, b){ return b.cost - a.cost; });
  var total = rows.reduce(function(a, r){ return a + r.cost; }, 0);'''
    html=once(html,old,'''  var pareto=window.EnergyAnalysis.pareto(VIEW.energy_mix);
  var rows=pareto.rows, total=pareto.total;''')
    old='''  var cum = 0, cums = [];
  rows.forEach(function(r, i){
    var p = r.cost / total * 100;
    cum += p; cums.push(cum);'''
    html=once(html,old,'''  var cums=pareto.cumulative;
  rows.forEach(function(r, i){
    var p=pareto.shares[i];''')
    old='''  for (var k = 0; k < cums.length; k++) if (cums[k] >= 80){ knee = k; break; }'''
    html=once(html,old,'  if(pareto.threshold_index!==null) knee=pareto.threshold_index;')
    html=html.replace('7 日移动平均','7 日历日均值（仅计有数据日）')
    html=html.replace('7 日均</span>','7 日历日均值</span>')
    html=once(html,"'<span><i class=\"line\" style=\"background:' + T.d2 + '\"></i>环比</span>'",
              "'<span><i class=\"line\" style=\"background:' + T.d2 + '\"></i>相邻月筛选总量环比（部分月份仅比较选中天数）</span>'")
    return html


