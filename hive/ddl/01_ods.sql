-- ODS 保留 MySQL 源字段，不改业务含义；事实按同步批次分区，快照维表用 current。
CREATE EXTERNAL TABLE IF NOT EXISTS energy_ods.ods_energy_consumption (
  id BIGINT, record_date DATE, workshop_code STRING, energy_code STRING,
  consumption STRING, unit STRING, unit_price STRING,
  cost STRING, record_status STRING, avg_temperature STRING,
  data_source STRING, is_production_day TINYINT, updated_at TIMESTAMP,
  is_deleted TINYINT
) PARTITIONED BY (dt STRING)
STORED AS ORC
LOCATION '/warehouse/energy_ods/ods_energy_consumption'
TBLPROPERTIES ('orc.compress'='SNAPPY');

CREATE EXTERNAL TABLE IF NOT EXISTS energy_ods.ods_production (
  record_date DATE, workshop_code STRING, output_qty STRING, output_unit STRING
) PARTITIONED BY (dt STRING) STORED AS ORC
LOCATION '/warehouse/energy_ods/ods_production';
CREATE EXTERNAL TABLE IF NOT EXISTS energy_ods.ods_workshop (
  workshop_code STRING, workshop_name STRING, process_type STRING,
  is_continuous TINYINT, output_unit STRING, area_m2 STRING, manager STRING
) PARTITIONED BY (dt STRING) STORED AS ORC
LOCATION '/warehouse/energy_ods/ods_workshop';

CREATE EXTERNAL TABLE IF NOT EXISTS energy_ods.ods_energy_type (
  energy_code STRING, energy_name STRING, unit STRING,
  std_coal_factor STRING, co2_factor STRING, reference_price STRING
) PARTITIONED BY (dt STRING) STORED AS ORC
LOCATION '/warehouse/energy_ods/ods_energy_type';

CREATE EXTERNAL TABLE IF NOT EXISTS energy_ods.ods_calendar (
  calendar_date DATE, year SMALLINT, quarter TINYINT, month TINYINT, year_month STRING,
  day_of_week TINYINT, weekday_name STRING, is_weekend TINYINT,
  holiday_name STRING, is_holiday TINYINT
) PARTITIONED BY (dt STRING) STORED AS ORC
LOCATION '/warehouse/energy_ods/ods_calendar';
