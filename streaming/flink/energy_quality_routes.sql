-- Optional quarantine routes for records outside the normal aggregate path.
-- Each route uses a distinct consumer group so independent Flink jobs do not
-- compete for the same Kafka offsets. DELETE here means an explicit CDC event;
-- it does not capture a physical MySQL delete unless a CDC producer emits it.
SET 'execution.runtime-mode' = 'streaming';
SET 'parallelism.default' = '1';
SET 'execution.checkpointing.interval' = '2s';
SET 'execution.checkpointing.mode' = 'EXACTLY_ONCE';
SET 'execution.attached' = 'false';

CREATE TEMPORARY SYSTEM FUNCTION iso_epoch_millis AS 'industrial.energy.streaming.IsoEpochMillis';
CREATE TEMPORARY SYSTEM FUNCTION valid_iso_date AS 'industrial.energy.streaming.ValidIsoDate';

CREATE TABLE late_energy_events (
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
    record_status STRING,
    is_production_day INT,
    event_time_safe AS TO_TIMESTAMP_LTZ(
        COALESCE(iso_epoch_millis(event_time), CAST(0 AS BIGINT)), 3),
    WATERMARK FOR event_time_safe AS event_time_safe - INTERVAL '5' SECOND
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
SELECT event_id, event_time_safe, TO_TIMESTAMP_LTZ(iso_epoch_millis(updated_at), 3),
       op, TRY_CAST(record_date AS DATE), workshop_code,
       energy_code, consumption, unit, unit_price, cost, record_status,
       is_production_day, CURRENT_WATERMARK(event_time_safe)
FROM late_energy_events
WHERE CURRENT_WATERMARK(event_time_safe) IS NOT NULL
  AND iso_epoch_millis(event_time) IS NOT NULL
  AND iso_epoch_millis(updated_at) IS NOT NULL
  AND valid_iso_date(record_date)
  AND REGEXP(workshop_code, '^W[0-9]{2}$')
  AND REGEXP(energy_code, '^E[0-9]{2}$')
  AND event_time_safe <= CURRENT_WATERMARK(event_time_safe)
  AND schema_version = 1;

CREATE TABLE delete_energy_events (
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
    record_status STRING,
    is_production_day INT
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
SELECT event_id, TO_TIMESTAMP_LTZ(iso_epoch_millis(event_time), 3),
       TO_TIMESTAMP_LTZ(iso_epoch_millis(updated_at), 3),
       op, TRY_CAST(record_date AS DATE), workshop_code,
       energy_code, consumption, unit, unit_price, cost, record_status,
       is_production_day
FROM delete_energy_events
WHERE op = 'DELETE' AND schema_version = 1
  AND iso_epoch_millis(event_time) IS NOT NULL
  AND iso_epoch_millis(updated_at) IS NOT NULL
  AND valid_iso_date(record_date)
  AND REGEXP(workshop_code, '^W[0-9]{2}$')
  AND REGEXP(energy_code, '^E[0-9]{2}$');

CREATE TABLE invalid_energy_events (
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
    record_status STRING,
    is_production_day INT,
    quality_error STRING
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
         WHEN schema_version IS NULL OR schema_version <> 1 THEN 'unsupported_schema_version'
         WHEN op NOT IN ('UPSERT', 'DELETE') OR op IS NULL THEN 'invalid_op'
         WHEN op = 'UPSERT' AND (consumption IS NULL OR consumption < 0) THEN 'invalid_consumption'
         WHEN op = 'UPSERT' AND (unit_price IS NULL OR unit_price <= 0) THEN 'invalid_unit_price'
         WHEN op = 'UPSERT' AND (cost IS NULL OR cost < 0) THEN 'invalid_cost'
         WHEN op = 'UPSERT' AND (unit IS NULL OR TRIM(unit) = '') THEN 'invalid_unit'
         WHEN record_date IS NULL THEN 'missing_business_date'
         WHEN NOT COALESCE(REGEXP(workshop_code, '^W[0-9]{2}$'), FALSE)
           OR NOT COALESCE(REGEXP(energy_code, '^E[0-9]{2}$'), FALSE)
           THEN 'invalid_business_key'
         WHEN event_id IS NULL OR event_time IS NULL OR updated_at IS NULL THEN 'missing_event_metadata'
       END
FROM invalid_energy_events
WHERE schema_version IS NULL OR schema_version <> 1
   OR op NOT IN ('UPSERT', 'DELETE') OR op IS NULL
   OR (op = 'UPSERT' AND (consumption IS NULL OR consumption < 0))
   OR (op = 'UPSERT' AND (unit_price IS NULL OR unit_price <= 0))
   OR (op = 'UPSERT' AND (cost IS NULL OR cost < 0))
   OR (op = 'UPSERT' AND (unit IS NULL OR TRIM(unit) = ''))
   OR record_date IS NULL
   OR NOT COALESCE(REGEXP(workshop_code, '^W[0-9]{2}$'), FALSE)
   OR NOT COALESCE(REGEXP(energy_code, '^E[0-9]{2}$'), FALSE)
   OR event_id IS NULL OR event_time IS NULL OR updated_at IS NULL;
