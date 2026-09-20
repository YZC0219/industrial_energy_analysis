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
ORDER BY 综合能耗_tce DESC, 车间编码;


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
ORDER BY 折标煤_tce DESC, 能源编码;


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
ORDER BY 同比_pct, 车间编码;


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
ORDER BY 单耗同比_pct, 车间;


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
ORDER BY c DESC, 车间;


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
ORDER BY 合计_tce DESC, 车间;


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
ORDER BY 待机能耗_tce DESC, 车间;


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
ORDER BY 日均能耗_tce DESC, 节假日;


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
ORDER BY Z值 DESC, 日期, 车间
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
ORDER BY 环比_pct DESC, 年月, 车间;


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
ORDER BY tce DESC, record_date, 车间
LIMIT 20;


-- @@name Q25_产量基线单耗期望值
-- @@desc 【#10 考虑产量的能耗基线】按 产量 + 季节 拟合该车间的"期望单耗", 再拿
--       实际单耗与期望值之差(残差)判异常。与 Q16/Q24 的固定阈值思路相比, 这条
--       基线会随当日产量与季节移动: 同样烧掉 100 tce, 产量高的那天不算异常。
--
--       模型(每个车间独立拟合, 最小二乘闭合解, 定义见 v_unit_energy_baseline):
--           期望单耗 = a + b/产量 + g·sin(2π·年内第几天/365) + h·cos(…)
--       为什么是 1/产量: 待机底负荷被摊到产量上, 单耗本来就是 1/产量 的线性函数。
--       为什么带季节项: 天然气采暖、蒸汽冬季高, 是独立于产量的第二部分结构,
--       不分离出来的话冬天的残差会整体偏正, 把冬季全报成异常。
--
--       回归与残差统计都收在视图里, 这条查询只负责取数与判限, 保证 Q25/Q26/Q27
--       用的是同一套残差口径。
SELECT
    record_date                                 AS 日期,
    workshop_code                               AS 车间编码,
    workshop_name                               AS 车间,
    ROUND(output_qty, 3)                        AS 产量,
    ROUND(ue, 4)                                AS 实际单耗_kgce,
    ROUND(yhat, 4)                              AS 期望单耗_kgce,
    ROUND(r, 4)                                 AS 单耗残差_kgce,
    ROUND(mu_r, 4)                              AS 残差均值,
    ROUND(sd_r, 4)                              AS 残差标准差,
    ROUND(z_r, 2)                               AS Z值,
    -- 阈值口径与 Q16/Q24 一致: 用 mu+2sd, 只是这里的 "mu" 是产量的期望值
    CASE WHEN r > mu_r + 2 * sd_r THEN 1 ELSE 0 END AS 是否超限,
    -- 同时给出固定阈值法(Q16/Q24)的判定, 报告要对比两条方法的差异
    CASE WHEN ue > mu_u + 2 * sd_u THEN 1 ELSE 0 END AS 固定阈值是否超限
FROM v_unit_energy_baseline
ORDER BY workshop_code, record_date;


-- @@name Q26_三种检测方法对比
-- @@desc 【#12 算法对比实验】固定 2σ 阈值法 / 产量基线残差法 / CUSUM 累积和法
--       的逐车间对比。三个口径都只取产量>0、剔除公用工程, 与 Q16/Q24 同源。
--
--       要回答的问题不是"哪个方法更好", 而是"判据形态决定了它看见什么":
--         · 固定 2σ —— 逐日截面, 比"今天 vs 该车间历史平均"。产量低的节假日
--           单耗必然抬升, 所以它的检出被日历主导。
--         · 产量基线 —— 逐日截面, 比"今天 vs 同产量同季节的期望值"。把产量与
--           季节吸收进模型后, 日历那部分应该大幅下降。
--         · CUSUM —— 累积判据, 比"最近一段时间是否系统性偏离"。它不看单日尖峰,
--           只看持续性, 所以会把前两者的散点检出归并成少数几段。
--
--       后三列量化 CUSUM 的白化必要性: 未白化最大S 与 白化最大S 的差, 就是
--       被当成持续偏移累积进去的季节伪影。
WITH j AS (
    SELECT
        b.workshop_code, b.record_date, b.workshop_name,
        b.ue, b.mu_u, b.sd_u, b.r, b.mu_r, b.sd_r,
        cu.s_hi, cu.s_lo, cu.s_hi_raw, cu.s_lo_raw, cu.is_alarm, cu.alarm_side,
        cal.is_weekend, cal.is_holiday,
        CASE WHEN cal.is_holiday = 1 THEN '节假日'
             WHEN cal.is_weekend = 1 THEN '周末' ELSE '工作日' END AS day_type,
        CASE WHEN b.ue > b.mu_u + 2 * b.sd_u THEN 1 ELSE 0 END AS hit_fixed,
        CASE WHEN b.r  > b.mu_r + 2 * b.sd_r THEN 1 ELSE 0 END AS hit_base
    FROM v_unit_energy_baseline b
    JOIN v_unit_energy_cusum cu
      ON cu.workshop_code = b.workshop_code AND cu.record_date = b.record_date
    JOIN dim_calendar cal ON cal.calendar_date = b.record_date
),
k AS (
    -- 报警段数 = is_alarm 由 0 变 1 的次数(需要先算出 is_alarm 才能取 LAG, 故分两步)
    SELECT j.*,
           CASE WHEN is_alarm = 1
                 AND COALESCE(LAG(is_alarm) OVER (PARTITION BY workshop_code
                                                  ORDER BY record_date), 0) = 0
                THEN 1 ELSE 0 END AS seg_start,
           CASE WHEN alarm_side = '偏高' THEN 1 ELSE 0 END AS hi_alarm,
           CASE WHEN alarm_side = '偏低' THEN 1 ELSE 0 END AS lo_alarm
    FROM j
)
SELECT
    k.workshop_code                           AS 车间编码,
    MAX(k.workshop_name)                      AS 车间,
    COUNT(*)                                  AS 样本天数,
    SUM(k.hit_fixed)                          AS 固定阈值检出,
    SUM(k.hit_base)                           AS 产量基线检出,
    SUM(k.is_alarm)                           AS 累计和报警天数,
    SUM(k.seg_start)                          AS 累计和报警段数,
    SUM(k.hi_alarm)                           AS 累计和上侧天数,
    SUM(k.lo_alarm)                           AS 累计和下侧天数,
    SUM(k.hit_fixed * k.hit_base)             AS 两法一致,
    SUM(k.hit_fixed * (1 - k.hit_base))       AS 仅固定阈值,
    SUM((1 - k.hit_fixed) * k.hit_base)       AS 仅产量基线,
    ROUND(100.0 * SUM(k.hit_fixed * (1 - k.hit_base)) / NULLIF(SUM(k.hit_fixed), 0), 1)
                                              AS 固定阈值中基线不认_pct,
    SUM(CASE WHEN k.hit_fixed = 1 AND k.day_type = '节假日' THEN 1 ELSE 0 END)
                                              AS 固定_节假日,
    SUM(CASE WHEN k.hit_base = 1 AND k.day_type = '节假日' THEN 1 ELSE 0 END)
                                              AS 基线_节假日,
    SUM(CASE WHEN k.is_alarm = 1 AND k.day_type = '节假日' THEN 1 ELSE 0 END)
                                              AS 累计和_节假日,
    ROUND(100.0 * SUM(CASE WHEN k.hit_fixed=1 AND k.day_type='节假日' THEN 1 ELSE 0 END)
          / NULLIF(SUM(k.hit_fixed), 0), 1)   AS 固定_节假日占比_pct,
    ROUND(100.0 * SUM(CASE WHEN k.hit_base=1 AND k.day_type='节假日' THEN 1 ELSE 0 END)
          / NULLIF(SUM(k.hit_base), 0), 1)    AS 基线_节假日占比_pct,
    ROUND(100.0 * SUM(CASE WHEN k.is_alarm=1 AND k.day_type='节假日' THEN 1 ELSE 0 END)
          / NULLIF(SUM(k.is_alarm), 0), 1)    AS 累计和_节假日占比_pct,
    ROUND(MAX(k.sd_u), 4)                     AS 固定阈值σ,
    ROUND(MAX(k.sd_r), 4)                     AS 残差σ,
    ROUND(MAX(k.sd_r) / NULLIF(MAX(k.sd_u), 0), 4) AS 噪声压低比,
    -- 两个最大 S 都按各自的 σ 标准化, 所以它们的差**不是**一个可加减的分解, 要结合
    -- 车间类型读: 连续型车间白化后 S 掉到三分之一(轧制 37.1→15.7), 那六成是季节伪影;
    -- 间歇型车间白化把 σ 也压小了, 按自身 σ 标准化后 S 反而略升, 说明它们的持续性
    -- 不是季节造成的, 而是节假日簇 —— 白化在它们身上去的是方差, 不是信号。
    ROUND(MAX(GREATEST(k.s_hi_raw, k.s_lo_raw)), 2) AS 未白化最大S,
    ROUND(MAX(GREATEST(k.s_hi, k.s_lo)), 2)         AS 白化最大S
FROM k
GROUP BY k.workshop_code
ORDER BY k.workshop_code;


-- @@name Q27_单耗CUSUM累积和序列
-- @@desc 【#11 CUSUM 累积和控制图】每个车间的 S⁺/S⁻ 逐日轨迹, 供报告画累积和控制图。
--
--       为什么要单列一条: Q26 给的是每车间一个汇总数, 而 CUSUM 的价值恰恰在
--       "什么时候开始累积、累积了多久"——只有日序列才看得出报警是孤立尖峰还是
--       一整段持续偏移。图上还要同时画未白化的轨迹作对照, 好直观看到季节伪影
--       是怎么被一路累积上去的。
--
--       报警判据见 v_unit_energy_cusum: h = 9.9σ(按 n=730 的零假设 99 分位标定,
--       不是教科书的 5σ)。
SELECT
    record_date                               AS 日期,
    workshop_code                             AS 车间编码,
    workshop_name                             AS 车间,
    ROUND(z_w, 3)                             AS 白化残差Z值,
    ROUND(s_hi, 3)                            AS 上侧累积和,
    ROUND(s_lo, 3)                            AS 下侧累积和,
    ROUND(s_hi_raw, 3)                        AS 上侧累积和_未白化,
    ROUND(s_lo_raw, 3)                        AS 下侧累积和_未白化,
    h                                         AS 判定限,
    is_alarm                                  AS 是否报警,
    alarm_side                                AS 报警方向
FROM v_unit_energy_cusum
ORDER BY workshop_code, record_date;


-- @@name Q28_各车间日度能耗与产量
-- @@desc 【#14 动态筛选的数据底座】把 车间 × 日 一行的"能耗 × 费用 × 碳排 × 产量 ×
--       日型"摊平导出, 供报告在浏览器里按日期区间 / 车间集合 / 日型重算 KPI、
--       趋势、结构与损耗。
--
--       为什么必须一条查询同时给出四个口径: 前端只能做加法和除法, 所以需要的是
--       **可加的量**, 而不是已经算好的比率。而比率不能靠"取一个全厂平均数再乘" ——
--       实测全厂 费用/能耗 的月际比值在 3214~3661 之间摆动(极差 13%), 不是常数,
--       乘一个固定系数会把筛选后的数字算歪。四个量在同一行里做聚合, 比值自然对。
--
--       为什么带日型: 报告原来靠 Q14 三行汇总做"工作日/周末/节假日"对比, 那三行
--       是按全期算好的, 一旦按日期或车间过滤就失效。日型是逐日属性, 必须回到
--       (车间×日) 这一层才能参与筛选, 所以在这里一并带出, 而不是另开一条查询 ——
--       另开一条要重复一遍同样的日期键, 白白多传 5,117 个日期字符串。
--
--       **口径: 全厂(含公用工程)**, 与 Q01/Q03/Q14 一致, 不剔除动力站。这一条很
--       关键: 筛选控件在"全区间+全部车间"下的结果必须与报告里原有那些写死的
--       数字**逐位相同**, 否则用户一动筛选就看到总量变了个数, 会以为控件算错了。
--       单耗类分析(异常/基线/CUSUM)另有 Q16/Q24/Q25/Q27, 它们自己剔除公用工程,
--       口径不受本条影响 —— 报告的"结构/趋势/风险"用全厂口径, "异常/基线"用
--       剔除口径, 这个分工本来就是报告既有的设计, 这里只是让筛选跟着它走。
--
--       产量: 用 LEFT JOIN 取。能耗列一定有值(事实表按 车间×日×能源 明细),
--       但车间当天可能没有产量记录, 那时 产量 为 NULL、单位产品能耗 也为 NULL ——
--       前端遇到 NULL 就当"这天没有产量口径", 不参与单耗统计, 而不是当成 0 产值。
--
--       为什么还要带 是否生产日: 待机损耗(Q13)按 is_production_day = 0 定义停产日,
--       而**这个标志不能由产量是否为 0 推出来** —— fact_production 对 8×731 个
--       车间日全都有记录且产量为正, 停产日的产量并不为 0(实测四个间歇型车间
--       各 58 个停产日, 按"产量=0"数出来是 0 天)。它只是能耗事实表上的一个标志位,
--       所以要原样带出来, 否则筛选后的待机损耗根本算不出。
SELECT
    d.record_date                             AS 日期,
    d.workshop_code                           AS 车间编码,
    MAX(d.workshop_name)                      AS 车间,
    MAX(w.process_type)                       AS 工序,
    MAX(CASE WHEN c.is_holiday = 1 THEN '节假日'
             WHEN c.is_weekend = 1 THEN '周末'
             ELSE '工作日' END)               AS 日型,
    MAX(d.prod)                               AS 是否生产日,
    -- 能耗/碳排保留 6 位: 前端要把 5,848 行加起来还原全厂口径, 每行只留 4 位的话
    -- 截断误差会累积到 0.07 tce —— 显示成两位小数时就是 63,759.70 vs 63,759.63,
    -- 一眼能看出的对不上。6 位把累积误差压到 1e-5 以下, 稳在显示精度之内。
    -- 费用本身是 DECIMAL, 求和精确, 两位足够, 不必跟着放。
    ROUND(SUM(d.tce), 6)                      AS 综合能耗_tce,
    ROUND(SUM(d.cost), 2)                     AS 能源费用_元,
    ROUND(SUM(d.co2), 6)                      AS 碳排放_tCO2,
    ROUND(MAX(p.output_qty), 3)               AS 产量,
    -- 单耗只在有产量的日子有意义; 产量为 0 或空时置 NULL, 交给前端跳过
    ROUND(SUM(d.tce) * 1000 / NULLIF(MAX(p.output_qty), 0), 4)
                                              AS 单位产品能耗_kgce
FROM (
    SELECT workshop_code, workshop_name, record_date,
           SUM(std_coal_kgce) / 1000 AS tce,
           SUM(cost)                 AS cost,
           SUM(co2_kg) / 1000        AS co2,
           -- 取 MIN: 只要有任一能源品种被标成非生产日, 这一天就算停产日。
           -- 与 v_daily_workshop 里 MAX(has_shutdown) 的取法同向(那边取 MAX
           -- 是因为用的补集标志), 保证与 Q13 数出来的停产天数一致。
           MIN(is_production_day)    AS prod
    FROM v_energy_enriched
    GROUP BY workshop_code, workshop_name, record_date
) d
JOIN dim_workshop w       ON w.workshop_code = d.workshop_code
JOIN dim_calendar c       ON c.calendar_date = d.record_date
LEFT JOIN fact_production p
       ON p.record_date = d.record_date AND p.workshop_code = d.workshop_code
GROUP BY d.record_date, d.workshop_code
ORDER BY d.workshop_code, d.record_date;


-- @@name Q29_各车间日度能耗结构
-- @@desc 【#14 动态筛选的数据底座, 结构用】日 × 车间 × 能源 的"实物量 × 折标煤 ×
--       费用 × 碳排"四度量立方, 供报告在筛选后重算"能源结构"。全厂口径(含公用工程)。
--
--       为什么不能省: Q04 只有全厂口径的 能源 汇总, 它已经把"日期"和"车间"两个维度
--       卷掉了 —— 一旦按日期区间或车间过滤, 两个维度要**同时**收缩, 预先聚合好的
--       表拼不回来。所以这里给最细的那一层, 让前端自己往任意方向卷。
--
--       **为什么是"日"而不是"月"**: 结构卡里要显示各能源的实物量与折标煤**绝对值**,
--       如果立方只到月, 用户选一个跨月中的区间(比如 3-15 ~ 5-10)时, 这张卡只能整月
--       计入, 于是它表里的合计与上方 KPI 的合计**对不上** —— 而这份报告的立身之本
--       就是每个数都能对上。代价是 19,006 行、紧凑编码后约 800 KB, 值得。
--       (只算占比的话月粒度就够, 但绝对值同样要展示。)
--
--       口径同样为**全厂(含公用工程)**, 与 Q28/Q04 一致 —— 全区间合计必须等于
--       Q04 的每一行, 否则筛选一动"结构"就和"总量"对不上。
SELECT
    e.record_date                             AS 日期,
    e.workshop_code                           AS 车间编码,
    e.energy_code                             AS 能源编码,
    -- 实物消耗量也要留 6 位, 理由与折标煤相同: 前端是把 19,006 行加起来还原 Q04 的。
    -- 只留 2 位的话每格最多带 0.005 的截断误差, 累加后天然气会差到 0.1 m³ 量级 ——
    -- 30,725,974.71 与 30,725,974.58 在表格里是**看得见**的两个数。
    ROUND(SUM(e.consumption), 6)              AS 实物消耗量,
    -- 折标煤与碳排同样保留 6 位, 理由见 Q28
    ROUND(SUM(e.std_coal_kgce) / 1000, 6)     AS 折标煤_tce,
    ROUND(SUM(e.cost), 2)                     AS 费用_元,
    ROUND(SUM(e.co2_kg) / 1000, 6)            AS 碳排放_tCO2
FROM v_energy_enriched e
GROUP BY e.record_date, e.workshop_code, e.energy_code
ORDER BY e.workshop_code, e.energy_code, e.record_date;


-- @@name Q24_单耗每日序列与2sigma带
-- @@desc 每个车间的每日单位产品能耗, 附该车间自身的均值与标准差, 供报告画
--       "单耗曲线 + 均值 ±2σ 带"并标出超限点。与 Q16 的分工: Q16 只返回
--       超限日(告警清单), 本查询返回完整序列(画图要的连续曲线与上下界)。
--       取值范围与 Q16 保持一致: 产量>0、剔除公用工程 —— 两者口径必须一致,
--       否则图上标出的点会比告警清单多/少。
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
    d.record_date               AS 日期,
    d.workshop_code             AS 车间编码,
    d.workshop_name             AS 车间,
    ROUND(d.output_qty, 3)      AS 产量,
    ROUND(d.ue, 4)              AS 单位产品能耗_kgce,
    ROUND(s.mu, 4)              AS 车间均值,
    ROUND(s.sd, 4)              AS 标准差,
    ROUND((d.ue - s.mu) / s.sd, 2) AS Z值,
    -- 超限标记在 SQL 里算好, 前端不重复实现阈值逻辑
    CASE WHEN d.ue > s.mu + 2 * s.sd THEN 1 ELSE 0 END AS 是否超限
FROM d
JOIN s ON s.workshop_code = d.workshop_code
ORDER BY d.workshop_code, d.record_date;
