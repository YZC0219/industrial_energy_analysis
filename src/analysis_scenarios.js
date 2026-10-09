/* Runs inside the report closure, reusing its existing selection helpers. */
function renderEnergyScenario(){
  var settings=window.ENERGY_ANALYSIS_CONTRACT.formulas.proportional_energy_scenario;
  var input=document.getElementById('scenario-rate'), host=document.getElementById('scenario-table');
  if(!input || !host) return;
  if(!input.options.length){
    settings.report_rates_percent.forEach(function(rate){
      var option=document.createElement('option'); option.value=rate; option.textContent=rate+'%'; input.appendChild(option);
    });
  }
  var rate=Number(input.value), allowed=new Set(), dates=new Set(), groups={};
  D.ws_daily.forEach(function(row){
    if(row.code!==settings.exclude_workshop && row.d.slice(0,4)===String(settings.report_year) &&
       wsMatch(row.code) && dateMatch(row.d) && dayMatch(row.day)){
      allowed.add(row.code+'|'+row.d); dates.add(row.d);
    }
  });
  D.energy_daily.forEach(function(row){
    if(!allowed.has(row.code+'|'+row.d)) return;
    var g=groups[row.e] || (groups[row.e]={tce:[],cost:[],co2:[]});
    g.tce.push(row.tce); g.cost.push(row.cost); g.co2.push(row.co2);
  });
  var text=function(value){return String(value).replace(/[&<>"']/g,function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
  });};
  var ordered=Object.keys(groups).sort(), results=[];
  // Apply the same per-record proportional definition before summing; never use factory average factors.
  ordered.forEach(function(code){
    var g=groups[code], values=g.tce.map(function(tce,i){return window.EnergyAnalysis.scenario(tce,g.cost[i],g.co2[i],rate);});
    var value=window.EnergyAnalysis.sumScenario(values);
    results.push(value);
  });
  document.getElementById('scenario-scope').textContent=settings.report_year+' 年 · 当前筛选 · 剔除 '+settings.exclude_workshop+
    ' 公用工程 · '+dates.size+' 个有数据日期 · 假设降幅 '+rate+'%';
  if(!ordered.length){
    host.textContent='当前筛选在该年度没有可用记录，未生成节能情景。'; return;
  }
  var rows=ordered.map(function(code,i){
    var value=results[i], name=D.en_meta[code] ? D.en_meta[code].name : code;
    return [text(name),num(value.tce,2),num(value.cost,2),num(value.co2,2)];
  });
  var total=window.EnergyAnalysis.sumScenario(results);
  rows.push(['合计',num(total.tce,2),num(total.cost,2),num(total.co2,2)]);
  host.innerHTML=buildTable(['能源品种','假设能耗减少 tce','费用减少估算 元','碳排减少估算 tCO₂'],rows);
}

var energyScenarioInput=document.getElementById('scenario-rate');
if(energyScenarioInput) energyScenarioInput.addEventListener('change',renderEnergyScenario);
renderEnergyScenario();
