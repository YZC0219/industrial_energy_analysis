SET 'execution.runtime-mode' = 'streaming';
SET 'parallelism.default' = '1';
SET 'execution.checkpointing.interval' = '2s';
SET 'execution.checkpointing.mode' = 'EXACTLY_ONCE';
SET 'execution.attached' = 'false';

CREATE TABLE energy_events (
    schema_version INT,
    event_id STRING,
    event_time TIMESTAMP_LTZ(3),
    updated_at TIMESTAMP_LTZ(3),
    op STRING,
    record_date DATE,
    workshop_code STRING,
    energy_code STRING,
    consumption DECIMAL(18, 3),
    unit STRING,
    unit_price DECIMAL(18, 4),
    cost DECIMAL(18, 2),
    WATERMARK FOR event_time AS event_time - INTERVAL '5' SECOND
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
        TUMBLE(TABLE energy_events, DESCRIPTOR(event_time), INTERVAL '10' SECOND)
    )
    WHERE op = 'UPSERT' AND schema_version = 1
    GROUP BY workshop_code, window_start, window_end
)
WHERE total_cost >= 100.00;
