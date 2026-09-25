SET spark.sql.sources.partitionOverwriteMode=dynamic;
SET hive.exec.dynamic.partition=true;
SET hive.exec.dynamic.partition.mode=nonstrict;

-- 由本同步批次实际影响的业务日期驱动重算，而不是假定业务日期等于同步日期。
INSERT OVERWRITE TABLE energy_dws.dws_workshop_energy_day PARTITION(dt)
SELECT e.record_date,e.workshop_code,max(e.workshop_name),max(e.process_type),max(e.year_month),
       round(sum(CASE WHEN coalesce(e.is_deleted,0)=0 THEN e.std_coal_kgce ELSE 0 END)/1000,4),
       round(sum(CASE WHEN coalesce(e.is_deleted,0)=0 THEN e.co2_kg ELSE 0 END)/1000,3),
       round(sum(CASE WHEN coalesce(e.is_deleted,0)=0 THEN e.cost ELSE 0 END),2),
       max(p.output_qty),max(p.output_unit),
       CASE WHEN max(p.output_qty)>0 THEN round(sum(CASE WHEN coalesce(e.is_deleted,0)=0 THEN e.std_coal_kgce ELSE 0 END)/max(p.output_qty),5) END,
       max(CASE WHEN coalesce(e.is_deleted,0)=0 AND e.is_production_day=0 THEN 1 ELSE 0 END),
       avg(CASE WHEN coalesce(e.is_deleted,0)=0 THEN e.avg_temperature END),
       current_timestamp(),cast(e.record_date AS string)
FROM energy_dwd.dwd_energy_consumption_detail e
JOIN (SELECT DISTINCT target_dt FROM energy_dwd.dwd_energy_consumption_merge_stage
      WHERE batch_id='${biz_date}') i ON e.dt=i.target_dt
LEFT JOIN energy_dwd.dwd_production_detail p
 ON e.record_date=p.record_date AND e.workshop_code=p.workshop_code AND p.dt=e.dt
GROUP BY e.record_date,e.workshop_code;

-- 日汇总覆盖后，收集受影响月份；月分区重算时必须扫描这些月份的全部天，
-- 不能只汇总本批涉及的几天，否则 INSERT OVERWRITE 会用局部和替换整月结果。
INSERT OVERWRITE TABLE energy_dws.dws_workshop_energy_month PARTITION(year_month)
SELECT d.workshop_code,max(d.workshop_name),max(d.process_type),max(d.output_unit),
       round(sum(d.tce),4),round(sum(d.co2_t),3),round(sum(d.cost_yuan),2),round(sum(d.output_qty),3),
       CASE WHEN sum(d.output_qty)>0 THEN round(sum(d.tce)*1000/sum(d.output_qty),5) END,
       CASE WHEN sum(d.output_qty)>0 THEN round(sum(d.cost_yuan)/sum(d.output_qty),4) END,
       current_timestamp(),d.year_month
FROM energy_dws.dws_workshop_energy_day d
WHERE '${load_mode}'='full'
   OR d.year_month IN (
      SELECT DISTINCT substr(target_dt,1,7) year_month
      FROM energy_dwd.dwd_energy_consumption_merge_stage WHERE batch_id='${biz_date}')
GROUP BY d.year_month,d.workshop_code;
