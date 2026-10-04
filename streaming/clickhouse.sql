-- Apply explicitly to an isolated ClickHouse database; not executed by local POC.
CREATE DATABASE IF NOT EXISTS energy_poc;
CREATE TABLE IF NOT EXISTS energy_poc.power_minutes (
    workshop_code String,
    minute DateTime64(3, 'UTC'),
    mean_kw Float64,
    sample_count UInt32
) ENGINE = ReplacingMergeTree
PARTITION BY toYYYYMM(minute)
ORDER BY (workshop_code, minute);
-- One-minute mean kW × 1/60 h approximates kWh only for complete regular samples.
-- Read with FINAL: background merges do not guarantee immediate deduplication.
-- Replaying an identical complete minute aggregate is safe for FINAL reads.
-- Overlapping partial aggregates or out-of-order corrections require a source version contract.
