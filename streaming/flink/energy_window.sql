SET 'execution.runtime-mode' = 'streaming';
SET 'parallelism.default' = '1';
SET 'execution.checkpointing.interval' = '2s';
SET 'execution.checkpointing.mode' = 'EXACTLY_ONCE';
SET 'execution.attached' = 'false';

CREATE TEMPORARY SYSTEM FUNCTION iso_epoch_millis AS 'industrial.energy.streaming.IsoEpochMillis';
CREATE TEMPORARY SYSTEM FUNCTION valid_iso_datetime AS 'industrial.energy.streaming.ValidIsoDateTime';
CREATE TEMPORARY SYSTEM FUNCTION valid_iso_date AS 'industrial.energy.streaming.ValidIsoDate';

CREATE TABLE energy_events (
    schema_version INT,
    event_id STRING,
    event_time STRING,
    updated_at STRING,
    op STRING,
    record_date STRING,
    workshop_code STRING,
    energy_code STRING,
    consumption DECIMAL(18, 3),
    unit STRING,
    unit_price DECIMAL(18, 4),
    cost DECIMAL(18, 2),
    event_time_safe AS TO_TIMESTAMP_LTZ(
        COALESCE(iso_epoch_millis(event_time), CAST(0 AS BIGINT)), 3),
    WATERMARK FOR event_time_safe AS event_time_safe - INTERVAL '5' SECOND
) WITH (
    'connector' = 'kafka',
    'topic' = 'energy-events',
    'properties.bootstrap.servers' = 'kafka:9092',
    'properties.group.id' = 'industrial-energy-window-v1',
    'scan.startup.mode' = 'earliest-offset',
    'format' = 'json',
    'json.ignore-parse-errors' = 'true',
    'json.timestamp-format.standard' = 'ISO-8601'
);

CREATE TABLE energy_alerts (
    workshop_code STRING,
    window_start TIMESTAMP_LTZ(3),
    window_end TIMESTAMP_LTZ(3),
    event_count BIGINT,
    total_cost DECIMAL(18, 2),
    alert_level STRING
) WITH (
    'connector' = 'kafka',
    'topic' = 'energy-alerts',
    'properties.bootstrap.servers' = 'kafka:9092',
    'properties.transaction.timeout.ms' = '60000',
    'format' = 'json',
    'json.timestamp-format.standard' = 'ISO-8601',
    'sink.delivery-guarantee' = 'exactly-once',
    'sink.transactional-id-prefix' = 'industrial-energy-alert-v1-'
);

INSERT INTO energy_alerts
SELECT workshop_code, window_start, window_end, event_count, total_cost, 'HIGH_COST'
FROM (
    SELECT workshop_code, window_start, window_end,
           COUNT(*) AS event_count,
           CAST(SUM(cost) AS DECIMAL(18, 2)) AS total_cost
    FROM TABLE(
        TUMBLE(TABLE energy_events, DESCRIPTOR(event_time_safe), INTERVAL '10' SECOND)
    )
    WHERE op = 'UPSERT' AND schema_version = 1
      AND event_id IS NOT NULL AND event_id <> ''
      AND iso_epoch_millis(event_time) IS NOT NULL
      AND valid_iso_datetime(updated_at) AND valid_iso_date(record_date)
      AND REGEXP(workshop_code, '^W[0-9]{2}$')
      AND REGEXP(energy_code, '^E[0-9]{2}$')
      AND consumption IS NOT NULL AND consumption >= 0
      AND unit IS NOT NULL AND TRIM(unit) <> ''
      AND unit_price IS NOT NULL AND unit_price > 0
      AND cost IS NOT NULL AND cost >= 0
    GROUP BY workshop_code, window_start, window_end
)
WHERE total_cost >= 100.00;
