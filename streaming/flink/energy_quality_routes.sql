-- Optional quarantine routes for records outside the normal aggregate path.
-- Each route uses a distinct consumer group so independent Flink jobs do not
-- compete for the same Kafka offsets. DELETE here means an explicit CDC event;
-- it does not capture a physical MySQL delete unless a CDC producer emits it.
SET 'execution.runtime-mode' = 'streaming';
SET 'parallelism.default' = '1';
SET 'execution.checkpointing.interval' = '2s';
SET 'execution.checkpointing.mode' = 'EXACTLY_ONCE';
SET 'execution.attached' = 'false';

CREATE TABLE late_energy_events (
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
    record_status STRING,
    is_production_day INT,
    WATERMARK FOR event_time AS event_time - INTERVAL '5' SECOND
) WITH (
    'connector' = 'kafka',
    'topic' = 'energy-events',
    'properties.bootstrap.servers' = 'kafka:9092',
    'properties.group.id' = 'industrial-energy-late-v1',
    'scan.startup.mode' = 'earliest-offset',
    'format' = 'json',
    'json.ignore-parse-errors' = 'true',
    'json.timestamp-format.standard' = 'ISO-8601'
);

CREATE TABLE energy_late_events (
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
    record_status STRING,
    is_production_day INT,
    observed_watermark TIMESTAMP_LTZ(3)
) WITH (
    'connector' = 'kafka',
    'topic' = 'energy-late-events',
    'properties.bootstrap.servers' = 'kafka:9092',
    'format' = 'json',
    'json.timestamp-format.standard' = 'ISO-8601',
    'sink.delivery-guarantee' = 'exactly-once',
    'sink.transactional-id-prefix' = 'industrial-energy-late-v1-'
);

INSERT INTO energy_late_events
SELECT event_id, event_time, updated_at, op, record_date, workshop_code,
       energy_code, consumption, unit, unit_price, cost, record_status,
       is_production_day, CURRENT_WATERMARK(event_time)
FROM late_energy_events
WHERE CURRENT_WATERMARK(event_time) IS NOT NULL
  AND event_time <= CURRENT_WATERMARK(event_time);

CREATE TABLE delete_energy_events (
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
    record_status STRING,
    is_production_day INT,
    WATERMARK FOR event_time AS event_time - INTERVAL '5' SECOND
) WITH (
    'connector' = 'kafka',
    'topic' = 'energy-events',
    'properties.bootstrap.servers' = 'kafka:9092',
    'properties.group.id' = 'industrial-energy-delete-v1',
    'scan.startup.mode' = 'earliest-offset',
    'format' = 'json',
    'json.ignore-parse-errors' = 'true',
    'json.timestamp-format.standard' = 'ISO-8601'
);

CREATE TABLE energy_delete_events (
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
    record_status STRING,
    is_production_day INT
) WITH (
    'connector' = 'kafka',
    'topic' = 'energy-delete-events',
    'properties.bootstrap.servers' = 'kafka:9092',
    'format' = 'json',
    'json.timestamp-format.standard' = 'ISO-8601',
    'sink.delivery-guarantee' = 'exactly-once',
    'sink.transactional-id-prefix' = 'industrial-energy-delete-v1-'
);

INSERT INTO energy_delete_events
SELECT event_id, event_time, updated_at, op, record_date, workshop_code,
       energy_code, consumption, unit, unit_price, cost, record_status,
       is_production_day
FROM delete_energy_events
WHERE op = 'DELETE';

CREATE TABLE invalid_energy_events (
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
    record_status STRING,
    is_production_day INT,
    quality_error STRING,
    WATERMARK FOR event_time AS event_time - INTERVAL '5' SECOND
) WITH (
    'connector' = 'kafka',
    'topic' = 'energy-events',
    'properties.bootstrap.servers' = 'kafka:9092',
    'properties.group.id' = 'industrial-energy-invalid-v1',
    'scan.startup.mode' = 'earliest-offset',
    'format' = 'json',
    'json.ignore-parse-errors' = 'true',
    'json.timestamp-format.standard' = 'ISO-8601'
);

CREATE TABLE energy_invalid_events (
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
    record_status STRING,
    is_production_day INT,
    quality_error STRING
) WITH (
    'connector' = 'kafka',
    'topic' = 'energy-invalid-events',
    'properties.bootstrap.servers' = 'kafka:9092',
    'format' = 'json',
    'json.timestamp-format.standard' = 'ISO-8601',
    'sink.delivery-guarantee' = 'exactly-once',
    'sink.transactional-id-prefix' = 'industrial-energy-invalid-v1-'
);

INSERT INTO energy_invalid_events
SELECT event_id, event_time, updated_at, op, record_date, workshop_code,
       energy_code, consumption, unit, unit_price, cost, record_status,
       is_production_day,
       CASE
         WHEN op NOT IN ('UPSERT', 'DELETE') OR op IS NULL THEN 'invalid_op'
         WHEN op = 'UPSERT' AND (consumption IS NULL OR consumption < 0) THEN 'invalid_consumption'
         WHEN op = 'UPSERT' AND (unit_price IS NULL OR unit_price <= 0) THEN 'invalid_unit_price'
         WHEN op = 'UPSERT' AND (cost IS NULL OR cost < 0) THEN 'invalid_cost'
         WHEN workshop_code IS NULL OR energy_code IS NULL THEN 'missing_business_key'
         WHEN event_id IS NULL OR updated_at IS NULL THEN 'missing_event_metadata'
       END
FROM invalid_energy_events
WHERE op NOT IN ('UPSERT', 'DELETE') OR op IS NULL
   OR (op = 'UPSERT' AND (consumption IS NULL OR consumption < 0))
   OR (op = 'UPSERT' AND (unit_price IS NULL OR unit_price <= 0))
   OR (op = 'UPSERT' AND (cost IS NULL OR cost < 0))
   OR workshop_code IS NULL OR energy_code IS NULL
   OR event_id IS NULL OR updated_at IS NULL;
