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
    return integrate_remaining(integrate_industrial(html))


def integrate_industrial(html):
    html=once(html, 'var VIEW = D;', '''var VIEW = D;
function estimate(value, digits){ return value === null ? "—" : num(value, digits); }
function estimatedMoney(value){ return value === null ? "—" : num(value / 1e4, 1); }''')
    html=once(html, '  var up = g.mu + 2 * g.sd, dn = Math.max(0, g.mu - 2 * g.sd);',
              '  var band=window.EnergyAnalysis.dispersion(g.mu,g.sd), up=band.upper, dn=band.lower;')
    html=once(html, ' + g.cv + "%</b>　检出 <b>"',
              ' + (g.cv === null ? "—" : g.cv + "%") + "</b>　检出 <b>"')
    html=once(html, '''      var co2 = r.save * (D.save_co2 / (tce || 1));
      var yuan = r.save * (D.save_cost / (tce || 1));''',
              '''      var conversion=window.EnergyAnalysis.conversion(r.save,D.totals);
      var co2=conversion.co2, yuan=conversion.cost;''')
    html=once(html, 'num(co2, 1) + " tCO₂</span></div>"', 'estimate(co2, 1) + " tCO₂</span></div>"')
    html=once(html, 'num(yuan / 1e4, 1) + " 万</span></div>"', 'estimatedMoney(yuan) + " 万</span></div>"')
    old='''  savechain: function(){
    var tce = D.save_total || 1;
    return buildTable(["车间","节能量 tce","折碳减排 tCO₂","折费用 万元","单耗同比"],
      D.savings.map(function(r){
        return [r.name, num(r.save,1), num(r.save * D.save_co2 / tce, 1),
                num(r.save * D.save_cost / tce / 1e4, 1), r.pct.toFixed(2)+"%"];
      }).concat([["合计", num(D.save_total,1), num(D.save_co2,1),
                  num(D.save_cost/1e4,1), "—"]]));
  },'''
    new='''  savechain: function(){
    var total=window.EnergyAnalysis.conversion(D.save_total,D.totals);
    return buildTable(["车间","基线差额 tce","折碳估算 tCO₂","折费用估算 万元","单耗同比"],
      D.savings.map(function(r){
        var value=window.EnergyAnalysis.conversion(r.save,D.totals);
        return [r.name,num(r.save,1),estimate(value.co2,1),estimatedMoney(value.cost),r.pct.toFixed(2)+"%"];
      }).concat([["合计",num(D.save_total,1),estimate(total.co2,1),estimatedMoney(total.cost),"—"]]));
  },'''
    html=once(html,old,new)
    for old,new in [
        ('return (D.totals.co2 / D.totals.tce).toFixed(4) + " tCO₂/tce";',
         'return estimate(window.EnergyAnalysis.conversion(0,D.totals).carbon_factor,4) + " tCO₂/tce";'),
        ('"save-co2":     function(){ return num(D.save_co2, 1); },',
         '"save-co2":     function(){ return estimate(window.EnergyAnalysis.conversion(D.save_total,D.totals).co2,1); },'),
        ('"save-cost":    function(){ return num(D.save_cost / 1e4, 1); },',
         '"save-cost":    function(){ return estimatedMoney(window.EnergyAnalysis.conversion(D.save_total,D.totals).cost); },\n    "cost-all": function(){ return estimate(window.EnergyAnalysis.conversion(0,D.totals).cost_factor,0); },'),
        ('"save-plane":   function(){ return num(D.save_co2 / 0.162, 0); },',
         '"save-plane":   function(){ var value=window.EnergyAnalysis.conversion(D.save_total,D.totals).co2; return value === null ? "—" : num(value / 0.162,0); },'),
    ]:
        html=once(html,old,new)
    html=once(html, '''  var maxV = Math.max.apply(null, rows.map(function(r){ return r.save; }));
  var sc = ticks(maxV, 3);
  var x = function(v){ return plotX + v / sc.top * plotW; };''',
              '''  var lo=Math.min(0,Math.min.apply(null,rows.map(function(r){return r.save;})));
  var hi=Math.max(0,Math.max.apply(null,rows.map(function(r){return r.save;})));
  if(lo===hi) hi=1;
  var sc=rangeTicks(lo,hi,3);
  var x=function(v){return plotX+(v-lo)/(hi-lo)*plotW;};''')
    html=once(html, '  var barH = 12, tce = D.save_total || 0;', '  var barH = 12;')
    start=html.index('function drawSaveChain(')
    end=html.index('\n/* ================= chart: 增速差',start)
    chart=html[start:end]
    html=once(html,chart,once(chart,
              '  mk("line", {x1:plotX, y1:padT-4, x2:plotX, y2:padT + rows.length*rowH, class:"c-axis"}, svg);',
              '  mk("line", {x1:x(0), y1:padT-4, x2:x(0), y2:padT + rows.length*rowH, class:"c-axis"}, svg);'))
    html=once(html, '''    var w = Math.max(2, x(r.save) - plotX);
    var rad = Math.min(4, w / 2);
    tx(svg, 0, midY + 4, r.name, "c-name");
    mk("path", {
      d:"M"+plotX+","+y+" h"+(w-rad)+" a"+rad+","+rad+" 0 0 1 "+rad+","+rad+
        " v"+(barH-2*rad)+" a"+rad+","+rad+" 0 0 1 "+(-rad)+","+rad+" h"+(-(w-rad))+" z",
      fill:T.m1
    }, svg);
    tx(svg, plotX + w + 7, midY + 4, num(r.save, 1), "c-value");''',
              '''    var start=Math.min(x(0),x(r.save)), width=Math.abs(x(r.save)-x(0));
    tx(svg,0,midY+4,r.name,"c-name");
    mk("rect",{x:start,y:y,width:width,height:barH,
      fill:r.save<0 ? T.critical : T.m1,"data-energy-difference":r.save},svg);
    tx(svg,x(r.save)+(r.save<0 ? -7 : 7),midY+4,num(r.save,1),"c-value",r.save<0 ? "end" : "start");''')
    # Reviewed wording describes arithmetic and sampling limits, not causal savings.
    html=once(html, '''        再减去 2025 年实际能耗，差额即为<b>可归因于能效提升</b>的节能量。
        这条口径把"产量增长带来的能耗"和"效率提升省下的能耗"分开了 —— 前者不算功劳，后者才算。
        碳排按全期碳强度 <b data-fill="ci-all">—</b> 折算；费用按全期单位能耗成本 ¥3,378/tce 折算。''',
              '''        再减去按 2025 年单耗推算的能耗，得到<b>历史单耗基线差额</b>；负值表示单耗上升。
        仅在同一车间和产量单位内比较，不能据此确认某项改造的因果效果。单耗取自导出的 Q08，分项差额保留一位小数后汇总。
        碳排按全期碳强度 <b data-fill="ci-all">—</b> 折算；费用按同一全期单位能耗成本 ¥<b data-fill="cost-all">—</b>/tce 折算。此处为全区间口径，不随筛选变化。''')
    html=once(html, '''<b>小基数车间的 Z 值会被放大</b>　σ/μ 越大，同样的绝对波动就越容易顶破 2σ。本例中<b>表面处理车间</b>的变异系数最高，其"超限日"里有相当一部分只是产量小、基数低造成的抖动，不是真的跑冒滴漏。<b>Z 值大 ≠ 问题严重</b>——要把 Z 值和产量一起看，产量越低的超限越可能是统计假象。这也是为什么下一阶段的算法对比要引入产量基线。''',
              '''<b>变异系数与 Z 值分开解释</b>　CV = σ/μ 表示相对离散程度；Z = (当日单耗 − μ)/σ。同样的绝对偏差下，σ 越大，Z 的绝对值越小。均值为零时 CV 显示为“—”。产量偏低可能抬高单位产品能耗，超限日仍需结合产量和日历复核；本图沿用 SQL 固定基线与异常标记，不按筛选范围重新判定。''')
    for old,new in [
        ('差额就是节能量','可用历史单耗基线比较差额'),
        ('节能量换算成碳与经济账','历史单耗基线差额与碳、费用估算'),
        ('三件能被考核的东西：<b>省了多少标煤、少了多少碳、值多少钱</b>','三个估算量：<b>基线差额、折碳差额、折费用差额</b>'),
        ('各车间节能量与折算收益','各车间基线差额与折算估算'),
        ('可归因的节能量','可比较的基线差额'),
        ('各车间可归因节能量','各车间历史单耗基线差额'),
        ('两年间可归因于能效提升的节能量','历史单耗基线差额'),
        ('省下的标煤','折标煤基线差额'),
        ('少排的碳','折碳差额估算'),
        ('省下的能源费用','折费用差额估算'),
        ('这是毛收益，不是投资回报','这是按平均系数折算的毛额估算，未验证措施效果'),
        ('条形为节能量（tce）','条形为基线差额（tce），负值表示单耗上升'),
        ('的节能量——高单耗车间','的基线差额——高单耗车间'),
        ('></i>节能量</span>','></i>基线差额</span>'),
    ]:
        html=html.replace(old,new)
    return html


def integrate_remaining(html):
    html=once(html,'  var up = g.mu_r + 2 * g.sd_r, dn = g.mu_r - 2 * g.sd_r;',
              '  var residualBand=window.EnergyAnalysis.residualBand(g.mu_r,g.sd_r), up=residualBand.upper, dn=residualBand.lower;')
    html=once(html,'Math.max(Math.abs(up), Math.abs(dn), g.sd_r * 2) * 1.25',
              'Math.max(Math.abs(up), Math.abs(dn), residualBand.half_width) * 1.25')
    html=once(html,'"aria-label":g.name + " 单耗残差与 ±2σ 控制带"}',
              '"aria-label":g.name + " 单耗残差与 ±2σ 控制带", "data-band-upper":up,"data-band-lower":dn}')
    html=once(html,'  var g = gs[cusumSel], pts = g.points, H = g.h;',
              '  var g=gs[cusumSel], pts=g.points, summary=window.EnergyAnalysis.cusumSummary(pts,g.h), H=summary.threshold;')
    html=once(html,'"aria-label":g.name + " 单耗 CUSUM 累积和控制图"}',
              '"aria-label":g.name + " 单耗 CUSUM 累积和控制图","data-cusum-threshold":H}')
    html=once(html,'''function cusumCaption(){
  var g = D.cusum[cusumSel];
  return "判定限 h = <b>" + g.h.toFixed(1) + "σ</b>（按 731 天样本量标定）　" +''',
              '''function cusumCaption(){
  var source=D.cusum[cusumSel], g=window.EnergyAnalysis.cusumSummary(source.points,source.h);
  return "判定限 h = <b>" + g.threshold.toFixed(1) + "σ</b>（来自 Q27；已有 " + g.observed_points + " 条日记录，记录数不代表重新标定）　" +''')
    html=once(html, "'σ 的判定线下没有持续偏移。</p>';", "'σ 的判定线下没有记录到报警日。</p>';")
    html=html.replace('<b>h = 9.9σ</b>','<b data-fill="cusum-limit">—</b>')
    html=once(html,'"cusum-lo-z-lo": function(){ return dv.cusum_lo_z_lo.toFixed(2); },',
              '''"cusum-limit": function(){
      var limits=Array.from(new Set(D.cusum.map(function(g){return g.h;})));
      return limits.length===1 ? "h = "+limits[0].toFixed(1)+"σ（Q27）" : "各车间阈值不同，详见控制图";
    },
    "cusum-lo-z-lo": function(){ return dv.cusum_lo_z_lo.toFixed(2); },''')
    html=once(html,'''  </section>

  <section class="sec">
    <div class="sec-head">
      <p class="eyebrow">损耗</p>''', '''  </section>

  <section class="sec" id="energy-scenario">
    <div class="sec-head">
      <p class="eyebrow">情景</p><h2>按能源品种估算假设节能量</h2>
      <p class="sec-sub">复用现有节能情景规则，对各能源的消耗、实际费用与碳排按同一比例减少。结果为假设估算，未计改造成本、负荷影响与价格变化，不能作为实测收益或措施承诺。</p>
    </div>
    <figure class="card" style="margin:0">
      <figcaption class="card-head">
        <label for="scenario-rate">假设降幅</label> <select id="scenario-rate" aria-label="假设节能降幅"></select>
        <p class="card-sub" id="scenario-scope" role="status" aria-live="polite"></p>
      </figcaption>
      <div id="scenario-table"></div>
    </figure>
  </section>

  <section class="sec">
    <div class="sec-head">
      <p class="eyebrow">损耗</p>''')
    html=once(html,'  VIEW = rebuildView(aggAll());','  VIEW = rebuildView(aggAll());\n  renderEnergyScenario();')
    scenario=(ROOT/'src/analysis_scenarios.js').read_text(encoding='utf-8')
    html=once(html,'})();\n</script>\n</body>',scenario+'\n})();\n</script>\n</body>')
    return html


