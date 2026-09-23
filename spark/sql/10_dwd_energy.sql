SET spark.sql.sources.partitionOverwriteMode=dynamic;
SET hive.exec.dynamic.partition=true;
SET hive.exec.dynamic.partition.mode=nonstrict;
-- 本地/单节点集群上，迟到分区与事实表的动态分区裁剪可能形成广播交换等待。
-- 关闭该查询的广播与 AQE，使用确定性的 shuffle join；数据扫描仍由 dt 等值条件裁剪。
SET spark.sql.adaptive.enabled=false;
SET spark.sql.autoBroadcastJoinThreshold=-1;

-- ODS.dt 是同步批次日期，record_date 才是业务日期；迟到修正可能影响任意历史日。
-- 清理本次临时分区，避免重跑或空增量批次读取上次残留数据。
ALTER TABLE energy_dwd.dwd_energy_consumption_merge_stage
DROP IF EXISTS PARTITION (run_dt='${biz_date}',batch_id='${biz_date}');

WITH incoming AS (
  SELECT sha2(concat_ws('|',cast(f.record_date AS string),f.workshop_code,f.energy_code),256) energy_detail_key,
         f.record_date,f.workshop_code,w.workshop_name,w.process_type,
         f.energy_code,e.energy_name,cast(f.consumption AS decimal(16,3)) consumption,
         f.unit,cast(f.unit_price AS decimal(12,4)) unit_price,
         cast(f.cost AS decimal(16,2)) cost,f.record_status,
         cast(f.avg_temperature AS decimal(6,2)) avg_temperature,
         f.is_production_day,c.is_weekend,c.is_holiday,c.year_month,
         round(cast(f.consumption AS decimal(16,3))*cast(e.std_coal_factor AS decimal(10,4)),3) std_coal_kgce,
         round(cast(f.consumption AS decimal(16,3))*cast(e.co2_factor AS decimal(10,4)),3) co2_kg,
         f.updated_at source_updated_at,
         current_timestamp() etl_time
  FROM energy_ods.ods_energy_consumption f
  JOIN energy_ods.ods_workshop w ON f.workshop_code=w.workshop_code AND w.dt='current'
  JOIN energy_ods.ods_energy_type e ON f.energy_code=e.energy_code AND e.dt='current'
  JOIN energy_ods.ods_calendar c ON f.record_date=c.calendar_date AND c.dt='current'
  WHERE ('${load_mode}'='full' OR f.dt='${biz_date}')
    AND cast(f.consumption AS decimal(16,3))>=0
    AND cast(f.unit_price AS decimal(12,4))>0
),
impacted AS (SELECT DISTINCT record_date FROM incoming),
candidates AS (
  SELECT d.energy_detail_key,d.record_date,d.workshop_code,d.workshop_name,d.process_type,
         d.energy_code,d.energy_name,d.consumption,d.unit,d.unit_price,d.cost,d.record_status,
         d.avg_temperature,d.is_production_day,d.is_weekend,d.is_holiday,d.year_month,
         d.std_coal_kgce,d.co2_kg,d.source_updated_at,d.etl_time,0 source_priority
  FROM energy_dwd.dwd_energy_consumption_detail d
  JOIN impacted i ON d.dt=cast(i.record_date AS string) AND d.record_date=i.record_date
  WHERE '${load_mode}' <> 'full'
  UNION ALL
  SELECT i.*,1 source_priority FROM incoming i
),
latest AS (
  SELECT x.*,row_number() OVER (
    PARTITION BY record_date,workshop_code,energy_code
    ORDER BY source_updated_at DESC,source_priority DESC
  ) version_rank
  FROM candidates x
)
INSERT OVERWRITE TABLE energy_dwd.dwd_energy_consumption_merge_stage
PARTITION (run_dt,batch_id)
SELECT energy_detail_key,record_date,workshop_code,workshop_name,process_type,
       energy_code,energy_name,consumption,unit,unit_price,cost,record_status,
       avg_temperature,is_production_day,is_weekend,is_holiday,year_month,
       std_coal_kgce,co2_kg,source_updated_at,etl_time,
       cast(record_date AS string) target_dt,cast('${biz_date}' AS string) run_dt,
       cast('${biz_date}' AS string) batch_id
FROM latest WHERE version_rank=1;

-- 工作表与目标表分离，只动态覆盖实际受影响的业务日期分区。
INSERT OVERWRITE TABLE energy_dwd.dwd_energy_consumption_detail PARTITION (dt)
SELECT energy_detail_key,record_date,workshop_code,workshop_name,process_type,
       energy_code,energy_name,consumption,unit,unit_price,cost,record_status,
       avg_temperature,is_production_day,is_weekend,is_holiday,year_month,
       std_coal_kgce,co2_kg,source_updated_at,etl_time,target_dt
FROM energy_dwd.dwd_energy_consumption_merge_stage
WHERE batch_id='${biz_date}';

-- 产量是本批全量快照；只重建能耗本批所影响的业务日期，保证首次全量与迟到修正
-- 都能取得同日分母。若未来给产量增加 updated_at，可改为同样的版本合并策略。
INSERT OVERWRITE TABLE energy_dwd.dwd_production_detail PARTITION (dt)
SELECT sha2(concat_ws('|',cast(p.record_date AS string),p.workshop_code),256),
       p.record_date,p.workshop_code,w.workshop_name,w.process_type,
       cast(p.output_qty AS decimal(16,3)),p.output_unit,current_timestamp(),cast(p.record_date AS string)
FROM energy_ods.ods_production p JOIN energy_ods.ods_workshop w
  ON p.workshop_code=w.workshop_code AND w.dt='current'
JOIN (SELECT DISTINCT target_dt FROM energy_dwd.dwd_energy_consumption_merge_stage
      WHERE batch_id='${biz_date}') i ON cast(p.record_date AS string)=i.target_dt
WHERE p.dt='current' AND cast(p.output_qty AS decimal(16,3))>=0;
