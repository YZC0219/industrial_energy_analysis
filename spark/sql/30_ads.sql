SET spark.sql.sources.partitionOverwriteMode=dynamic;
SET hive.exec.dynamic.partition=true;
SET hive.exec.dynamic.partition.mode=nonstrict;

INSERT OVERWRITE TABLE energy_ads.ads_factory_energy_day PARTITION(dt)
SELECT d.record_date,round(sum(d.tce),4),round(sum(d.co2_t),3),round(sum(d.cost_yuan),2),
       count(DISTINCT d.workshop_code),
       sum(CASE WHEN d.unit_energy_kgce IS NULL OR d.unit_energy_kgce<0 THEN 1 ELSE 0 END),
       current_timestamp(),cast(d.record_date AS string)
FROM energy_dws.dws_workshop_energy_day d
WHERE '${load_mode}'='full'
   OR d.dt IN (SELECT DISTINCT target_dt FROM energy_dwd.dwd_energy_consumption_merge_stage
               WHERE batch_id='${biz_date}')
GROUP BY d.record_date;
