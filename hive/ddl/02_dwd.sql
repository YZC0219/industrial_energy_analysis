-- 粒度/逻辑主键：record_date × workshop_code × energy_code。
CREATE TABLE IF NOT EXISTS energy_dwd.dwd_energy_consumption_detail (
  energy_detail_key STRING, record_date DATE, workshop_code STRING, workshop_name STRING,
  process_type STRING, energy_code STRING, energy_name STRING, consumption DECIMAL(16,3),
  unit STRING, unit_price DECIMAL(12,4), cost DECIMAL(16,2), record_status STRING,
  avg_temperature DECIMAL(6,2), is_production_day TINYINT, is_weekend TINYINT,
  is_holiday TINYINT, year_month STRING, std_coal_kgce DECIMAL(20,3),
  co2_kg DECIMAL(20,3), source_updated_at TIMESTAMP, etl_time TIMESTAMP,
  is_deleted TINYINT
) PARTITIONED BY (dt STRING) STORED AS PARQUET
LOCATION '/warehouse/energy_dwd/dwd_energy_consumption_detail'
TBLPROPERTIES ('parquet.compression'='snappy');

-- 单批合并工作表：先把“受影响业务分区的旧快照 + 本批新版本”去重写入此表，
-- 再由它动态覆盖 DWD，避免 Spark 直接边读边覆盖同一张表。
CREATE TABLE IF NOT EXISTS energy_dwd.dwd_energy_consumption_merge_stage (
  energy_detail_key STRING, record_date DATE, workshop_code STRING, workshop_name STRING,
  process_type STRING, energy_code STRING, energy_name STRING, consumption DECIMAL(16,3),
  unit STRING, unit_price DECIMAL(12,4), cost DECIMAL(16,2), record_status STRING,
  avg_temperature DECIMAL(6,2), is_production_day TINYINT, is_weekend TINYINT,
  is_holiday TINYINT, year_month STRING, std_coal_kgce DECIMAL(20,3),
  co2_kg DECIMAL(20,3), source_updated_at TIMESTAMP, etl_time TIMESTAMP,
  is_deleted TINYINT,
  target_dt STRING
) PARTITIONED BY (run_dt STRING, batch_id STRING) STORED AS PARQUET
LOCATION '/warehouse/energy_dwd/dwd_energy_consumption_merge_stage';

-- 粒度/逻辑主键：record_date × workshop_code。
CREATE TABLE IF NOT EXISTS energy_dwd.dwd_production_detail (
  production_detail_key STRING, record_date DATE, workshop_code STRING,
  workshop_name STRING, process_type STRING, output_qty DECIMAL(16,3), output_unit STRING,
  etl_time TIMESTAMP
) PARTITIONED BY (dt STRING) STORED AS PARQUET
LOCATION '/warehouse/energy_dwd/dwd_production_detail';
