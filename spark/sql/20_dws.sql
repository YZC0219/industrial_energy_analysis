SET spark.sql.sources.partitionOverwriteMode=dynamic;
SET hive.exec.dynamic.partition=true;
SET hive.exec.dynamic.partition.mode=nonstrict;

-- 由本同步批次实际影响的业务日期驱动重算，而不是假定业务日期等于同步日期。
INSERT OVERWRITE TABLE energy_dws.dws_workshop_energy_day PARTITION(dt)
SELECT e.record_date,e.workshop_code,max(e.workshop_name),max(e.process_type),max(e.year_month),
       round(sum(e.std_coal_kgce)/1000,4),round(sum(e.co2_kg)/1000,3),round(sum(e.cost),2),
       max(p.output_qty),max(p.output_unit),
       CASE WHEN max(p.output_qty)>0 THEN round(sum(e.std_coal_kgce)/max(p.output_qty),5) END,
       max(CASE WHEN e.is_production_day=0 THEN 1 ELSE 0 END),avg(e.avg_temperature),
       current_timestamp(),cast(e.record_date AS string)
FROM energy_dwd.dwd_energy_consumption_detail e
JOIN (SELECT DISTINCT target_dt FROM energy_dwd.dwd_energy_consumption_merge_stage
      WHERE run_dt='${biz_date}') i ON e.dt=i.target_dt
LEFT JOIN energy_dwd.dwd_production_detail p
 ON e.record_date=p.record_date AND e.workshop_code=p.workshop_code AND p.dt=e.dt
GROUP BY e.record_date,e.workshop_code;

-- 一个迟到修正可能属于历史月份，因此从受影响日期推导月份并动态覆盖。
INSERT OVERWRITE TABLE energy_dws.dws_workshop_energy_month PARTITION(year_month)
SELECT d.workshop_code,max(d.workshop_name),max(d.process_type),max(d.output_unit),
       round(sum(d.tce),4),round(sum(d.co2_t),3),round(sum(d.cost_yuan),2),round(sum(d.output_qty),3),
       CASE WHEN sum(d.output_qty)>0 THEN round(sum(d.tce)*1000/sum(d.output_qty),5) END,
       CASE WHEN sum(d.output_qty)>0 THEN round(sum(d.cost_yuan)/sum(d.output_qty),4) END,
       current_timestamp(),d.year_month
FROM energy_dws.dws_workshop_energy_day d
JOIN (SELECT DISTINCT substr(target_dt,1,7) year_month
      FROM energy_dwd.dwd_energy_consumption_merge_stage WHERE run_dt='${biz_date}') i
  ON d.year_month=i.year_month
GROUP BY d.year_month,d.workshop_code;
