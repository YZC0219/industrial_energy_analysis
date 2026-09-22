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

import calendar
import collections
import csv
import json
import os
import statistics
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

    # ---- 节能量: 把"单耗下降"翻译成 tce / tCO2 / 元 ----
    # 口径: 以 2024 单耗为基线, 乘 2025 实际产量 = "效率不提升本该消耗多少",
    # 再减 2025 实际能耗。差额才是**可归因于能效提升**的部分 ——
    # 产量增长带来的能耗不算功劳, 只有效率提升省下的才算。
    #
    # 单位换算: Q08 的单耗是 kgce/产量单位, 产量单位随车间变(吨/件/台/平方米),
    # 所以 qty * kgce / 1000 = tce, **只在同一车间内成立**(跨车间相加无意义)。
    # 但这里加总的是"节能量", 它和产量不同 —— tce 是可加的, 七个车间的节能量
    # 相加有物理意义(都是标煤吨数)。
    save_rows = []
    for u in out["unit_yoy"]:
        q25 = prod.get((u["name"], "2025"), 0.0)
        if q25 <= 0:
            continue
        should = q25 * u["y2024"] / 1000.0     # kgce -> tce
        actual = q25 * u["y2025"] / 1000.0
        save_rows.append({
            "name": u["name"],
            "save": round(should - actual, 1),
            "pct": u["pct"],
        })
    total_save = sum(r["save"] for r in save_rows)

    # 折算系数从**全期实测**取, 不写死: 碳强度 = 总碳排/总能耗,
    # 单位能耗成本 = 总费用/总能耗。换一批数据这两个数会变, 正文跟着变。
    ci_all = out["totals"]["co2"] / out["totals"]["tce"] if out["totals"]["tce"] else 0.0
    cost_per_tce = out["totals"]["cost"] / out["totals"]["tce"] if out["totals"]["tce"] else 0.0
    # 年度基准: 2025 全厂能耗, 用来算节能量占全厂能耗的比例
    tce_2025 = sum(r["tce"] for r in out["monthly"] if r["ym"].startswith("2025"))

    save_rows.sort(key=lambda r: -r["save"])
    out["savings"] = save_rows
    out["save_total"] = round(total_save, 1)
    out["save_co2"] = round(total_save * ci_all, 1)
    out["save_cost"] = round(total_save * cost_per_tce, 0)
    out["save_pct"] = round(total_save / tce_2025 * 100.0, 2) if tce_2025 else 0.0
    # 排名前两位的车间合计占比 —— 正文用它说明"高单耗车间杠杆最大"
    top2 = sum(r["save"] for r in save_rows[:2])
    out["save_top2_pct"] = round(top2 / total_save * 100.0, 1) if total_save else 0.0

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
        # 偏低那几段的单日 |Z| 范围 —— 正文用它论证"不是某天极端值, 是连续多天
        # 累积推过线"。只取偏低侧的日子, 全部取绝对值: 早先手写 "0.86 ~ 2.35",
        # 下界错了(实为 0.11), 而 0.86 恰好是集合里的一员, 所以看着像对的。
        **_cusum_lo_z(out["cusum"]),
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
        # 正文要说清"11 月环比含月长效应, 剔除后真实抬升更强", 所以把剔除后的数
        # 也一并算出来 —— 光有原始环比, 读者无法判断那 3.9pp 有多大。
        # 口径与 tools/decompose_mom.py 一致(日均环比), 两处算法必须同步修改。
        "mom_nov_daily": _mom_daily_nov(out["monthly"]),
        # ---- 2026-09-22 补: 三处"图表脚注/检测卡片"里漏掉的手写数字 ----
        # 这批本来该在 A 类改造里一起插值, 但当时是按"卡片"扫的, 漏了图表脚的
        # 脚注(§4 气温关系)。事后核对发现它们早已与查询对不上(相关系数三个
        # 全偏、Q26 的两法对比整句都错), 正是 A 类改造要防的那类漂移。
        #
        # 按名字取车间, 而不是按下标 —— Q26 的行序由 SQL 的 ORDER BY 决定,
        # 拿 out["cmp"][i] 会在加/减车间时静默错位到别的车间身上。
        **{
            f"corr_{k}": out["temp_corr"][k]
            for k in ("elec", "gas", "steam")
        },
        **_cmp_named(out["cmp"]),
        **_clean_volumes(),
    }
    return out


def _mom_daily_nov(monthly: list) -> dict:
    """按**日均**重算 11 月的环比 —— 把月长效应除掉。

    Q05/Q22 的环比是"整月总量之比", 相邻两月天数不同（28~31 天）时, 天数差
    会混进结果里: 11 月 30 天、上月 31 天, 于是原始环比被**压低**约 3.9pp。
    日均环比把两个月都化成"每天多少 tce"再比, 天数影响即被约掉。

    **符号与直觉相反**: 11 月比上月短, 所以月长对它是负向的, 真实抬升比
    原始值**更强**。别照着"31 天的月更高"去推 11 月。

    返回 {"2024-11": 21.00, ...}, 键与 mom_nov 一致以便正文并排引用。
    """
    bym = {r["ym"]: r["tce"] for r in monthly}
    out = {}
    for ym in sorted(bym):
        if not ym.endswith("-11"):
            continue
        y, m = map(int, ym.split("-"))
        prev = f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"
        if prev not in bym:
            continue
        days = calendar.monthrange(y, m)[1]
        pdays = calendar.monthrange(*map(int, prev.split("-")))[1]
        prev_daily = bym[prev] / pdays
        out[ym] = (bym[ym] / days - prev_daily) / prev_daily * 100
    return out


def _cusum_lo_z(cusum: list) -> dict:
    """偏低(S⁻)那些报警日的单日 |Z| 范围。

    正文用它论证"偏低不是某天出现了极端值, 而是连续多天的小幅偏低把累积和
    推过了线"。所以取的是**偏低侧报警日**的 |Z|, 不是全表 Z 的极值 ——
    混用会让结论反过来(全表有 +19.62 的春节效应, 那是偏高侧)。
    """
    zs = [abs(p["z"]) for g in cusum for p in g["points"] if p["side"] == "偏低"]
    return {
        "cusum_lo_z_lo": round(min(zs), 2) if zs else 0,
        "cusum_lo_z_hi": round(max(zs), 2) if zs else 0,
    }


def _cmp_named(cmp: list) -> dict:
    """把 Q26 的逐车间对比拆成正文要用的具名键。

    **按名字取, 不按下标**。Q26 的行序由 SQL 的 ORDER BY 决定, 正文里每一处
    点名的都是具体车间("熔炼 21 vs 6")—— 用下标会在加/减车间时静默指到别人
    身上, 而且数字仍然"看起来合理"。
    """
    by = {c["name"]: c for c in cmp}
    out = {}
    for name in ("熔炼车间", "轧制车间", "装配车间", "表面处理车间", "机加工车间"):
        if name not in by:
            continue
        c = by[name]
        # 正文用短名(不带"车间"), 与图例和点位一致
        short = name.replace("车间", "")
        out[f"cmp_{short}_fixed"] = c["n_fixed"]
        out[f"cmp_{short}_base"] = c["n_base"]
        out[f"cmp_{short}_both"] = c["n_both"]
        out[f"cmp_{short}_holiday"] = c["fixed_holiday"]
    out["cmp_noise_min"] = min(c["noise_ratio"] for c in cmp) if cmp else 0
    out["cmp_noise_max"] = max(c["noise_ratio"] for c in cmp) if cmp else 0
    # 白化降幅最大的两个车间(连续型) —— 正文要指名道姓地举例
    cut = sorted(
        (c for c in cmp if c["s_raw"] > 0),
        key=lambda c: c["s_white"] / c["s_raw"],
    )[:2]
    for i, c in enumerate(cut):
        out[f"white_cut{i}_name"] = c["name"].replace("车间", "")
        out[f"white_cut{i}_raw"] = round(c["s_raw"], 1)
        out[f"white_cut{i}_white"] = round(c["s_white"], 1)
        out[f"white_cut{i}_pct"] = round((1 - c["s_white"] / c["s_raw"]) * 100, 1)
    return out


def _read_soft(name: str) -> list:
    """同 read(), 但文件不存在时返回空表而不是退出。

    用于**非查询**产物: 清洗留痕(clean_rejects / clean_fixed)与清洗结果
    (clean_energy / clean_production / dim_calendar)。它们不是 analysis.sql
    的输出, 只是给正文"清洗量级"那几句提供计数。

    为什么必须软读: CI 生成报告时 output/ 里**只有** tests/baseline/*.csv
    (29 份 Q*.csv), 清洗产物一份都没有 —— 报告是照基线离线渲染的。硬读会
    让报告根本生成不出来, 而这几行只是叙述性计数, 不该有这种权力。

    **代价要认清**: 缺失时退化成 0, 报告仍会生成。这个代价**不能只靠"CI 里
    看不到"来兜底** —— 线上确实因此出现过一整段"装载 0 条能耗明细"零值句子。
    所以 `_clean_volumes()` 另外返回 `clean_ledgers_present`(任一留痕文件在
    即为真), 页面据此把依赖台账的整段隐掉, 而不是显示一串 0。
    字数计数本身仍由对着真实 output/ 跑的 `test_prose_numbers_*` 守住。
    """
    try:
        return read(name)
    except SystemExit:
        return []


def _clean_volumes() -> dict:
    """清洗量级 —— 从留痕文件现数, 而不是手写。

    正文那句"剔除 228 条（全部是业务键重复）、另修正 708 处（插补 329、
    单价 267、费用重算 243）"曾经全是手写值。这类数字每次调清洗参数都会变,
    必须自动取。

    **"插补"要按 clean_report.txt 的口径取, 不是窄口径**: 清洗报告把
    "消耗量离群置空后插补" 计入"消耗量缺失插补 (其中离群置空 34)", 即
    `329 = 295 + 34`。正文列举的是**修了多少处**, 两种都算"修", 所以用
    `clean_impute_all`(329)。窄口径 295 只是 genuinely-missing 那部分,
    两个数都对, 但混用会让读者以为是同一个量 —— 早先我按 295 改并断言
    329 是重复计数, 是错的: 329 才是与清洗报告一致的口径。

    clean_rejects 里同时含"业务键重复"与"消耗量离群"两类 —— 后者**不再删行**
    (只把该格置空后插补), 只是留痕, 所以剔除数要按原因过滤, 不能数总行数。
    """
    raw = read(os.path.join(BASE_DIR, "data", "raw_energy_data.csv"))
    # 清洗产物与留痕全部软读 —— 理由见 _read_soft 的 docstring: CI 渲染报告时
    # output/ 里只有基线 Q*.csv, 没有这些文件。
    energy = _read_soft("clean_energy.csv")
    prod = _read_soft("clean_production.csv")
    days = _read_soft("dim_calendar.csv")
    rejects = _read_soft("clean_rejects.csv")
    fixed = _read_soft("clean_fixed.csv")
    # 分项按 fix_reason 归类。"消耗量离群置空后插补"里也含"插补"二字, 但它与
    # "消耗量插补"在清洗报告里被合并成一项(329 = 295 + 34), 所以两个都给:
    # clean_impute 是窄口径(真·缺失), clean_impute_all 是清洗报告口径(含离群置空)。
    # 正文用后者 —— 它说的是"修了多少处"。
    reason = lambda r: r.get("fix_reason", "")
    impute_narrow = sum(1 for r in fixed if reason(r) == "消耗量插补")
    impute_outlier = sum(1 for r in fixed if reason(r) == "消耗量离群置空后插补")
    # 离群幅度的两个倍数 —— 正文用它说明"这些格子离谱到什么程度"。
    # 原值只存在 clean_rejects 里(置空后 clean_fixed 只剩插补值), 所以两边配对取。
    # 基准用该(车间,品种)在**事实表里**的中位数(即"正常水平"), 不是全厂中位数 ——
    # 不同品种量纲差几个数量级, 混在一起算出来的倍数没有意义。
    mult_lo = mult_hi = None
    imp_ratio = None
    normal = collections.defaultdict(list)
    for r in energy:
        try:
            normal[(r["workshop_code"], r["energy_code"])].append(float(r["consumption"]))
        except (KeyError, ValueError):
            pass
    orig = {}
    for r in rejects:
        if "离群" not in r.get("reject_reason", ""):
            continue
        try:
            orig[(r["record_date"], r["workshop_code"], r["energy_code"])] = float(r["consumption"])
        except (KeyError, ValueError):
            pass
    mults, ratios = [], []
    for r in fixed:
        if reason(r) != "消耗量离群置空后插补":
            continue
        key = (r["record_date"], r["workshop_code"], r["energy_code"])
        base = normal.get((r["workshop_code"], r["energy_code"]))
        if not base:
            continue
        med = statistics.median(base)
        if med:
            if key in orig:
                mults.append(orig[key] / med)
                if orig[key]:
                    ratios.append(float(r["consumption"]) / orig[key])
    if mults:
        mult_lo, mult_hi = round(min(mults)), round(max(mults))
    if ratios:
        imp_ratio = round(statistics.median(ratios) * 100)
    return {
        # ledgers 是否在场。CI 只用 tests/baseline/*.csv 渲染, 清洗产物一份都没有,
        # 上面那几个计数会整体退化成 0 —— 那不是"清洗掉了 0 条", 是"根本没读着"。
        # 页面据此把整段隐掉, 而不是显示一串 0(详见 _read_soft 的 docstring)。
        "clean_ledgers_present": bool(energy or fixed or rejects),
        "clean_raw": len(raw),
        "clean_energy": len(energy),
        "clean_prod": len(prod),
        "clean_days": len(days),
        "clean_dup": sum(1 for r in rejects if "重复" in r.get("reject_reason", "")),
        "clean_outlier": sum(1 for r in rejects if "离群" in r.get("reject_reason", "")),
        "clean_fixed_n": len(fixed),
        "clean_impute": impute_narrow,
        "clean_impute_all": impute_narrow + impute_outlier,
        "clean_price": sum(1 for r in fixed if reason(r) == "单价插补"),
        "clean_recalc": sum(1 for r in fixed if reason(r) == "费用重算"),
        "outlier_mult_lo": mult_lo,
        "outlier_mult_hi": mult_hi,
        "outlier_impute_pct": imp_ratio,
    }


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
