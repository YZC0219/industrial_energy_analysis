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
CREATE TEMPORARY SYSTEM FUNCTION valid_iso_datetime AS 'industrial.energy.streaming.ValidIsoDateTime';
CREATE TEMPORARY SYSTEM FUNCTION valid_iso_date AS 'industrial.energy.streaming.ValidIsoDate';

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
       quality_error,
       raw_base64(payload)
FROM (
    SELECT payload, kafka_partition, kafka_offset,
           CASE
             WHEN NOT utf8_ok THEN 'invalid_utf8'
             WHEN NOT (json_text IS JSON OBJECT) THEN 'invalid_json'
             WHEN schema_version IS NULL OR schema_version <> 1 THEN 'unsupported_schema_version'
             WHEN op = 'UPSERT' AND (consumption_value IS NULL
                  OR unit_price_value IS NULL OR cost_value IS NULL) THEN 'invalid_numeric_field'
             WHEN NOT valid_iso_datetime(JSON_VALUE(json_text, '$.event_time')) THEN 'invalid_event_time'
             WHEN NOT valid_iso_datetime(JSON_VALUE(json_text, '$.updated_at')) THEN 'invalid_updated_at'
             WHEN NOT valid_iso_date(JSON_VALUE(json_text, '$.record_date')) THEN 'invalid_record_date'
           END AS quality_error
    FROM (
        SELECT payload, kafka_partition, kafka_offset, utf8_ok, json_text,
               TRY_CAST(JSON_VALUE(json_text, '$.schema_version') AS INT) AS schema_version,
               JSON_VALUE(json_text, '$.op') AS op,
               TRY_CAST(JSON_VALUE(json_text, '$.consumption') AS DECIMAL(18, 3)) AS consumption_value,
               TRY_CAST(JSON_VALUE(json_text, '$.unit_price') AS DECIMAL(18, 4)) AS unit_price_value,
               TRY_CAST(JSON_VALUE(json_text, '$.cost') AS DECIMAL(18, 2)) AS cost_value
        FROM (
            SELECT payload, kafka_partition, kafka_offset,
                   strict_utf8(payload) AS utf8_ok,
                   TRY_CAST(payload AS STRING) AS json_text
            FROM raw_energy_events
        )
    )
)
WHERE quality_error IS NOT NULL;
