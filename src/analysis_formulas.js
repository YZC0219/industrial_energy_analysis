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
  function finite(v){
    if(typeof v !== 'number' || !Number.isFinite(v)) throw new Error('Invalid signed analytical measure or overflow');
    return v;
  }
  function dispersion(mean,sigma){
    measure(mean); measure(sigma);
    var width=finite(root.ENERGY_ANALYSIS_CONTRACT.formulas.unit_energy_dispersion.sigma_multiplier*sigma);
    return {upper:finite(mean+width),lower:Math.max(0,mean-width),cv:mean ? finite(sigma/mean*100) : null};
  }
  function baselineDifference(quantity,baseline,current){
    measure(quantity); measure(baseline); measure(current);
    return finite(quantity*((baseline-current)/root.ENERGY_ANALYSIS_CONTRACT.formulas.historical_intensity_difference.kgce_per_tce));
  }
  function conversion(delta,totals){
    finite(delta); measure(totals.tce); measure(totals.co2); measure(totals.cost);
    if(!totals.tce) return {carbon_factor:null,cost_factor:null,co2:null,cost:null};
    var cf=finite(totals.co2/totals.tce),yf=finite(totals.cost/totals.tce);
    return {carbon_factor:cf,cost_factor:yf,co2:finite(delta*cf),cost:finite(delta*yf)};
  }
  function residualBand(mean,sigma){
    finite(mean); measure(sigma);
    var width=finite(root.ENERGY_ANALYSIS_CONTRACT.formulas.residual_control_band.sigma_multiplier*sigma);
    return {upper:finite(mean+width),lower:finite(mean-width),half_width:width};
  }
  function cusumSummary(points,threshold){
    measure(threshold);
    if(!threshold) throw new Error('CUSUM threshold must be positive');
    var previousDate=null,previousAlarm=false,alarms=0,high=0,low=0,segments=0,maxHi=0,maxLo=0,maxRaw=0;
    points.forEach(function(p){
      var stamp=day(p.d);
      if(previousDate!==null && stamp<=previousDate) throw new Error('CUSUM dates must be unique and sorted');
      if(typeof p.alarm!=='boolean' || !['','偏高','偏低'].includes(p.side) || p.alarm!==(p.side!==''))
        throw new Error('Invalid stored CUSUM classification');
      finite(p.z); [p.hi,p.lo,p.hi_raw,p.lo_raw].forEach(measure);
      alarms+=Number(p.alarm); high+=Number(p.side==='偏高'); low+=Number(p.side==='偏低');
      segments+=Number(p.alarm && !previousAlarm);
      maxHi=Math.max(maxHi,p.hi); maxLo=Math.max(maxLo,p.lo); maxRaw=Math.max(maxRaw,p.hi_raw,p.lo_raw);
      previousDate=stamp; previousAlarm=p.alarm;
    });
    return {threshold:threshold,observed_points:points.length,n_alarm:alarms,n_hi:high,n_lo:low,n_seg:segments,
      max_hi:maxHi,max_lo:maxLo,max_raw:maxRaw,side:low>high ? '偏低' : (high ? '偏高' : '无')};
  }
  function scenario(tce,cost,carbon,ratePercent){
    [tce,cost,carbon,ratePercent].forEach(measure);
    if(ratePercent>100) throw new Error('Scenario reduction must be between 0 and 100 percent');
    var rate=ratePercent/100;
    return {tce:finite(tce*rate),cost:finite(cost*rate),co2:finite(carbon*rate)};
  }
  function sumScenario(rows){
    return {tce:sum(rows.map(function(r){return r.tce;})),cost:sum(rows.map(function(r){return r.cost;})),
            co2:sum(rows.map(function(r){return r.co2;}))};
  }
  root.EnergyAnalysis={months:months,average:average,pareto:pareto,
                       dispersion:dispersion,baselineDifference:baselineDifference,conversion:conversion,
                       residualBand:residualBand,cusumSummary:cusumSummary,scenario:scenario,sumScenario:sumScenario};
})(window);
