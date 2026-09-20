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


if __name__ == "__main__":
    main()
