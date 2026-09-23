-- DWS 日粒度；逻辑主键：record_date × workshop_code。
CREATE TABLE IF NOT EXISTS energy_dws.dws_workshop_energy_day (
  record_date DATE, workshop_code STRING, workshop_name STRING, process_type STRING,
  year_month STRING, tce DECIMAL(20,4), co2_t DECIMAL(20,3), cost_yuan DECIMAL(20,2),
  output_qty DECIMAL(20,3), output_unit STRING, unit_energy_kgce DECIMAL(20,5),
  has_shutdown TINYINT, avg_temperature DECIMAL(8,2), etl_time TIMESTAMP
) PARTITIONED BY (dt STRING) STORED AS PARQUET;
-- DWS 月粒度；逻辑主键：year_month × workshop_code；按月份分区。
CREATE TABLE IF NOT EXISTS energy_dws.dws_workshop_energy_month (
  workshop_code STRING, workshop_name STRING, process_type STRING, output_unit STRING,
  tce DECIMAL(20,4), co2_t DECIMAL(20,3), cost_yuan DECIMAL(20,2), output_qty DECIMAL(20,3),
  unit_energy_kgce DECIMAL(20,5), unit_cost_yuan DECIMAL(20,4), etl_time TIMESTAMP
) PARTITIONED BY (year_month STRING) STORED AS PARQUET;

-- ADS 全厂日看板；逻辑主键：record_date。
CREATE TABLE IF NOT EXISTS energy_ads.ads_factory_energy_day (
  record_date DATE, tce DECIMAL(20,4), co2_t DECIMAL(20,3), cost_yuan DECIMAL(20,2),
  workshop_count BIGINT, alarm_workshop_count BIGINT, etl_time TIMESTAMP
) PARTITIONED BY (dt STRING) STORED AS PARQUET;
