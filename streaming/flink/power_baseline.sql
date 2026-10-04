-- Optional power telemetry topic; samples are kW, not cumulative meter readings.
-- Requires the project's Kafka connector jar and registered iso_epoch_millis UDF.
-- Run only after raw event validation. A malformed source row fails this job.
SET 'execution.runtime-mode' = 'streaming';
SET 'execution.checkpointing.interval' = '10s';
SET 'execution.attached' = 'false';
SET 'parallelism.default' = '1';
SET 'table.local-time-zone' = 'UTC';

CREATE TEMPORARY SYSTEM FUNCTION iso_epoch_millis AS 'industrial.energy.streaming.IsoEpochMillis';

CREATE TABLE power_samples (
    event_id STRING,
    workshop_code STRING,
    event_time STRING,
    power_kw DOUBLE,
    ts AS TO_TIMESTAMP_LTZ(iso_epoch_millis(event_time), 3),
    WATERMARK FOR ts AS ts - INTERVAL '30' SECOND
) WITH (
    'connector' = 'kafka', 'topic' = 'energy-power-samples',
    'properties.bootstrap.servers' = 'kafka:9092',
    'properties.group.id' = 'energy-power-baseline-poc',
    'scan.startup.mode' = 'earliest-offset', 'format' = 'json'
);

CREATE TABLE power_alerts (
    workshop_code STRING, window_start TIMESTAMP(3), window_end TIMESTAMP(3),
    mean_kw DOUBLE, slope_kw_per_minute DOUBLE, sample_count BIGINT
) WITH (
    'connector' = 'kafka', 'topic' = 'energy-power-alerts',
    'properties.bootstrap.servers' = 'kafka:9092', 'format' = 'json'
);

-- W01 baseline is illustrative. Replace with reviewed workshop/hour baselines.
-- Minimum count is not a completeness guarantee; upstream must deduplicate ids.
INSERT INTO power_alerts
SELECT workshop_code, window_start, window_end, AVG(power_kw),
       (COUNT(*) * SUM(x * power_kw) - SUM(x) * SUM(power_kw)) /
       NULLIF(COUNT(*) * SUM(x * x) - SUM(x) * SUM(x), 0), COUNT(*)
FROM (
    SELECT workshop_code, window_start, window_end, power_kw,
           CAST(TIMESTAMPDIFF(SECOND, window_start, CAST(ts AS TIMESTAMP(3))) AS DOUBLE) / 60.0 AS x
    FROM TABLE(HOP(TABLE power_samples, DESCRIPTOR(ts), INTERVAL '1' MINUTE, INTERVAL '15' MINUTE))
    WHERE workshop_code = 'W01' AND power_kw >= 0 AND event_id IS NOT NULL
)
GROUP BY workshop_code, window_start, window_end
HAVING COUNT(*) >= 15 AND AVG(power_kw) > 100.0 + 2 * 10.0;
