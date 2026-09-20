-- =============================================================================
-- 工业能耗分析 —— 建库建表脚本
-- MySQL 8.0+ / InnoDB / utf8mb4
--
-- 执行: mysql -u root -p < sql/create_table.sql
--       或在 import_mysql.py 中自动执行 (--init)
--
-- 模型: 星型模型
--   维表  dim_workshop          车间/工序
--         dim_energy_type       能源品种(含折标煤系数、碳排放因子)
--         dim_calendar          日期维(周末/法定节假日)
--   事实  fact_energy_consumption  车间×日×能源品种 消耗明细
--         fact_production           车间×日 产量
-- =============================================================================

CREATE DATABASE IF NOT EXISTS industrial_energy
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_general_ci;

USE industrial_energy;

DROP VIEW  IF EXISTS v_monthly_workshop;
DROP VIEW  IF EXISTS v_daily_workshop;
DROP VIEW  IF EXISTS v_energy_enriched;
DROP TABLE IF EXISTS fact_energy_consumption;
DROP TABLE IF EXISTS fact_production;
DROP TABLE IF EXISTS dim_calendar;
DROP TABLE IF EXISTS dim_energy_type;
DROP TABLE IF EXISTS dim_workshop;


-- -----------------------------------------------------------------------------
-- 维表 1: 车间 / 工序
-- -----------------------------------------------------------------------------
CREATE TABLE dim_workshop (
    workshop_code  VARCHAR(8)   NOT NULL COMMENT '车间编码',
    workshop_name  VARCHAR(64)  NOT NULL COMMENT '车间名称',
    process_type   VARCHAR(32)  NOT NULL COMMENT '工序类型',
    is_continuous  TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '是否连续生产 1=是',
    output_unit    VARCHAR(16)  NOT NULL COMMENT '产量单位',
    area_m2        DECIMAL(10,1)        COMMENT '占地面积(m²)',
    manager        VARCHAR(32)          COMMENT '负责人',
    PRIMARY KEY (workshop_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='车间主数据';

INSERT INTO dim_workshop
    (workshop_code, workshop_name, process_type, is_continuous, output_unit, area_m2, manager)
VALUES
    ('W01', '熔炼车间',     '熔炼',     1, '吨',     4200.0, '张伟'),
    ('W02', '轧制车间',     '轧制',     1, '吨',     6800.0, '李强'),
    ('W03', '热处理车间',   '热处理',   1, '吨',     3100.0, '王芳'),
    ('W04', '机加工车间',   '机加工',   0, '件',     5400.0, '刘洋'),
    ('W05', '表面处理车间', '表面处理', 0, '平方米', 2600.0, '陈静'),
    ('W06', '装配车间',     '装配',     0, '台',     7200.0, '赵磊'),
    ('W07', '动力站',       '公用工程', 1, '吨蒸汽', 1800.0, '孙鹏'),
    ('W08', '包装车间',     '包装',     0, '件',     2200.0, '周敏');


-- -----------------------------------------------------------------------------
-- 维表 2: 能源品种
--   std_coal_factor 折标煤系数 kgce/单位  —— GB/T 2589-2020《综合能耗计算通则》当量值
--   co2_factor      碳排放因子 kgCO2/单位 —— 电力取生态环境部全国电网平均排放因子;
--                                            燃料取 IPCC 缺省值; 压缩空气为二次能源, 不重复计入
-- -----------------------------------------------------------------------------
CREATE TABLE dim_energy_type (
    energy_code      VARCHAR(8)   NOT NULL COMMENT '能源编码',
    energy_name      VARCHAR(32)  NOT NULL COMMENT '能源名称',
    unit             VARCHAR(16)  NOT NULL COMMENT '计量单位',
    std_coal_factor  DECIMAL(10,4) NOT NULL COMMENT '折标煤系数 kgce/单位',
    co2_factor       DECIMAL(10,4) NOT NULL COMMENT '碳排放因子 kgCO2/单位',
    reference_price  DECIMAL(10,4) NOT NULL COMMENT '参考单价 元/单位',
    PRIMARY KEY (energy_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='能源品种主数据';

INSERT INTO dim_energy_type
    (energy_code, energy_name, unit, std_coal_factor, co2_factor, reference_price)
VALUES
    ('E01', '电力',     'kWh',  0.1229,   0.5703,   0.6800),
    ('E02', '天然气',   'm³',   1.3300,   2.1622,   3.4500),
    ('E03', '蒸汽',     't',   95.7000, 260.0000, 220.0000),
    ('E04', '工业水',   'm³',   0.2429,   0.3440,   4.1000),
    ('E05', '压缩空气', 'm³',   0.0400,   0.0000,   0.2800),
    ('E06', '柴油',     'kg',   1.4571,   3.0959,   7.8500);


-- -----------------------------------------------------------------------------
-- 维表 3: 日期维 (数据由 import_mysql.py 从 output/dim_calendar.csv 装载)
-- -----------------------------------------------------------------------------
CREATE TABLE dim_calendar (
    calendar_date  DATE         NOT NULL COMMENT '日期',
    year           SMALLINT     NOT NULL COMMENT '年',
    quarter        TINYINT      NOT NULL COMMENT '季度',
    month          TINYINT      NOT NULL COMMENT '月',
    `year_month`     CHAR(7)      NOT NULL COMMENT '年月 YYYY-MM',
    day_of_week    TINYINT      NOT NULL COMMENT '星期 1=周一',
    weekday_name   VARCHAR(16)  NOT NULL COMMENT '星期名称',
    is_weekend     TINYINT(1)   NOT NULL COMMENT '是否周末',
    holiday_name   VARCHAR(32)           COMMENT '节假日名称',
    is_holiday     TINYINT(1)   NOT NULL COMMENT '是否法定节假日',
    PRIMARY KEY (calendar_date),
    KEY idx_cal_ym (`year_month`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='日期维表';


-- -----------------------------------------------------------------------------
-- 事实表 1: 能源消耗明细 (车间 × 日 × 能源品种)
-- -----------------------------------------------------------------------------
CREATE TABLE fact_energy_consumption (
    id                BIGINT       NOT NULL AUTO_INCREMENT,
    record_date       DATE         NOT NULL COMMENT '记录日期',
    workshop_code     VARCHAR(8)   NOT NULL COMMENT '车间编码',
    energy_code       VARCHAR(8)   NOT NULL COMMENT '能源编码',
    consumption       DECIMAL(16,3) NOT NULL COMMENT '消耗量',
    unit              VARCHAR(16)  NOT NULL COMMENT '计量单位',
    unit_price        DECIMAL(12,4) NOT NULL COMMENT '单价 元/单位',
    cost              DECIMAL(16,2) NOT NULL COMMENT '费用 元',
    record_status     VARCHAR(16)  NOT NULL COMMENT '生产状态: 正常/低负荷/停产',
    avg_temperature   DECIMAL(6,2)          COMMENT '日均气温(℃)',
    data_source       VARCHAR(16)           COMMENT '数据来源',
    is_production_day TINYINT(1)   NOT NULL DEFAULT 1 COMMENT '是否生产日',
    updated_at        DATETIME     NULL COMMENT '源系统最后修改时刻',
    PRIMARY KEY (id),
    UNIQUE KEY uk_date_ws_energy (record_date, workshop_code, energy_code),
    KEY idx_date (record_date),
    KEY idx_ws (workshop_code),
    KEY idx_energy (energy_code),
    CONSTRAINT fk_energy_ws     FOREIGN KEY (workshop_code) REFERENCES dim_workshop (workshop_code),
    CONSTRAINT fk_energy_type   FOREIGN KEY (energy_code)   REFERENCES dim_energy_type (energy_code),
    CONSTRAINT fk_energy_date   FOREIGN KEY (record_date)   REFERENCES dim_calendar (calendar_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='能源消耗事实表';


-- -----------------------------------------------------------------------------
-- 事实表 2: 产量 (车间 × 日)
-- -----------------------------------------------------------------------------
CREATE TABLE fact_production (
    record_date    DATE         NOT NULL COMMENT '记录日期',
    workshop_code  VARCHAR(8)   NOT NULL COMMENT '车间编码',
    output_qty     DECIMAL(16,3) NOT NULL COMMENT '产量',
    output_unit    VARCHAR(16)  NOT NULL COMMENT '产量单位',
    PRIMARY KEY (record_date, workshop_code),
    KEY idx_prod_ws (workshop_code),
    CONSTRAINT fk_prod_ws   FOREIGN KEY (workshop_code) REFERENCES dim_workshop (workshop_code),
    CONSTRAINT fk_prod_date FOREIGN KEY (record_date)   REFERENCES dim_calendar (calendar_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='产量事实表';


-- =============================================================================
-- 视图: 把折标煤 / 碳排放 / 费用 统一算出来, 供分析 SQL 直接引用
-- =============================================================================

-- 明细级: 附上折标煤、碳排放、单价参考价
CREATE VIEW v_energy_enriched AS
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
    ROUND(f.consumption * e.co2_factor,      3) AS co2_kg
FROM fact_energy_consumption f
JOIN dim_workshop    w ON w.workshop_code = f.workshop_code
JOIN dim_energy_type e ON e.energy_code   = f.energy_code
JOIN dim_calendar    c ON c.calendar_date = f.record_date;


-- 车间 × 日 汇总
CREATE VIEW v_daily_workshop AS
SELECT
    record_date,
    `year_month`,
    workshop_code,
    workshop_name,
    process_type,
    ROUND(SUM(std_coal_kgce) / 1000, 4) AS tce,          -- 综合能耗 吨标煤
    ROUND(SUM(co2_kg) / 1000, 3)        AS co2_t,        -- 碳排放 吨
    ROUND(SUM(cost), 2)                 AS cost_yuan,
    MAX(CASE WHEN is_production_day = 0 THEN 1 ELSE 0 END) AS has_shutdown,
    AVG(avg_temperature)                AS avg_temperature
FROM v_energy_enriched
GROUP BY record_date, `year_month`, workshop_code, workshop_name, process_type;


-- 车间 × 月 汇总 (含产量与单位产品能耗)
CREATE VIEW v_monthly_workshop AS
SELECT
    d.`year_month`,
    d.workshop_code,
    d.workshop_name,
    d.process_type,
    w.output_unit,
    ROUND(SUM(d.tce), 4)        AS tce,
    ROUND(SUM(d.co2_t), 3)      AS co2_t,
    ROUND(SUM(d.cost_yuan), 2)  AS cost_yuan,
    ROUND(SUM(p.output_qty), 3) AS output_qty,
    CASE WHEN SUM(p.output_qty) > 0
         THEN ROUND(SUM(d.tce) * 1000 / SUM(p.output_qty), 5)
    END AS unit_energy_kgce,                                   -- 单位产品综合能耗
    CASE WHEN SUM(p.output_qty) > 0
         THEN ROUND(SUM(d.cost_yuan) / SUM(p.output_qty), 4)
    END AS unit_cost_yuan
FROM v_daily_workshop d
JOIN dim_workshop  w ON w.workshop_code = d.workshop_code
LEFT JOIN fact_production p
       ON p.record_date = d.record_date AND p.workshop_code = d.workshop_code
GROUP BY d.`year_month`, d.workshop_code, d.workshop_name, d.process_type, w.output_unit;
