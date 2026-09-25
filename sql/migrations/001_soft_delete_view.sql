CREATE OR REPLACE VIEW v_energy_enriched AS
SELECT
    f.record_date,
    c.year,
    c.quarter,
    c.month,
    c.`year_month`,
    c.is_weekend,
    c.is_holiday,
    c.holiday_name,
    f.workshop_code,
    w.workshop_name,
    w.process_type,
    w.is_continuous,
    f.energy_code,
    e.energy_name,
    e.reference_price,
    f.consumption,
    f.unit,
    f.unit_price,
    f.cost,
    f.record_status,
    f.is_production_day,
    f.avg_temperature,
    ROUND(f.consumption * e.std_coal_factor, 3) AS std_coal_kgce,
    ROUND(f.consumption * e.co2_factor, 3) AS co2_kg
FROM fact_energy_consumption f
JOIN dim_workshop w ON w.workshop_code = f.workshop_code
JOIN dim_energy_type e ON e.energy_code = f.energy_code
JOIN dim_calendar c ON c.calendar_date = f.record_date
WHERE f.is_deleted = 0;
