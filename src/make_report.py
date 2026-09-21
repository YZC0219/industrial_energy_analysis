# -*- coding: utf-8 -*-
"""
make_report.py — 把 analysis.sql 导出的查询结果渲染成一份可视化报告

读取 output/ 下的 Q*.csv (由 import_mysql.py --run-analysis 生成),
组装成一份自包含的 HTML 报告 output/report.html —— 无外部依赖、无构建步骤,
数据以 JSON 内联在页面里, 双击即可打开。

用法:
  python src/make_report.py
  python src/import_mysql.py --run-analysis && python src/make_report.py
"""

import csv
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(BASE_DIR, "output")
TEMPLATE = os.path.join(BASE_DIR, "src", "report_template.html")
TARGET = os.path.join(OUT_DIR, "report.html")

Q = {  # 查询名 -> output/ 下的文件名
    "totals":      "Q01_能源消费总览.csv",
    "totals_ex":   "Q02_剔除公用工程后的能耗总览.csv",
    "workshops":   "Q03_各车间综合能耗排名.csv",
    "mix":         "Q04_能源结构_折标煤与费用双口径.csv",
    "monthly":     "Q05_月度能耗趋势与环比.csv",
    "yoy":         "Q06_各车间能耗同比.csv",
    "unit_month":  "Q07_各车间单位产品能耗月度趋势.csv",
    "unit_yoy":    "Q08_单位产品能耗同比_节能改造效果.csv",
    "temp_bins":   "Q11_气温与能耗的关系_分档.csv",
    "temp_corr":   "Q12_气温与各能源相关系数.csv",
    "standby":     "Q13_停产日待机损耗分析.csv",
    "daytype":     "Q14_工作日与周末节假日能耗对比.csv",
    "carbon":      "Q21_碳排放强度.csv",
    "alerts":      "Q22_能耗突增预警_环比超25pct.csv",
    "unit_series": "Q24_单耗每日序列与2sigma带.csv",
    "baseline":    "Q25_产量基线单耗期望值.csv",
    "cmp":         "Q26_三种检测方法对比.csv",
    "cusum":       "Q27_单耗CUSUM累积和序列.csv",
    "daily_ws":    "Q28_各车间日度能耗与产量.csv",
    "energy_daily": "Q29_各车间日度能耗结构.csv",
}


def read(name: str):
    """读取一条查询结果, 返回 list[dict] (utf-8-sig, 去多余空白)"""
    path = os.path.join(OUT_DIR, name)
    if not os.path.exists(path):
        raise SystemExit(f"[错误] 找不到 {name}, 请先执行: python src/import_mysql.py --run-analysis")
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [
            {(k or "").strip(): (v or "").strip() for k, v in row.items()}
            for row in csv.DictReader(f)
        ]


def f(v, default=0.0) -> float:
    """空串 -> default, 其余转 float (DATE/空值都可能出现)"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def opt(v):
    """可空数值: 空串返回 None, 便于 JSON 序列化成 null"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def build() -> dict:
    totals = read(Q["totals"])[0]
    totals_ex = read(Q["totals_ex"])[0]
    corr = read(Q["temp_corr"])[0]

    # ---- Q01/Q02 总览 ----
    out = {
        "totals": {
            "days": int(f(totals["统计天数"])),
            "tce": f(totals["综合能耗_tce"]),
            "cost": f(totals["能源费用_元"]),
            "co2": f(totals["碳排放_tCO2"]),
            "tce_ex": f(totals_ex["综合能耗_tce"]),
        },
    }

    # ---- Q03 车间排名 ----
    out["workshops"] = [
        {
            "code": r["车间编码"],
            "name": r["车间"],
            "process": r["工序"],
            "tce": f(r["综合能耗_tce"]),
            "tce_pct": f(r["能耗占比_pct"]),
            "cost": f(r["能源费用_元"]),
            "cost_pct": f(r["费用占比_pct"]),
            "co2": f(r["碳排放_tCO2"]),
            # 动力站属公用工程, 其蒸汽被其他车间二次消费, 全厂口径会重复计入
            "utility": r["工序"] == "公用工程",
        }
        for r in read(Q["workshops"])
    ]

    # ---- Q04 能源结构双口径 ----
    out["energy_mix"] = [
        {
            "name": r["能源"],
            "unit": r["单位"],
            "qty": f(r["实物消耗量"]),
            "tce": f(r["折标煤_tce"]),
            "tce_pct": f(r["折标煤占比_pct"]),
            "cost": f(r["费用_元"]),
            "cost_pct": f(r["费用占比_pct"]),
            "co2": f(r["碳排放_tCO2"]),
            "price": f(r["实际均价"]),
        }
        for r in read(Q["mix"])
    ]

    # ---- Q05 + Q21 月度趋势 (碳排强度按年月并入) ----
    ci = {r["年月"]: f(r["碳排强度_tCO2每tce"]) for r in read(Q["carbon"])}
    out["monthly"] = [
        {
            "ym": r["年月"],
            "tce": f(r["综合能耗_tce"]),
            "cost": f(r["能源费用_元"]),
            "co2": f(r["碳排放_tCO2"]),
            "mom": opt(r["环比_pct"]),
            "ci": ci.get(r["年月"], 0.0),
        }
        for r in read(Q["monthly"])
    ]

    # ---- Q07 产量: 按 车间 x 年份 汇总, 得到各车间产量同比 ----
    # 注意: 各车间产量单位不同(吨/件/台), 跨车间求和没有意义, 只能车间内纵向比
    prod = {}   # (车间, 年份) -> 产量
    for r in read(Q["unit_month"]):
        ym = r["年月"]
        key = (r["车间"], ym[:4])
        prod[key] = prod.get(key, 0.0) + f(r["产量"])
    growth = {}
    for (ws, yr), qty in prod.items():
        if yr == "2024" and qty > 0:
            g = (prod.get((ws, "2025"), 0.0) - qty) / qty * 100.0
            growth[ws] = round(g, 2)

    # ---- Q08 单耗同比 ----
    out["unit_yoy"] = [
        {
            "name": r["车间"],
            "y2024": f(r["单耗2024"]),
            "y2025": f(r["单耗2025"]),
            "pct": f(r["单耗同比_pct"]),
        }
        for r in read(Q["unit_yoy"])
    ]

    # ---- 增速差图: 同一批车间、同一单位(%), 产量同比 vs 单耗同比 ----
    out["deco"] = [
        {"name": u["name"], "a": growth.get(u["name"], 0.0), "b": u["pct"]}
        for u in out["unit_yoy"]
        if u["name"] in growth
    ]

    # ---- Q11/Q12 气温 ----
    out["temp_bins"] = [
        {
            "label": r["气温区间"],
            "days": int(f(r["天数"])),
            "avg_t": f(r["区间均温"]),
            "elec": f(r["日均电耗_kWh"]),
            "gas": f(r["日均气耗_m3"]),
            "steam": f(r["日均蒸汽_t"]),
        }
        for r in read(Q["temp_bins"])
    ]
    out["temp_corr"] = {
        "n": int(f(corr["样本天数"])),
        "elec": f(corr["电力_r"]),
        "gas": f(corr["天然气_r"]),
        "steam": f(corr["蒸汽_r"]),
    }

    # ---- Q13 待机损耗: 只保留真正有停产日的车间 ----
    standby = [
        {
            "name": r["车间"],
            "days": int(f(r["停产天数"])),
            "tce": f(r["待机能耗_tce"]),
            "total": f(r["综合能耗_tce"]),
            "pct": f(r["待机占比_pct"]),
            "waste": f(r["待机浪费_元"]),
        }
        for r in read(Q["standby"])
        if f(r["停产天数"]) > 0
    ]
    standby.sort(key=lambda r: r["tce"], reverse=True)
    out["standby"] = standby

    # ---- Q14 日型对比: 按 工作日 -> 周末 -> 节假日 排序 ----
    order = {"工作日": 0, "周末": 1, "法定节假日": 2}
    daytype = [
        {
            "name": r["日型"].split("_")[-1],
            "days": int(f(r["天数"])),
            "tce": f(r["日均能耗_tce"]),
            "cost": f(r["日均费用_元"]),
            "co2": f(r["日均碳排_tCO2"]),
        }
        for r in read(Q["daytype"])
    ]
    daytype.sort(key=lambda r: order.get(r["name"], 9))
    out["daytype"] = daytype

    # ---- Q22 突增预警 ----
    out["alerts"] = [
        {
            "ym": r["年月"],
            "ws": r["车间"],
            "cur": f(r["当月能耗_tce"]),
            "prev": f(r["上月能耗_tce"]),
            "mom": f(r["环比_pct"]),
        }
        for r in read(Q["alerts"])
    ]

    # ---- Q24 单耗每日序列: 按车间分组, 每组自带该车间的均值/标准差 ----
    # 每个车间一个独立的 μ/σ, 所以不能合并成一张图(量级差三个数量级), 按车间分组
    groups: dict = {}
    for r in read(Q["unit_series"]):
        code = r["车间编码"]
        g = groups.setdefault(code, {
            "code": code,
            "name": r["车间"],
            "mu": f(r["车间均值"]),
            "sd": f(r["标准差"]),
            "points": [],
        })
        g["points"].append({
            "d": r["日期"],
            "ue": f(r["单位产品能耗_kgce"]),
            "z": f(r["Z值"]),
            "qty": f(r["产量"]),
            "out": r["是否超限"] == "1",
        })
    series = sorted(groups.values(), key=lambda g: -len([p for p in g["points"] if p["out"]]))
    for g in series:
        g["n_out"] = len([p for p in g["points"] if p["out"]])
        # σ/μ 越大, Z 值越容易被小基数放大 —— 页面上要按这个比值排序提示
        g["cv"] = round(g["sd"] / g["mu"] * 100, 1) if g["mu"] else 0.0
    out["unit_series"] = series

    # ---- Q25 产量基线: 按车间分组, 每日带上期望值/残差与两法判定 ----
    # 与 Q24 同构(按车间分组), 但每条点多了 yhat(期望单耗) 与两法是否超限的标记,
    # 供报告在同一张图上对比"固定阈值"与"产量基线"两条判据。
    bgroups: dict = {}
    for r in read(Q["baseline"]):
        code = r["车间编码"]
        g = bgroups.setdefault(code, {
            "code": code,
            "name": r["车间"],
            "mu_r": f(r["残差均值"]),
            "sd_r": f(r["残差标准差"]),
            "points": [],
        })
        g["points"].append({
            "d": r["日期"],
            "ue": f(r["实际单耗_kgce"]),
            "yhat": f(r["期望单耗_kgce"]),
            "res": f(r["单耗残差_kgce"]),
            "z": f(r["Z值"]),
            "qty": f(r["产量"]),
            "out": r["是否超限"] == "1",
            "out_fixed": r["固定阈值是否超限"] == "1",
        })
    bseries = sorted(bgroups.values(), key=lambda g: g["code"])
    for g in bseries:
        g["n_out"] = len([p for p in g["points"] if p["out"]])
        g["n_fixed"] = len([p for p in g["points"] if p["out_fixed"]])
        g["n_both"] = len([p for p in g["points"] if p["out"] and p["out_fixed"]])
    out["baseline"] = bseries

    # ---- Q26 三法对比(逐车间一行) ----
    out["cmp"] = [
        {
            "code": r["车间编码"],
            "name": r["车间"],
            "n_days": int(f(r["样本天数"])),
            "n_fixed": int(f(r["固定阈值检出"])),
            "n_base": int(f(r["产量基线检出"])),
            "n_cusum": int(f(r["累计和报警天数"])),
            "n_seg": int(f(r["累计和报警段数"])),
            "cusum_hi": int(f(r["累计和上侧天数"])),
            "cusum_lo": int(f(r["累计和下侧天数"])),
            "n_both": int(f(r["两法一致"])),
            "only_fixed": int(f(r["仅固定阈值"])),
            "only_base": int(f(r["仅产量基线"])),
            "fixed_reject_pct": f(r["固定阈值中基线不认_pct"]),
            "fixed_holiday": int(f(r["固定_节假日"])),
            "base_holiday": int(f(r["基线_节假日"])),
            "cusum_holiday": int(f(r["累计和_节假日"])),
            "fixed_holiday_pct": f(r["固定_节假日占比_pct"]),
            "base_holiday_pct": f(r["基线_节假日占比_pct"]),
            "cusum_holiday_pct": f(r["累计和_节假日占比_pct"]),
            "sd_fixed": f(r["固定阈值σ"]),
            "sd_res": f(r["残差σ"]),
            "noise_ratio": f(r["噪声压低比"]),
            "s_raw": f(r["未白化最大S"]),
            "s_white": f(r["白化最大S"]),
        }
        for r in read(Q["cmp"])
    ]

    # ---- Q27 CUSUM 日序列: 按车间分组, 供报告画累积和控制图 ----
    # 每条点带白化/未白化两套 S 轨迹 —— 图上并排画, 好直观看到季节伪影是怎么
    # 被一路累积上去的(连续型车间尤其明显)。
    cgroups: dict = {}
    for r in read(Q["cusum"]):
        code = r["车间编码"]
        g = cgroups.setdefault(code, {
            "code": code, "name": r["车间"], "h": f(r["判定限"]), "points": [],
        })
        g["points"].append({
            "d": r["日期"],
            "z": f(r["白化残差Z值"]),
            "hi": f(r["上侧累积和"]),
            "lo": f(r["下侧累积和"]),
            "hi_raw": f(r["上侧累积和_未白化"]),
            "lo_raw": f(r["下侧累积和_未白化"]),
            "alarm": r["是否报警"] == "1",
            "side": r["报警方向"],
        })
    cseries = sorted(cgroups.values(), key=lambda g: g["code"])
    for g in cseries:
        g["n_alarm"] = len([p for p in g["points"] if p["alarm"]])
        g["n_hi"] = len([p for p in g["points"] if p["side"] == "偏高"])
        g["n_lo"] = len([p for p in g["points"] if p["side"] == "偏低"])
        # 报警段数 = 报警状态由 0 变 1 的次数。Q26 里 SQL 已按同样口径算过一份,
        # 这里再算一遍是为了让 CUSUM 图和副标题能独立于 Q26 渲染 —— 两处口径一致。
        seg = 0
        for j, p in enumerate(g["points"]):
            if p["alarm"] and not (g["points"][j - 1]["alarm"] if j else False):
                seg += 1
        g["n_seg"] = seg
        g["max_hi"] = max((p["hi"] for p in g["points"]), default=0.0)
        g["max_lo"] = max((p["lo"] for p in g["points"]), default=0.0)
        g["max_raw"] = max((max(p["hi_raw"], p["lo_raw"]) for p in g["points"]), default=0.0)
        g["side"] = "偏低" if g["n_lo"] > g["n_hi"] else ("偏高" if g["n_hi"] else "无")
    out["cusum"] = cseries

    # ---- Q28/Q29 前端动态筛选用的两套序列 ----
    # #14 日期/车间筛选: 页面要在浏览器里按「日期区间 × 车间 × 日型」重算上面的图。
    # 但页面拿不到 fact 表, 只有这些 CSV —— 所以这里先把"可加的量"摊平导出,
    # 让前端能自己聚合。两条硬约束:
    #   1. 综合能耗/费用/碳排放必须由**同一行**带出(不能各取一个全厂数再拼),
    #      否则筛选后两个口径的比值会自相矛盾(实测全厂费用/能耗月际比值在
    #      3214~3658 之间摆动 13.8%, 不是常数, 乘一个比例常数会算错)。
    #   2. 按**全厂口径**导出, 即含动力站(W07 公用工程)。这一条是刻意的: 报告里
    #      "能源结构"一节的正文本身就在讲"全厂口径把动力站二次计入了", 若筛选器
    #      悄悄换成剔除口径, 一碰筛选 KPI 就会掉一块, 与正文自相矛盾。所以筛选
    #      前后都是同一个口径 —— Q28 求和 = Q01/Q03/Q05/Q14, Q29 求和 = Q04,
    #      逐个核对过(误差 < 0.01, 全部来自 SQL 里的显示位取整)。
    #      "剔除公用工程"那个口径仍然只由 Q02 单独提供, 不在筛选器覆盖范围内。
    # 两套都按 车间 × (日期|能源) 字典序排; 前端只按区间/集合做子集取值, 不重排。

    # Q29 只给最细的 (日期 × 车间 × 能源) 一层, 车间名/工序/能源名/单位都不在 SQL
    # 里 —— 这是**刻意的**: 19,006 行每行重复一遍名称会把内联 JSON 撑大一倍多,
    # 而名称只有 8 种车间 × 6 种能源。所以这里从前面的结果里取出两张小字典,
    # 让前端按编码查名字。字典的权威来源与 Q03/Q04 同源, 不另立一套。
    ws_meta = {w["code"]: {"name": w["name"], "proc": w["process"]} for w in out["workshops"]}
    # 能源编码 -> 名称/单位。别名表是"标准名"的唯一权威(见 clean_data.ENERGY_NAME),
    # 不在这里另写一份字面量; 单位取清洗阶段定的标准单位。
    from clean_data import CANONICAL_UNIT, ENERGY_NAME

    out["energy_daily"] = [
        {
            "d": r["日期"],
            "code": r["车间编码"],
            "e": r["能源编码"],
            "qty": f(r["实物消耗量"]),
            "tce": f(r["折标煤_tce"]),
            "cost": f(r["费用_元"]),
            "co2": f(r["碳排放_tCO2"]),
        }
        for r in read(Q["energy_daily"])
    ]
    # 前端渲染图例/表头要用的两张查找表, 与立方体分开传, 避免逐行重复
    out["ws_meta"] = ws_meta
    out["en_meta"] = {
        code: {"name": ENERGY_NAME.get(code, code), "unit": CANONICAL_UNIT.get(code, "")}
        for code in sorted({r["e"] for r in out["energy_daily"]})
    }

    # (2) 车间 × 日: KPI 总量、日型对比、生产日/待机这些**不含能源维度**的口径走这一份。
    # 为什么不从上面的立方体现算: 立方体是 (日×车间×能源) 三层, 按车间汇总要再
    # 遍历 19,006 行; 而 Q28 已经是 5,848 行的一层汇总, 且**带日型与是否生产日**
    # —— 那两个字段立方体里没有(能源没有"日型")。前端筛选一碰日型就必须有这一份。
    # 口径同 Q28: 全厂(含公用工程), 所以全区间+全部车间时 sum(tce) == Q01/Q03。
    out["ws_daily"] = [
        {
            "d": r["日期"],
            "code": r["车间编码"],
            "day": r["日型"],                       # 工作日 / 周末 / 节假日
            "prod": r["是否生产日"] == "1",
            "tce": f(r["综合能耗_tce"]),
            "cost": f(r["能源费用_元"]),
            "co2": f(r["碳排放_tCO2"]),
            "qty": f(r["产量"]),
            # 单耗为 0 时 SQL 会给 NULL(停产日), 用 opt 保留 null 而不是折成 0 ——
            # 折成 0 会让"停产日单耗=0"混进均值, 把筛选后的单耗算低。
            "ue": opt(r["单位产品能耗_kgce"]),
        }
        for r in read(Q["daily_ws"])
    ]

    # 前端筛选栏的区间锚点。取数据里的首末日, 而不是"报告生成日" ——
    # 报告是离线快照, 用今天当锚点会筛出一个空集。
    out["filter_meta"] = {
        "min_date": min(r["d"] for r in out["ws_daily"]),
        "max_date": max(r["d"] for r in out["ws_daily"]),
    }

    # ---- 由数据推出的派生结论, 供页面文案直接引用 ----
    out["derived"] = {
        "utility_ratio": round(out["totals"]["tce_ex"] / out["totals"]["tce"] * 100, 1),
        "standby_tce": round(sum(r["tce"] for r in out["standby"]), 2),
        "standby_cost": round(sum(r["waste"] for r in out["standby"]), 2),
        "top4_pct": round(sum(w["tce_pct"] for w in out["workshops"][:4]), 1),
        "us_total": sum(g["n_out"] for g in series),
        "us_pts": sum(len(g["points"]) for g in series),
        # 变异系数最高的车间 —— 小基数放大的典型案例, 文案里点名
        "us_cv_top": max(series, key=lambda g: g["cv"])["name"] if series else "",
        # 产量基线法的效果指标, 全部从 Q25/Q26 算出, 不写死
        "bl_fixed": sum(c["n_fixed"] for c in out["cmp"]),
        "bl_base": sum(c["n_base"] for c in out["cmp"]),
        "bl_both": sum(c["n_both"] for c in out["cmp"]),
        # 基线法整体把残差噪声压到原始 σ 的百分之几(按车间平均)
        "bl_noise_pct": round(
            sum(c["noise_ratio"] for c in out["cmp"]) / len(out["cmp"]) * 100, 1
        ) if out["cmp"] else 0.0,
        # 固定阈值法检出中被基线法否掉的比例(按检出量加权)
        "bl_reject_pct": round(
            100.0 * sum(c["only_fixed"] for c in out["cmp"])
            / max(1, sum(c["n_fixed"] for c in out["cmp"])), 1
        ),
        # 节假日占检出的比例, 两法各一个 —— 对比"误报结构"的关键论据
        "bl_fixed_holiday_pct": round(
            100.0 * sum(c["fixed_holiday"] for c in out["cmp"])
            / max(1, sum(c["n_fixed"] for c in out["cmp"])), 1
        ),
        "bl_base_holiday_pct": round(
            100.0 * sum(c["base_holiday"] for c in out["cmp"])
            / max(1, sum(c["n_base"] for c in out["cmp"])), 1
        ),
        # CUSUM 把逐日散点压成持续段的效果 —— #11 的核心论据
        "cs_days": sum(c["n_cusum"] for c in out["cmp"]),
        "cs_segs": sum(c["n_seg"] for c in out["cmp"]),
        "cs_per_seg": round(
            sum(c["n_cusum"] for c in out["cmp"])
            / max(1, sum(c["n_seg"] for c in out["cmp"])), 1
        ),
        # 同一份数据, 2σ 报 251 个散点, CUSUM 只报 11 段 —— 压缩倍数
        "cs_compress": round(
            sum(c["n_fixed"] for c in out["cmp"])
            / max(1, sum(c["n_cusum"] for c in out["cmp"])), 1
        ),
        "cs_holiday_pct": round(
            100.0 * sum(c["cusum_holiday"] for c in out["cmp"])
            / max(1, sum(c["n_cusum"] for c in out["cmp"])), 1
        ),
        # 报警方向按车间分裂: 持续偏低 / 持续偏高 各是哪几个车间。
        # 这个分裂本身就是结论 —— 连续型与间歇型车间的残差结构不同,
        # 所以列出来给文案点名, 而不是只报一个总数。
        "cs_lo_names": "、".join(c["name"] for c in out["cmp"] if c["cusum_lo"] > c["cusum_hi"]),
        "cs_hi_names": "、".join(c["name"] for c in out["cmp"] if c["cusum_hi"] > c["cusum_lo"]),
        "cs_n_lo": len([c for c in out["cmp"] if c["cusum_lo"] > c["cusum_hi"]]),
        "cs_n_hi": len([c for c in out["cmp"] if c["cusum_hi"] > c["cusum_lo"]]),
        # 白化把连续型车间的累积和峰值砍掉的比例(取三个降幅最大的平均)
        "cs_white_cut_pct": round(
            100.0 * sum(1 - c["s_white"] / c["s_raw"] for c in out["cmp"]
                        if c["s_white"] < c["s_raw"] * 0.6)
            / max(1, len([c for c in out["cmp"] if c["s_white"] < c["s_raw"] * 0.6])), 1
        ),
        # ---- 正文散文里引用的数字, 一律从这里取, 不在模板里手写 ----
        # 峰谷月与碳排强度极值: 正文要点名"哪个月最高", 所以连年月一起带出来,
        # 否则数字随数据变了、月份还停在旧值, 读者对不上。
        "tce_peak_ym": max(out["monthly"], key=lambda r: r["tce"])["ym"],
        "tce_peak": max(out["monthly"], key=lambda r: r["tce"])["tce"],
        "tce_trough_ym": min(out["monthly"], key=lambda r: r["tce"])["ym"],
        "tce_trough": min(out["monthly"], key=lambda r: r["tce"])["tce"],
        "ci_max": max(out["monthly"], key=lambda r: r["ci"])["ci"],
        "ci_min": min(out["monthly"], key=lambda r: r["ci"])["ci"],
        # 日型对比: 周末/节假日相对工作日的百分比, 正文用整数百分数叙述。
        # 名字取自 Q14 的 "1_法定节假日".split("_")[-1] == "法定节假日"
        # (前端 chip 上显示的 "节假日" 是缩短后的标签, 不是这里的数据键)
        "dt_weekend_pct": round(
            100.0 * next(r["tce"] for r in out["daytype"] if r["name"] == "周末")
            / next(r["tce"] for r in out["daytype"] if r["name"] == "工作日")
        ),
        "dt_holiday_pct": round(
            100.0 * next(r["tce"] for r in out["daytype"] if r["name"] == "法定节假日")
            / next(r["tce"] for r in out["daytype"] if r["name"] == "工作日")
        ),
        # 告警区正文要引用"11 月全厂环比"作为背景, 两个年份各一个。
        # 按 ym 的 "-11" 后缀取, 而不是写死 2024-11/2025-11 —— 换数据区间也不用改。
        "mom_nov": {r["ym"]: r["mom"] for r in out["monthly"] if r["ym"].endswith("-11")},
    }
    return out


def main() -> None:
    data = build()
    with open(TEMPLATE, encoding="utf-8") as fh:
        html = fh.read()

    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    # 占位符是一段区间: /*__DATA__*/ ... /*__END__*/
    # 模板里该区间内是 null, 所以模板单独打开也是合法 JS(不会因语法错误整页脚本失效);
    # 生成时把整段区间换成 JSON。只替换第一个区间, 避免误伤正文里出现的同样字样。
    start, end = "/*__DATA__*/", "/*__END__*/"
    i = html.find(start)
    j = html.find(end, i + len(start)) if i != -1 else -1
    if i == -1 or j == -1:
        raise SystemExit(f"[错误] 模板 {TEMPLATE} 缺少 {start} ... {end} 占位区间")
    html = html[:i + len(start)] + payload + html[j + len(end):]

    with open(TARGET, "w", encoding="utf-8") as fh:
        fh.write(html)

    d = data["derived"]
    print(f"[完成] 报告已生成 -> {os.path.relpath(TARGET, BASE_DIR)}")
    print(f"       期间      {data['monthly'][0]['ym']} ~ {data['monthly'][-1]['ym']}"
          f" ({data['totals']['days']} 天)")
    print(f"       综合能耗  {data['totals']['tce']:,.2f} tce"
          f" (剔除公用工程 {data['totals']['tce_ex']:,.2f} tce, "
          f"{d['utility_ratio']}%)")
    print(f"       能源费用  ¥{data['totals']['cost']:,.2f}")
    print(f"       碳排放    {data['totals']['co2']:,.2f} tCO2")
    print(f"       图表数据  {len(data['workshops'])} 车间 / {len(data['energy_mix'])} 能源 / "
          f"{len(data['monthly'])} 月 / {len(data['temp_bins'])} 气温档 / "
          f"{len(data['standby'])} 待机车间 / {len(data['alerts'])} 条预警")
    print(f"       单耗序列  {len(data['unit_series'])} 车间 / {d['us_pts']} 个日点 / "
          f"{d['us_total']} 个超限点 (变异系数最大: {d['us_cv_top']})")
    print(f"       产量基线  固定阈值检出 {d['bl_fixed']} / 基线检出 {d['bl_base']} / "
          f"两法一致 {d['bl_both']} (残差噪声压到 {d['bl_noise_pct']}%)")
    print(f"       误报结构  固定阈值检出中节假日占 {d['bl_fixed_holiday_pct']}%, "
          f"基线法占 {d['bl_base_holiday_pct']}%")
    print(f"       CUSUM     报警 {d['cs_days']} 天 / {d['cs_segs']} 段 "
          f"(平均 {d['cs_per_seg']} 天/段, 相对 2σ 的 {d['bl_fixed']} 个散点压缩 "
          f"{d['cs_compress']} 倍)")
    print(f"       报警方向  持续偏低 {d['cs_n_lo']} 个 ({d['cs_lo_names']}) / "
          f"持续偏高 {d['cs_n_hi']} 个 ({d['cs_hi_names']}); "
          f"白化把累积和峰值平均砍掉 {d['cs_white_cut_pct']}%")


if __name__ == "__main__":
    main()
