-- Byte-preserving quarantine runs independently of the typed JSON jobs.
-- The typed readers use json.ignore-parse-errors so one malformed message
-- cannot restart their tasks; this raw reader retains its original value.
SET 'execution.runtime-mode' = 'streaming';
SET 'parallelism.default' = '1';
SET 'execution.checkpointing.interval' = '2s';
SET 'execution.checkpointing.mode' = 'EXACTLY_ONCE';
SET 'execution.attached' = 'false';

CREATE TEMPORARY SYSTEM FUNCTION strict_utf8 AS 'industrial.energy.streaming.StrictUtf8';
CREATE TEMPORARY SYSTEM FUNCTION raw_base64 AS 'industrial.energy.streaming.RawBase64';

CREATE TABLE raw_energy_events (
    payload BYTES,
    kafka_partition INT METADATA FROM 'partition' VIRTUAL,
    kafka_offset BIGINT METADATA FROM 'offset' VIRTUAL
) WITH (
    'connector' = 'kafka',
    'topic' = 'energy-events',
    'properties.bootstrap.servers' = 'kafka:9092',
    'properties.group.id' = 'industrial-energy-raw-quarantine-v1',
    'scan.startup.mode' = 'earliest-offset',
    'format' = 'raw'
);

CREATE TABLE energy_malformed_events (
    source_topic STRING,
    source_partition INT,
    source_offset BIGINT,
    quality_error STRING,
    payload_base64 STRING
) WITH (
    'connector' = 'kafka',
    'topic' = 'energy-malformed-events',
    'properties.bootstrap.servers' = 'kafka:9092',
    'format' = 'json',
    'sink.delivery-guarantee' = 'exactly-once',
    'sink.transactional-id-prefix' = 'industrial-energy-malformed-v1-'
);

INSERT INTO energy_malformed_events
SELECT 'energy-events', kafka_partition, kafka_offset,
       CASE WHEN NOT utf8_ok THEN 'invalid_utf8' ELSE 'invalid_json' END,
       raw_base64(payload)
FROM (
    SELECT payload, kafka_partition, kafka_offset,
           strict_utf8(payload) AS utf8_ok,
           TRY_CAST(payload AS STRING) AS json_text
    FROM raw_energy_events
)
WHERE NOT utf8_ok OR NOT (json_text IS JSON OBJECT);
