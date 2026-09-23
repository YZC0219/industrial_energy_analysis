-- ODS 保留 MySQL 源字段，不改业务含义；事实按同步批次分区，快照维表用 current。
CREATE EXTERNAL TABLE IF NOT EXISTS energy_ods.ods_energy_consumption (
  id BIGINT, record_date DATE, workshop_code STRING, energy_code STRING,
  consumption DECIMAL(16,3), unit STRING, unit_price DECIMAL(12,4),
  cost DECIMAL(16,2), record_status STRING, avg_temperature DECIMAL(6,2),
  data_source STRING, is_production_day TINYINT, updated_at TIMESTAMP
) PARTITIONED BY (dt STRING)
STORED AS PARQUET TBLPROPERTIES ('parquet.compression'='snappy');

CREATE EXTERNAL TABLE IF NOT EXISTS energy_ods.ods_production (
  record_date DATE, workshop_code STRING, output_qty DECIMAL(16,3), output_unit STRING
) PARTITIONED BY (dt STRING) STORED AS PARQUET;
CREATE EXTERNAL TABLE IF NOT EXISTS energy_ods.ods_workshop (
  workshop_code STRING, workshop_name STRING, process_type STRING,
  is_continuous TINYINT, output_unit STRING, area_m2 DECIMAL(10,1), manager STRING
) PARTITIONED BY (dt STRING) STORED AS PARQUET;

CREATE EXTERNAL TABLE IF NOT EXISTS energy_ods.ods_energy_type (
  energy_code STRING, energy_name STRING, unit STRING,
  std_coal_factor DECIMAL(10,4), co2_factor DECIMAL(10,4), reference_price DECIMAL(10,4)
) PARTITIONED BY (dt STRING) STORED AS PARQUET;

CREATE EXTERNAL TABLE IF NOT EXISTS energy_ods.ods_calendar (
  calendar_date DATE, year SMALLINT, quarter TINYINT, month TINYINT, year_month STRING,
  day_of_week TINYINT, weekday_name STRING, is_weekend TINYINT,
  holiday_name STRING, is_holiday TINYINT
) PARTITIONED BY (dt STRING) STORED AS PARQUET;
