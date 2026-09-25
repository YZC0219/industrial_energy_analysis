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
--   元数据 etl_watermark          ETL 水位线(增量装载进度) —— 注意它**不在** DROP 清单里
-- =============================================================================

CREATE DATABASE IF NOT EXISTS industrial_energy
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_general_ci;

USE industrial_energy;

DROP VIEW  IF EXISTS v_unit_energy_cusum;
DROP VIEW  IF EXISTS v_unit_energy_baseline;
DROP VIEW  IF EXISTS v_monthly_workshop;
DROP VIEW  IF EXISTS v_daily_workshop;
DROP VIEW  IF EXISTS v_energy_enriched;
DROP TABLE IF EXISTS fact_energy_consumption;
DROP TABLE IF EXISTS fact_production;
DROP TABLE IF EXISTS dim_calendar;
DROP TABLE IF EXISTS dim_energy_type;
DROP TABLE IF EXISTS dim_workshop;

-- 注意: etl_watermark **故意不在这里 DROP**。
--
-- 这一串 DROP 的语义是"重建 schema"; 而水位线是"状态"不是"schema"。
-- 判断标准: 重置它会不会丢掉已经完成的工作。
--   - dim_workshop / dim_energy_type 的行是**主数据**(统计口径的一部分),
--     随 --init 重置是正确的 —— 下面的 seed INSERT 会把它们重新灌成标准值。
--   - etl_watermark 的行是**装载进度**, 重置它等于抹掉"已经装到哪了"这个事实,
--     下一批就会从头重装。所以它只保留 CREATE TABLE IF NOT EXISTS 来保证表**存在**,
--     表里的行由装载流程自己维护, 活过每一次 --init。
--
-- 这条决定有一个强制性配套: --init 不能再出现在每晚的常规运行里。
-- 若 --init 照常每晚执行, 事实表被清空而水位线活了下来 —— 水位线说"已装到
-- 2025-12-31", 表里却一行没有, 下一批取空集, 数据永久丢失且不报错。
-- 所以 import_mysql.py 里 --init 与增量模式互斥, 且 load_warehouse 已改用 --incremental。


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
    is_deleted        TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '软删除标记; 增量 CDC tombstone',
    PRIMARY KEY (id),
    UNIQUE KEY uk_date_ws_energy (record_date, workshop_code, energy_code),
    KEY idx_date (record_date),
    KEY idx_ws (workshop_code),
    KEY idx_energy (energy_code),
    -- 增量装载每批要跑两次 `WHERE updated_at > <水位线>`(一次取批次, 一次取新最大值)。
    -- 没有这个索引就是两次全表扫描。当前 19,006 行扫起来不慢, 但这条路径的意图是
    -- 随天数增长, 索引是让它增长后仍然成立的保证。加索引不改变任何查询结果。
    KEY idx_updated_at (updated_at),
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


-- -----------------------------------------------------------------------------
-- ETL 水位线: 记录每张目标表"已经装载到哪一刻"
-- -----------------------------------------------------------------------------
-- 为什么需要它: 装载从"每晚全量重建"改为增量后, 必须有一个**跨运行持久**的地方
-- 记住上一批装到哪。放进程内存活不过一次 DAG run; 放文件则多一份需要和数据库
-- 保持一致的副本 —— 数据库回滚而文件已推进, 中间那段记录会被**永久跳过**。
-- 所以水位线和它描述的装载必须落在同一个事务边界内的同一份存储里。
--
-- 表结构由 CREATE TABLE IF NOT EXISTS 保证(每次 --init 幂等), 表里的行是状态,
-- 不随 --init 重置 —— 理由见文件顶部 DROP 清单旁的说明。
CREATE TABLE IF NOT EXISTS etl_watermark (
    target_table   VARCHAR(64)  NOT NULL COMMENT '目标表名',
    watermark_col  VARCHAR(64)  NOT NULL COMMENT '水位线列名(判新旧的依据列)',
    watermark_val  DATETIME     NULL     COMMENT '已装载数据的最大水位线值, NULL 表示从未装载',
    last_batch_id  VARCHAR(64)           COMMENT '最近一次装载的批次标识, 便于排查',
    last_rows      INT          NOT NULL DEFAULT 0 COMMENT '最近一次装载实际写入的行数',
    updated_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
                                 ON UPDATE CURRENT_TIMESTAMP COMMENT '水位线本身的最后修改时刻',
    PRIMARY KEY (target_table),
    UNIQUE KEY uk_watermark (target_table, watermark_col)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ETL 水位线(增量装载进度, 不随 --init 重置)';


-- =============================================================================
-- 视图: 把折标煤 / 碳排放 / 费用 统一算出来, 供分析 SQL 直接引用
-- =============================================================================

-- 明细级: 附上折标煤、碳排放、单价参考价
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
    ROUND(f.consumption * e.co2_factor,      3) AS co2_kg
FROM fact_energy_consumption f
JOIN dim_workshop    w ON w.workshop_code = f.workshop_code
JOIN dim_energy_type e ON e.energy_code   = f.energy_code
JOIN dim_calendar    c ON c.calendar_date = f.record_date
WHERE f.is_deleted = 0;


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


-- =============================================================================
-- 单耗基线模型 (供 Q25/Q26/Q27 共用)
-- =============================================================================

-- 产量基线模型 —— 每车间独立拟合 "期望单耗 = a + b/产量 + g·sin + h·cos",
-- 并给出残差与其标准化值。抽成视图是因为 Q25/Q26/Q27 都要用同一套残差,
-- 口径必须只有一处定义(与 Q16/Q24 同口径: 产量>0、剔除公用工程)。
--
-- 为什么用 1/产量 而不是 ln(产量): 单耗的生成机理是
--     ue ∝ (待机底负荷 + (1-待机底负荷)·负荷) × 季节 × 趋势
-- 而负荷正比于产量, 所以 ue = 待机底·k/产量 + (1-待机底)·k ——
-- 它本来就是 1/产量 的**线性**函数, 是精确形式。上一版用 ln(产量) 只是近似:
-- 连续型车间(熔炼/轧制/热处理)待机底负荷占比低、双曲线形态弱, 两种基函数拟合
-- 几乎无差; 但间歇型车间待机底负荷占比高, 换用 1/产量 后残差 σ 下降 22%~39%
-- (装配 1.657→1.012、机加工 0.196→0.140、表面处理 0.566→0.442)。
--
-- 为什么带季节项: 天然气采暖、蒸汽冬季高, 是独立于产量的第二部分结构,
-- 不分离出来的话冬天的残差会整体偏正, 把冬季全报成异常。
--
-- 回归用正规方程 (X'X)β = X'y 闭合求解。因为 Σsin = Σcos = 0 且 Σ(sin·cos) = 0,
-- b 与 (g,h) 解耦, 故 b 可独立解出、g/h 再由中心化的 2×2 解 —— 这不是近似,
-- 是这两个基函数正交带来的精确简化。
--
-- 本视图给出**两套残差**, 用途不同, 不要混用:
--   r / z_r  仅扣掉产量与季节。逐日判据(Q25 的控制图、Q26 的两法对比)用它,
--            因为它回答的是"这天相对同产量同季节的日子是否异常"。
--   w / z_w  在 r 之上再扣掉 (车间×月) 的季节轮廓。**只有累积型判据(Q27 的
--            CUSUM)用它**。原因是逐日判据每天重新起算, 月度均值偏一点无所谓;
--            而 CUSUM 会把"每年 2 月都偏高"这种规律性偏移一路累积成持续漂移,
--            误判成设备劣化 —— 实测连续型车间(熔炼/轧制/热处理)的累积和峰值
--            会虚高一倍以上(轧制 15.7→37.1), 即约六成是季节伪影。
--
--            但白化不是对所有车间都"降噪": 间歇型车间(机加工/表面处理/装配/
--            包装)白化后 σ 也明显变小, 按各自 σ 标准化后累积和反而略升。原因是
--            它们的持续性本来就来自节假日簇而非季节, 白化去掉的是方差不是信号。
CREATE VIEW v_unit_energy_baseline AS
WITH d AS (
    SELECT
        v.record_date, v.workshop_code, v.workshop_name,
        v.tce, p.output_qty,
        v.tce * 1000 / NULLIF(p.output_qty, 0) AS ue,
        1.0 / p.output_qty                     AS x,
        SIN(2 * PI() * DAYOFYEAR(v.record_date) / 365.0) AS s,
        COS(2 * PI() * DAYOFYEAR(v.record_date) / 365.0) AS c
    FROM v_daily_workshop v
    JOIN fact_production p
      ON p.record_date = v.record_date AND p.workshop_code = v.workshop_code
    JOIN dim_workshop w ON w.workshop_code = v.workshop_code
    WHERE p.output_qty > 0 AND w.process_type <> '公用工程'
),
mom AS (
    SELECT workshop_code, COUNT(*) AS n,
           SUM(x) AS sx, SUM(x*x) AS sxx, SUM(ue) AS sy, SUM(x*ue) AS sxy,
           SUM(s) AS ss, SUM(c) AS sc,
           SUM(s*s) AS sss, SUM(c*c) AS scc, SUM(s*c) AS ssc,
           SUM(s*ue) AS ssy, SUM(c*ue) AS scy
    FROM d GROUP BY workshop_code
),
bh AS (
    SELECT workshop_code, n, sx, sxx, sy, ss, sc, ssy, scy,
        CASE WHEN n*sxx - sx*sx > 0 THEN (n*sxy - sx*sy) / (n*sxx - sx*sx) END AS b,
        CASE WHEN (n*sss-ss*ss)*(n*scc-sc*sc) - POW(n*ssc-ss*sc,2) > 0
             THEN ((n*ssy-ss*sy)*(n*scc-sc*sc) - (n*scy-sc*sy)*(n*ssc-ss*sc))
                  / ((n*sss-ss*ss)*(n*scc-sc*sc) - POW(n*ssc-ss*sc,2)) END AS g,
        CASE WHEN (n*sss-ss*ss)*(n*scc-sc*sc) - POW(n*ssc-ss*sc,2) > 0
             THEN ((n*scy-sc*sy)*(n*sss-ss*ss) - (n*ssy-ss*sy)*(n*ssc-ss*sc))
                  / ((n*sss-ss*ss)*(n*scc-sc*sc) - POW(n*ssc-ss*sc,2)) END AS h
    FROM mom
),
coef AS (
    -- a 由 b/g/h 回代得到: a = ȳ − b·x̄ − g·s̄ − h·c̄
    SELECT workshop_code, b, g, h,
           sy/n - b*(sx/n) - g*(ss/n) - h*(sc/n) AS a
    FROM bh
),
pred AS (
    SELECT d.*, k.a + k.b*d.x + k.g*d.s + k.h*d.c AS yhat
    FROM d JOIN coef k ON k.workshop_code = d.workshop_code
),
resid AS (
    SELECT record_date, workshop_code, workshop_name, output_qty, ue, yhat,
           ue - yhat AS r
    FROM pred
),
-- 季节轮廓: 按(车间 × 自然月)在这两年数据上合并估计的残差均值。
-- 用自然月而不是 "年+月": 两年的同一个月本来就应该有同一个季节水平, 分开估会把
-- 样本量砍半而估计更噪; 年际差异属于趋势, 由第 3 步的 CUSUM 去抓。
prof AS (
    SELECT workshop_code, MONTH(record_date) AS mth, AVG(r) AS mr
    FROM resid GROUP BY workshop_code, MONTH(record_date)
),
white AS (
    SELECT e.*, e.r - pf.mr AS w
    FROM resid e
    JOIN prof pf ON pf.workshop_code = e.workshop_code
                AND pf.mth = MONTH(e.record_date)
),
stat AS (
    SELECT workshop_code,
           AVG(r)  AS mu_r,
           -- sd = sqrt((Σr² − (Σr)²/n) / (n−1)), 纯 SUM 聚合, 无逐行子查询
           SQRT(GREATEST(SUM(r*r)  - POW(SUM(r),  2)/COUNT(*), 0) / (COUNT(*)-1)) AS sd_r,
           AVG(w)  AS mu_w,
           SQRT(GREATEST(SUM(w*w)  - POW(SUM(w),  2)/COUNT(*), 0) / (COUNT(*)-1)) AS sd_w,
           AVG(ue) AS mu_u,
           SQRT(GREATEST(SUM(ue*ue)- POW(SUM(ue), 2)/COUNT(*), 0) / (COUNT(*)-1)) AS sd_u
    FROM white GROUP BY workshop_code
)
SELECT
    e.record_date, e.workshop_code, e.workshop_name,
    e.output_qty, e.ue, e.yhat, e.r,
    st.mu_r, st.sd_r,
    (e.r  - st.mu_r) / NULLIF(st.sd_r, 0) AS z_r,   -- 残差标准化(逐日判据用)
    e.w, st.mu_w, st.sd_w,
    (e.w  - st.mu_w) / NULLIF(st.sd_w, 0) AS z_w,   -- 白化残差标准化(CUSUM 用)
    st.mu_u, st.sd_u,
    (e.ue - st.mu_u) / NULLIF(st.sd_u, 0) AS z_u    -- 原始单耗标准化(供固定阈值法对比)
FROM white e
JOIN stat st ON st.workshop_code = e.workshop_code;


-- CUSUM(累积和)控制图 —— 跑在 v_unit_energy_baseline 的**白化**残差 z_w 上。
--
-- 为什么需要它: 2σ 与产量基线都是**逐日截面**判据, 它们比较"今天 vs 平时",
-- 一天一天看。若单耗持续偏低, 每一天相对它自己的邻域都可能"正常", 两种方法都
-- 检不出来; 只有把偏差**累积**起来才能发现"持续偏移"这件事。这正是 CUSUM 的
-- 用途: 抓持续性, 与抓单点跳变的 2σ/基线法互补。
--
-- 递推式:  S⁺ₜ = max(0, S⁺ₜ₋₁ + zₜ − k)      S⁻ₜ = max(0, S⁻ₜ₋₁ − zₜ − k)
-- 展开后有一个闭合解:  S⁺ₜ = Cₜ − min_{0≤j≤t} Cⱼ,  其中 Cₜ = Σ(zᵢ − k)
-- 所以不必写递归 CTE(5117 行会撞 cte_max_recursion_depth), 两个窗口函数即可:
-- 一个前缀和 + 一个前缀最小值。min 要带上 C₀ = 0, 故取 LEAST(0, …)。
--
-- 参数 k = 0.5(松弛量, 对 1σ 量级的持续偏移最敏感)。
--
-- 决策限 h 取 **9.9σ 而不是教科书的 5σ**, 这一点必须说清楚:
--   h=5 对应 ARL₀ ≈ 300 天, 那是为"在线连续监控、每天判一次"标定的。本项目是
--   对 730 天历史做**一次性回顾**检验, 同一个门槛下误报率完全不同。5000 次零假设
--   模拟(纯白噪声, k=0.5, 统计量取 max(S⁺,S⁻))给出:
--       n=365  中位 5.06  99分位  8.76   h=5 误报率 52.3%
--       n=730  中位 5.75  99分位  9.90   h=5 误报率 77.4%   <- 本项目
--       n=1461 中位 6.45  99分位 10.40   h=5 误报率 95.6%
--   即 n=730 时用 h=5, 77% 的车间会至少报一次警, 全是噪声。故按 99 分位取 9.9。
--
-- 视图同时给出未白化(z_r)的 S 轨迹作对照, 供 Q26 量化"naive CUSUM 里有多少是
-- 季节伪影"。正式判定只用白化后的 s_hi / s_lo。
CREATE VIEW v_unit_energy_cusum AS
WITH z AS (
    SELECT record_date, workshop_code, workshop_name, z_r, z_w,
           z_r - 0.5 AS cr_hi,  -z_r - 0.5 AS cr_lo,   -- 未白化(对照)
           z_w - 0.5 AS cw_hi,  -z_w - 0.5 AS cw_lo    -- 白化后(正式)
    FROM v_unit_energy_baseline
),
cum AS (
    SELECT record_date, workshop_code, workshop_name, z_r, z_w,
           SUM(cr_hi) OVER w AS Cr_hi, SUM(cr_lo) OVER w AS Cr_lo,
           SUM(cw_hi) OVER w AS Cw_hi, SUM(cw_lo) OVER w AS Cw_lo
    FROM z
    WINDOW w AS (PARTITION BY workshop_code ORDER BY record_date
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
),
s AS (
    SELECT record_date, workshop_code, workshop_name, z_r, z_w,
           Cr_hi - LEAST(0.0, MIN(Cr_hi) OVER w) AS s_hi_raw,
           Cr_lo - LEAST(0.0, MIN(Cr_lo) OVER w) AS s_lo_raw,
           Cw_hi - LEAST(0.0, MIN(Cw_hi) OVER w) AS s_hi,   -- 上侧累积和 S⁺ₜ
           Cw_lo - LEAST(0.0, MIN(Cw_lo) OVER w) AS s_lo    -- 下侧累积和 S⁻ₜ
    FROM cum
    WINDOW w AS (PARTITION BY workshop_code ORDER BY record_date
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
)
-- 决策限只在 lim 里写一次, 再随结果列带出去: 报告要画这条线, 画的时候必须跟
-- SQL 的判定同源, 不能在前端另抄一个常数(抄了就会有一天改了一处忘了另一处)。
, lim AS (SELECT 9.9 AS h)
SELECT
    s.record_date, s.workshop_code, s.workshop_name, s.z_r, s.z_w,
    ROUND(s.s_hi_raw, 4) AS s_hi_raw, ROUND(s.s_lo_raw, 4) AS s_lo_raw,
    ROUND(s.s_hi,     4) AS s_hi,     ROUND(s.s_lo,     4) AS s_lo,
    l.h AS h,
    CASE WHEN GREATEST(s.s_hi, s.s_lo) > l.h THEN 1 ELSE 0 END AS is_alarm,
    CASE WHEN s.s_hi > l.h THEN '偏高'
         WHEN s.s_lo > l.h THEN '偏低' END                      AS alarm_side
FROM s CROSS JOIN lim l;
