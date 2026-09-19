-- =============================================================================
-- 工业能耗分析 —— 分析查询集
--
-- 执行方式一: mysql -u root -p industrial_energy < sql/analysis.sql
-- 执行方式二: python src/import_mysql.py --run-analysis
--             (逐条执行并把结果导出到 output/*.csv)
--
-- 每条查询以 "-- @@name <名称>" 开头, 便于脚本切分与导出。
-- 依赖视图: v_energy_enriched / v_daily_workshop / v_monthly_workshop
-- =============================================================================

USE industrial_energy;


-- @@name Q01_能源消费总览
-- @@desc 全厂 2024-2025 综合能耗、费用、碳排放总量与日均水平
SELECT
    COUNT(DISTINCT record_date)                             AS 统计天数,
    COUNT(DISTINCT workshop_code)                           AS 车间数,
    COUNT(DISTINCT energy_code)                             AS 能源品种数,
    ROUND(SUM(std_coal_kgce) / 1000, 2)                     AS 综合能耗_tce,
    ROUND(SUM(cost), 2)                                     AS 能源费用_元,
    ROUND(SUM(co2_kg) / 1000, 2)                            AS 碳排放_tCO2,
    ROUND(SUM(std_coal_kgce) / 1000 / COUNT(DISTINCT record_date), 3) AS 日均能耗_tce,
    ROUND(SUM(cost) / COUNT(DISTINCT record_date), 2)       AS 日均费用_元
FROM v_energy_enriched;


-- @@name Q02_剔除公用工程后的能耗总览
-- @@desc 动力站(公用工程)产出的蒸汽被其他车间二次消费, 全厂口径会重复计算,
--        因此单耗类指标应剔除动力站口径
SELECT
    ROUND(SUM(std_coal_kgce) / 1000, 2)  AS 综合能耗_tce,
    ROUND(SUM(cost), 2)                  AS 能源费用_元,
    ROUND(SUM(co2_kg) / 1000, 2)         AS 碳排放_tCO2
FROM v_energy_enriched
WHERE process_type <> '公用工程';


-- @@name Q03_各车间综合能耗排名
-- @@desc 折标煤口径的车间能耗排名, 含占比与费用强度
SELECT
    workshop_code                                   AS 车间编码,
    workshop_name                                   AS 车间,
    process_type                                    AS 工序,
    ROUND(SUM(std_coal_kgce) / 1000, 2)             AS 综合能耗_tce,
    ROUND(100 * SUM(std_coal_kgce) / SUM(SUM(std_coal_kgce)) OVER (), 2) AS 能耗占比_pct,
    ROUND(SUM(cost), 2)                             AS 能源费用_元,
    ROUND(100 * SUM(cost) / SUM(SUM(cost)) OVER (), 2)  AS 费用占比_pct,
    ROUND(SUM(co2_kg) / 1000, 2)                    AS 碳排放_tCO2,
    RANK() OVER (ORDER BY SUM(std_coal_kgce) DESC)  AS 能耗排名
FROM v_energy_enriched
GROUP BY workshop_code, workshop_name, process_type
ORDER BY 综合能耗_tce DESC;


-- @@name Q04_能源结构_折标煤与费用双口径
-- @@desc 各能源品种的实物量、折标煤占比、费用占比、碳排放
SELECT
    energy_code                                     AS 能源编码,
    energy_name                                     AS 能源,
    unit                                            AS 单位,
    ROUND(SUM(consumption), 2)                      AS 实物消耗量,
    ROUND(SUM(std_coal_kgce) / 1000, 2)             AS 折标煤_tce,
    ROUND(100 * SUM(std_coal_kgce) / SUM(SUM(std_coal_kgce)) OVER (), 2) AS 折标煤占比_pct,
    ROUND(SUM(cost), 2)                             AS 费用_元,
    ROUND(100 * SUM(cost) / SUM(SUM(cost)) OVER (), 2)  AS 费用占比_pct,
    ROUND(SUM(co2_kg) / 1000, 2)                    AS 碳排放_tCO2,
    ROUND(SUM(cost) / NULLIF(SUM(consumption), 0), 4) AS 实际均价
FROM v_energy_enriched
GROUP BY energy_code, energy_name, unit
ORDER BY 折标煤_tce DESC;


-- @@name Q05_月度能耗趋势与环比
-- @@desc 逐月综合能耗、费用, 并用窗口函数计算环比(MoM)
WITH m AS (
    SELECT
        `year_month`,
        SUM(std_coal_kgce) / 1000 AS tce,
        SUM(cost)                 AS cost_yuan,
        SUM(co2_kg) / 1000        AS co2_t
    FROM v_energy_enriched
    GROUP BY `year_month`
)
SELECT
    `year_month`                                      AS 年月,
    ROUND(tce, 2)                                   AS 综合能耗_tce,
    ROUND(cost_yuan, 2)                             AS 能源费用_元,
    ROUND(co2_t, 2)                                 AS 碳排放_tCO2,
    ROUND(LAG(tce) OVER (ORDER BY `year_month`), 2)   AS 上月能耗_tce,
    ROUND(100 * (tce - LAG(tce) OVER (ORDER BY `year_month`))
              / LAG(tce) OVER (ORDER BY `year_month`), 2) AS 环比_pct
FROM m
ORDER BY `year_month`;


-- @@name Q06_各车间能耗同比
-- @@desc 2025 年 vs 2024 年综合能耗同比, 产量增长的同时能耗是否同步增长
SELECT
    workshop_code  AS 车间编码,
    workshop_name  AS 车间,
    ROUND(SUM(CASE WHEN year = 2024 THEN std_coal_kgce END) / 1000, 2) AS 能耗2024_tce,
    ROUND(SUM(CASE WHEN year = 2025 THEN std_coal_kgce END) / 1000, 2) AS 能耗2025_tce,
    ROUND(100 * (SUM(CASE WHEN year = 2025 THEN std_coal_kgce END)
               - SUM(CASE WHEN year = 2024 THEN std_coal_kgce END))
              / NULLIF(SUM(CASE WHEN year = 2024 THEN std_coal_kgce END), 0), 2) AS 同比_pct
FROM v_energy_enriched
GROUP BY workshop_code, workshop_name
ORDER BY 同比_pct;


-- @@name Q07_各车间单位产品能耗月度趋势
-- @@desc 单位产品综合能耗(kgce/单位产量), 口径为剔除公用工程后的生产车间
SELECT
    `year_month`        AS 年月,
    workshop_name     AS 车间,
    output_qty        AS 产量,
    output_unit       AS 产量单位,
    tce               AS 综合能耗_tce,
    unit_energy_kgce  AS 单位产品能耗_kgce
FROM v_monthly_workshop
WHERE process_type <> '公用工程'
ORDER BY workshop_code, `year_month`;


-- @@name Q08_单位产品能耗同比_节能改造效果
-- @@desc 以年度平均单耗衡量各车间节能改造的实际成效
SELECT
    workshop_name  AS 车间,
    ROUND(AVG(CASE WHEN LEFT(`year_month`, 4) = '2024' THEN unit_energy_kgce END), 4) AS 单耗2024,
    ROUND(AVG(CASE WHEN LEFT(`year_month`, 4) = '2025' THEN unit_energy_kgce END), 4) AS 单耗2025,
    ROUND(100 * (AVG(CASE WHEN LEFT(`year_month`, 4) = '2025' THEN unit_energy_kgce END)
               - AVG(CASE WHEN LEFT(`year_month`, 4) = '2024' THEN unit_energy_kgce END))
              / NULLIF(AVG(CASE WHEN LEFT(`year_month`, 4) = '2024' THEN unit_energy_kgce END), 0), 2)
        AS 单耗同比_pct
FROM v_monthly_workshop
WHERE process_type <> '公用工程'
GROUP BY workshop_code, workshop_name
ORDER BY 单耗同比_pct;


-- @@name Q09_能源费用帕累托分析
-- @@desc 车间能源费用降序累计占比, 识别关键少数(通常前 3 个车间占 70%+)
WITH t AS (
    SELECT workshop_name, SUM(cost) AS c
    FROM v_energy_enriched
    GROUP BY workshop_name
)
SELECT
    workshop_name                                   AS 车间,
    ROUND(c, 2)                                     AS 能源费用_元,
    ROUND(100 * c / SUM(c) OVER (), 2)              AS 费用占比_pct,
    ROUND(100 * SUM(c) OVER (ORDER BY c DESC) / SUM(c) OVER (), 2) AS 累计占比_pct
FROM t
ORDER BY c DESC;


-- @@name Q10_各车间能耗结构
-- @@desc 交叉表: 每个车间各能源品种的折标煤消耗, 定位各车间的用能特征
SELECT
    workshop_name AS 车间,
    ROUND(SUM(CASE WHEN energy_code = 'E01' THEN std_coal_kgce END) / 1000, 2) AS 电力_tce,
    ROUND(SUM(CASE WHEN energy_code = 'E02' THEN std_coal_kgce END) / 1000, 2) AS 天然气_tce,
    ROUND(SUM(CASE WHEN energy_code = 'E03' THEN std_coal_kgce END) / 1000, 2) AS 蒸汽_tce,
    ROUND(SUM(CASE WHEN energy_code = 'E04' THEN std_coal_kgce END) / 1000, 2) AS 工业水_tce,
    ROUND(SUM(CASE WHEN energy_code = 'E05' THEN std_coal_kgce END) / 1000, 2) AS 压缩空气_tce,
    ROUND(SUM(CASE WHEN energy_code = 'E06' THEN std_coal_kgce END) / 1000, 2) AS 柴油_tce,
    ROUND(SUM(std_coal_kgce) / 1000, 2) AS 合计_tce
FROM v_energy_enriched
GROUP BY workshop_code, workshop_name
ORDER BY 合计_tce DESC;


-- @@name Q11_气温与能耗的关系_分档
-- @@desc 仅取连续型车间(全年不停产), 排除停产日对气温信号的干扰。
--        电力呈"U 形"——冬季采暖与夏季制冷双高峰; 天然气/蒸汽随气温升高单调下降
SELECT
    CASE
        WHEN avg_temperature <  0 THEN '1_零度以下'
        WHEN avg_temperature < 10 THEN '2_0-10℃'
        WHEN avg_temperature < 20 THEN '3_10-20℃'
        WHEN avg_temperature < 28 THEN '4_20-28℃'
        ELSE                           '5_28℃以上'
    END                                                   AS 气温区间,
    COUNT(DISTINCT record_date)                           AS 天数,
    ROUND(AVG(avg_temperature), 1)                        AS 区间均温,
    ROUND(SUM(CASE WHEN energy_code = 'E01' THEN consumption END)
          / COUNT(DISTINCT record_date), 0)               AS 日均电耗_kWh,
    ROUND(SUM(CASE WHEN energy_code = 'E02' THEN consumption END)
          / COUNT(DISTINCT record_date), 0)               AS 日均气耗_m3,
    ROUND(SUM(CASE WHEN energy_code = 'E03' THEN consumption END)
          / COUNT(DISTINCT record_date), 1)               AS 日均蒸汽_t
FROM v_energy_enriched
WHERE is_continuous = 1
GROUP BY 气温区间
ORDER BY 气温区间;


-- @@name Q12_气温与各能源相关系数
-- @@desc 手算 Pearson 相关系数(MySQL 无 CORR 函数), 同样限定连续型车间。
--        天然气/蒸汽与气温强负相关(采暖驱动); 电力因冬夏双高峰呈 U 形,
--        线性相关系数天然偏弱, 需结合 Q11 的分档结果解读
WITH d AS (
    SELECT
        record_date,
        AVG(avg_temperature) AS t,
        COALESCE(SUM(CASE WHEN energy_code = 'E01' THEN consumption END), 0) AS kwh,
        COALESCE(SUM(CASE WHEN energy_code = 'E02' THEN consumption END), 0) AS gas,
        COALESCE(SUM(CASE WHEN energy_code = 'E03' THEN consumption END), 0) AS steam
    FROM v_energy_enriched
    WHERE is_continuous = 1
    GROUP BY record_date
)
SELECT
    COUNT(*) AS 样本天数,
    ROUND((COUNT(*) * SUM(t * kwh) - SUM(t) * SUM(kwh))
        / SQRT((COUNT(*) * SUM(t * t) - SUM(t) * SUM(t))
             * (COUNT(*) * SUM(kwh * kwh) - SUM(kwh) * SUM(kwh))), 4) AS 电力_r,
    ROUND((COUNT(*) * SUM(t * gas) - SUM(t) * SUM(gas))
        / SQRT((COUNT(*) * SUM(t * t) - SUM(t) * SUM(t))
             * (COUNT(*) * SUM(gas * gas) - SUM(gas) * SUM(gas))), 4) AS 天然气_r,
    ROUND((COUNT(*) * SUM(t * steam) - SUM(t) * SUM(steam))
        / SQRT((COUNT(*) * SUM(t * t) - SUM(t) * SUM(t))
             * (COUNT(*) * SUM(steam * steam) - SUM(steam) * SUM(steam))), 4) AS 蒸汽_r
FROM d;


-- @@name Q13_停产日待机损耗分析
-- @@desc 非生产日仍发生的能耗即待机损耗, 是节能挖潜的重点
SELECT
    workshop_name  AS 车间,
    process_type   AS 工序,
    COUNT(DISTINCT CASE WHEN is_production_day = 0 THEN record_date END) AS 停产天数,
    ROUND(SUM(CASE WHEN is_production_day = 0 THEN std_coal_kgce END) / 1000, 2) AS 待机能耗_tce,
    ROUND(SUM(std_coal_kgce) / 1000, 2)                                          AS 综合能耗_tce,
    ROUND(100 * SUM(CASE WHEN is_production_day = 0 THEN std_coal_kgce END)
              / NULLIF(SUM(std_coal_kgce), 0), 2)                                AS 待机占比_pct,
    ROUND(SUM(CASE WHEN is_production_day = 0 THEN cost END), 2)                 AS 待机浪费_元
FROM v_energy_enriched
GROUP BY workshop_code, workshop_name, process_type
ORDER BY 待机能耗_tce DESC;


-- @@name Q14_工作日与周末节假日能耗对比
-- @@desc 对比不同日型的日均能耗与日均费用
SELECT
    CASE WHEN is_holiday = 1 THEN '1_法定节假日'
         WHEN is_weekend = 1 THEN '2_周末'
         ELSE                     '3_工作日' END               AS 日型,
    COUNT(DISTINCT record_date)                                AS 天数,
    ROUND(SUM(std_coal_kgce) / 1000 / COUNT(DISTINCT record_date), 3) AS 日均能耗_tce,
    ROUND(SUM(cost) / COUNT(DISTINCT record_date), 2)          AS 日均费用_元,
    ROUND(SUM(co2_kg) / 1000 / COUNT(DISTINCT record_date), 3) AS 日均碳排_tCO2
FROM v_energy_enriched
GROUP BY 日型
ORDER BY 日型;


-- @@name Q15_各节假日能耗水平
-- @@desc 按 年份+节日 统计停工期能耗, 识别长假期间的能源浪费
SELECT
    year                                        AS 年份,
    holiday_name                                AS 节假日,
    COUNT(DISTINCT record_date)                 AS 天数,
    ROUND(SUM(std_coal_kgce) / 1000, 2)         AS 综合能耗_tce,
    ROUND(SUM(std_coal_kgce) / 1000 / COUNT(DISTINCT record_date), 3) AS 日均能耗_tce,
    ROUND(SUM(cost), 2)                         AS 能源费用_元
FROM v_energy_enriched
WHERE is_holiday = 1
GROUP BY year, holiday_name
ORDER BY 日均能耗_tce DESC;


-- @@name Q16_单耗异常日检测_2sigma
-- @@desc 单位产品能耗超过车间自身 均值+2倍标准差 的日期, 用于能效诊断
WITH d AS (
    SELECT
        v.record_date, v.workshop_code, v.workshop_name,
        v.tce, p.output_qty,
        v.tce * 1000 / NULLIF(p.output_qty, 0) AS ue
    FROM v_daily_workshop v
    JOIN fact_production p
      ON p.record_date = v.record_date AND p.workshop_code = v.workshop_code
    JOIN dim_workshop w ON w.workshop_code = v.workshop_code
    WHERE p.output_qty > 0 AND w.process_type <> '公用工程'
),
s AS (
    SELECT workshop_code, AVG(ue) AS mu, STDDEV_SAMP(ue) AS sd
    FROM d GROUP BY workshop_code
)
SELECT
    d.record_date                     AS 日期,
    d.workshop_name                   AS 车间,
    ROUND(d.output_qty, 2)            AS 产量,
    ROUND(d.ue, 4)                    AS 单位产品能耗_kgce,
    ROUND(s.mu, 4)                    AS 车间均值,
    ROUND(s.sd, 4)                    AS 标准差,
    ROUND((d.ue - s.mu) / s.sd, 2)    AS Z值
FROM d
JOIN s ON s.workshop_code = d.workshop_code
WHERE d.ue > s.mu + 2 * s.sd
ORDER BY Z值 DESC
LIMIT 50;


-- @@name Q17_能耗连续三日上涨
-- @@desc 窗口函数识别连续 3 天能耗递增的车间, 及时发现跑冒滴漏与设备劣化
WITH d AS (
    SELECT record_date, workshop_code, workshop_name, SUM(std_coal_kgce) AS tce
    FROM v_energy_enriched
    GROUP BY record_date, workshop_code, workshop_name
),
lagged AS (
    SELECT
        *,
        LAG(tce, 1) OVER w AS prev1,
        LAG(tce, 2) OVER w AS prev2
    FROM d
    WINDOW w AS (PARTITION BY workshop_code ORDER BY record_date)
)
SELECT
    record_date           AS 日期,
    workshop_name         AS 车间,
    ROUND(tce, 0)         AS 当日能耗_kgce,
    ROUND(prev1, 0)       AS 前一日_kgce,
    ROUND(prev2, 0)       AS 前二日_kgce,
    ROUND(100 * (tce - prev1) / prev1, 2) AS 日增幅_pct
FROM lagged
WHERE tce > prev1 AND prev1 > prev2
ORDER BY record_date, workshop_name;


-- @@name Q18_全厂能耗7日移动平均
-- @@desc 平滑日波动, 观察能耗趋势与波动区间
WITH d AS (
    SELECT record_date, SUM(std_coal_kgce) AS tce
    FROM v_energy_enriched
    GROUP BY record_date
)
SELECT
    record_date AS 日期,
    ROUND(tce, 0) AS 日能耗_kgce,
    ROUND(AVG(tce) OVER (ORDER BY record_date
                         ROWS BETWEEN 6 PRECEDING AND CURRENT ROW), 0) AS 七日移动平均,
    ROUND(STDDEV_SAMP(tce) OVER (ORDER BY record_date
                         ROWS BETWEEN 6 PRECEDING AND CURRENT ROW), 0) AS 七日标准差
FROM d
ORDER BY record_date;


-- @@name Q19_能源采购单价月度波动
-- @@desc 实际结算均价 vs 主数据参考价, 监控采购价偏离与成本风险
SELECT
    `year_month`                                       AS 年月,
    energy_name                                      AS 能源,
    unit                                             AS 单位,
    ROUND(SUM(consumption), 2)                       AS 消耗量,
    ROUND(SUM(cost) / NULLIF(SUM(consumption), 0), 4) AS 实际均价,
    MAX(reference_price)                             AS 参考单价,
    ROUND(100 * (SUM(cost) / NULLIF(SUM(consumption), 0)
                 - MAX(reference_price)) / MAX(reference_price), 2) AS 偏离_pct
FROM v_energy_enriched
GROUP BY `year_month`, energy_name, unit
ORDER BY energy_name, `year_month`;


-- @@name Q20_电耗强度月度趋势
-- @@desc 单位产量电耗(kWh/单位), 电力是最主要的二次能源成本项
SELECT
    e.`year_month`                          AS 年月,
    e.workshop_name                         AS 车间,
    w.output_unit                           AS 产量单位,
    ROUND(e.kwh / NULLIF(p.qty, 0), 4)      AS 单位产品电耗_kWh
FROM (
    SELECT `year_month`, workshop_code, workshop_name, SUM(consumption) AS kwh
    FROM v_energy_enriched
    WHERE energy_code = 'E01'
    GROUP BY `year_month`, workshop_code, workshop_name
) e
JOIN (
    SELECT
        c.`year_month`, p.workshop_code, SUM(p.output_qty) AS qty
    FROM fact_production p
    JOIN dim_calendar c ON c.calendar_date = p.record_date
    GROUP BY c.`year_month`, p.workshop_code
) p ON p.`year_month` = e.`year_month` AND p.workshop_code = e.workshop_code
JOIN dim_workshop w ON w.workshop_code = e.workshop_code
WHERE w.process_type <> '公用工程'
ORDER BY e.workshop_code, e.`year_month`;


-- @@name Q21_碳排放强度
-- @@desc 月度碳排放总量与单位标煤碳排强度, 反映能源结构清洁化程度
SELECT
    `year_month`                                        AS 年月,
    ROUND(SUM(co2_kg) / 1000, 2)                      AS 碳排放_tCO2,
    ROUND(SUM(std_coal_kgce) / 1000, 2)               AS 综合能耗_tce,
    ROUND(SUM(co2_kg) / NULLIF(SUM(std_coal_kgce), 0), 4) AS 碳排强度_tCO2每tce
FROM v_energy_enriched
GROUP BY `year_month`
ORDER BY `year_month`;


-- @@name Q22_能耗突增预警_环比超25pct
-- @@desc 月度环比增幅超 25% 的车间, 作为能效异常的第一道告警
--       (阈值需结合季节波动设定: 本数据季节峰谷差约 20%, 故 25% 为合理告警线)
WITH m AS (
    SELECT `year_month`, workshop_code, workshop_name, SUM(std_coal_kgce) AS tce
    FROM v_energy_enriched
    GROUP BY `year_month`, workshop_code, workshop_name
),
l AS (
    SELECT
        *,
        LAG(tce) OVER (PARTITION BY workshop_code ORDER BY `year_month`) AS prev
    FROM m
)
SELECT
    `year_month`                            AS 年月,
    workshop_name                         AS 车间,
    ROUND(tce / 1000, 3)                  AS 当月能耗_tce,
    ROUND(prev / 1000, 3)                 AS 上月能耗_tce,
    ROUND(100 * (tce - prev) / prev, 2)   AS 环比_pct
FROM l
WHERE prev IS NOT NULL
  AND (tce - prev) / prev > 0.25
ORDER BY 环比_pct DESC;


-- @@name Q23_能耗最高的车间日TOP20
-- @@desc 绝对能耗最高的车间-日, 用于峰值负荷与容量费管理
SELECT
    record_date       AS 日期,
    workshop_name     AS 车间,
    tce               AS 综合能耗_tce,
    co2_t             AS 碳排放_tCO2,
    cost_yuan         AS 能源费用_元,
    ROUND(avg_temperature, 1) AS 日均气温
FROM v_daily_workshop
ORDER BY tce DESC
LIMIT 20;
