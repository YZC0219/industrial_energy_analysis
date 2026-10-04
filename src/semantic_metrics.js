/* Offline metric interpreter: formulas and field bindings come from the registry. */
(function(root){
  "use strict";
  function sum(rows, field){
    var total = 0, correction = 0;
    rows.forEach(function(row){
      var value = row[field];
      if (typeof value !== "number" || !Number.isFinite(value) || value < 0)
        throw new Error("Invalid semantic value: " + field);
      // Compensated summation limits accumulated rounding drift on daily rows.
      var next = total + value;
      correction += Math.abs(total) >= Math.abs(value) ? (total-next)+value : (value-next)+total;
      total = next;
    });
    return total + correction;
  }
  function evaluate(contract, rows, metadata){
    var result = {};
    contract.metrics.forEach(function(metric){
      var selected = rows.filter(function(row){
        var meta = metadata[row.code];
        if (!meta) throw new Error("Missing workshop metadata");
        return !metric.exclude_process || meta.proc !== metric.exclude_process;
      });
      if (metric.aggregation !== "sum" && metric.aggregation !== "ratio_of_sums")
        throw new Error("Unsupported semantic aggregation");
      var status = selected.length ? "ok" : "empty", value = null;
      if (selected.length && metric.scope === "single_workshop_only" &&
          new Set(selected.map(function(r){ return r.code; })).size > 1) status = "unsupported_scope";
      if (status === "ok"){
        value = sum(selected, metric.field);
        if (metric.aggregation === "ratio_of_sums"){
          var denominator = sum(selected, metric.denominator_field);
          value = denominator ? value * metric.scale / denominator : null;
          if (!denominator) status = "zero_denominator";
        }
      }
      result[metric.id] = {value:value, status:status, row_count:selected.length, unit:metric.unit};
    });
    return result;
  }
  function share(value, total){ return total > 0 ? value / total * 100 : 0; }
  root.EnergySemantic = {evaluate:evaluate, share:share};
})(window);
