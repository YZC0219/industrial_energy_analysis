(function(root){
  'use strict';
  function measure(v){
    if(typeof v !== 'number' || !Number.isFinite(v) || v < 0) throw new Error('Invalid analytical measure');
    return v;
  }
  function day(value){
    if(typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value) || Number(value.slice(0,4))<1) throw new Error('Invalid analytical date');
    var stamp = Date.parse(value+'T00:00:00Z');
    if(!Number.isFinite(stamp) || new Date(stamp).toISOString().slice(0,10) !== value) throw new Error('Invalid analytical date');
    return stamp / 86400000;
  }
  function sum(values){
    var result = 0, correction = 0;
    values.forEach(function(v){ measure(v); var next=result+v; correction += Math.abs(result)>=Math.abs(v) ? (result-next)+v : (v-next)+result; result=next; });
    if(!Number.isFinite(result+correction)) throw new Error('Analytical sum overflow');
    return result+correction;
  }
  function months(rows){
    var previous = null;
    return rows.map(function(r){
      day(r.ym+'-01');
      var ordinal=Number(r.ym.slice(0,4))*12+Number(r.ym.slice(5,7)), value=measure(r.tce);
      if(previous && ordinal<=previous.ordinal) throw new Error('Months must be sorted and unique');
      var delta=previous && ordinal===previous.ordinal+1 && previous.value>0 ? (value/previous.value-1)*100 : null;
      if(delta!==null && !Number.isFinite(delta)) throw new Error('Monthly change overflow');
      previous={ordinal:ordinal,value:value}; return delta;
    });
  }
  function average(days,values){
    if(days.length!==values.length) throw new Error('Mismatched daily series');
    var settings=root.ENERGY_ANALYSIS_CONTRACT.formulas.daily_energy_average;
    var stamps=days.map(day); values.forEach(measure);
    for(var i=1;i<stamps.length;i++) if(stamps[i]<=stamps[i-1]) throw new Error('Dates must be sorted and unique');
    return stamps.map(function(end){
      var eligible=values.filter(function(v,j){return stamps[j]>=end-settings.window_days+1 && stamps[j]<=end;});
      return sum(eligible)/eligible.length;
    });
  }
  function pareto(rows){
    var field=root.ENERGY_ANALYSIS_CONTRACT.formulas.energy_cost_pareto.field;
    rows.forEach(function(r){measure(r[field]);});
    var sorted=rows.map(function(r,i){return {row:r,index:i};}).sort(function(a,b){return b.row[field]-a.row[field] || a.index-b.index;}).map(function(r){return r.row;});
    var total=sum(sorted.map(function(r){return r[field];})), cumulative=0, threshold=null;
    var shares=[],accumulated=[];
    sorted.forEach(function(r,i){var share=total ? r[field]/total*100 : 0; cumulative+=share; shares.push(share); accumulated.push(Math.min(100,cumulative));
      if(threshold===null && total && cumulative>=root.ENERGY_ANALYSIS_CONTRACT.formulas.energy_cost_pareto.threshold_percent-1e-10) threshold=i;});
    return {rows:sorted,total:total,shares:shares,cumulative:accumulated,threshold_index:threshold};
  }
  root.EnergyAnalysis={months:months,average:average,pareto:pareto};
})(window);
