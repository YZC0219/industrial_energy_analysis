# -*- coding: utf-8 -*-
"""
generate_data.py — 生成工业能耗原始数据(模拟 ERP/MES 导出)

输出: data/raw_energy_data.csv

刻意注入脏数据, 供 clean_data.py 清洗:
  - 缺失值 (消耗量 / 单价 / 产量)
  - 完全重复行
  - 负值 (仪表倒走)
  - 单位写法不统一 (kWh / KWH / 度 ...)
  - 车间名 / 能源名 别名、错别字、首尾空格
  - 日期格式不统一 (2024-01-05 / 2024/1/5 / 2024.01.05 / 2024年1月5日)
  - 极端离群值 (消耗量放大 10 倍)
  - 单价为 0

业务规则(生成逻辑, 用于后续验证分析结果):
  - 连续型车间全年不停产, 间歇型车间周日/节假日基本停产
  - 停产日仍存在基础负荷(待机损耗)
  - 能耗有季节性: 电夏冬高, 天然气冬季高
  - 产量年增长 5%, 单位产品能耗年下降 3%(节能改造)
"""

import argparse
import hashlib
import os
import sys
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(BASE_DIR, "data", "raw_energy_data.csv")

RANDOM_SEED = 20240918
START_DATE = date(2024, 1, 1)
END_DATE = date(2025, 12, 31)

# --------------------------------------------------------------------------
# 车间主数据
# --------------------------------------------------------------------------
# standby: 停产状态下的基础负荷比例(待机损耗)
WORKSHOPS = [
    dict(code="W01", name="熔炼车间", process="熔炼", unit="吨", out=260.0,
         continuous=True, standby=0.14,
         mix={"E01": 42000, "E02": 9500, "E04": 260, "E05": 3200}),
    dict(code="W02", name="轧制车间", process="轧制", unit="吨", out=320.0,
         continuous=True, standby=0.16,
         mix={"E01": 38000, "E02": 6200, "E03": 44, "E04": 180}),
    dict(code="W03", name="热处理车间", process="热处理", unit="吨", out=95.0,
         continuous=True, standby=0.20,
         mix={"E01": 26000, "E02": 8800, "E03": 30}),
    dict(code="W04", name="机加工车间", process="机加工", unit="件", out=4200.0,
         continuous=False, standby=0.18,
         mix={"E01": 18500, "E05": 5600, "E04": 60}),
    dict(code="W05", name="表面处理车间", process="表面处理", unit="平方米", out=6800.0,
         continuous=False, standby=0.22,
         mix={"E01": 22000, "E03": 26, "E04": 420, "E02": 900}),
    dict(code="W06", name="装配车间", process="装配", unit="台", out=260.0,
         continuous=False, standby=0.15,
         mix={"E01": 12000, "E05": 4200}),
    dict(code="W07", name="动力站", process="公用工程", unit="吨蒸汽", out=520.0,
         continuous=True, standby=0.30,
         mix={"E02": 16500, "E01": 8500, "E06": 180, "E04": 900}),
    dict(code="W08", name="包装车间", process="包装", unit="件", out=9800.0,
         continuous=False, standby=0.12,
         mix={"E01": 5400, "E05": 1400}),
]

# --------------------------------------------------------------------------
# 能源品种主数据
# --------------------------------------------------------------------------
# std_coal : 折标煤系数 kgce/单位   (GB/T 2589-2020《综合能耗计算通则》当量值)
# co2      : 排放因子 kgCO2/单位   (生态环境部电网平均排放因子 / IPCC 缺省值)
# price    : 参考单价 元/单位
ENERGY_TYPES = [
    dict(code="E01", name="电力", unit="kWh", std_coal=0.1229, co2=0.5703, price=0.68),
    dict(code="E02", name="天然气", unit="m³", std_coal=1.3300, co2=2.1622, price=3.45),
    dict(code="E03", name="蒸汽", unit="t", std_coal=95.7000, co2=260.00, price=220.00),
    dict(code="E04", name="工业水", unit="m³", std_coal=0.2429, co2=0.3440, price=4.10),
    dict(code="E05", name="压缩空气", unit="m³", std_coal=0.0400, co2=0.0000, price=0.28),
    dict(code="E06", name="柴油", unit="kg", std_coal=1.4571, co2=3.0959, price=7.85),
]

# 月度季节性系数: 电(夏高峰+冬采暖)、天然气(冬季采暖)、蒸汽(冬季)
SEASON_ELEC = {1: 1.10, 2: 1.05, 3: 0.97, 4: 0.93, 5: 0.96, 6: 1.10,
               7: 1.20, 8: 1.18, 9: 1.03, 10: 0.95, 11: 1.02, 12: 1.12}
SEASON_GAS = {1: 1.38, 2: 1.32, 3: 1.10, 4: 0.92, 5: 0.82, 6: 0.76,
              7: 0.74, 8: 0.76, 9: 0.82, 10: 0.94, 11: 1.15, 12: 1.34}
SEASON_STEAM = {1: 1.22, 2: 1.18, 3: 1.05, 4: 0.92, 5: 0.86, 6: 0.82,
                7: 0.80, 8: 0.82, 9: 0.88, 10: 0.96, 11: 1.10, 12: 1.20}
SEASON_FLAT = {m: 1.0 for m in range(1, 13)}

# 北方城市月均气温(℃), 用于相关性分析
MONTH_TEMP = {1: -4.2, 2: -0.5, 3: 6.8, 4: 15.1, 5: 21.3, 6: 25.8,
              7: 28.1, 8: 26.9, 9: 21.7, 10: 14.6, 11: 5.9, 12: -1.3}

# 法定节假日 (含春节/国庆长假)
HOLIDAYS = set()
for _rng in ["2024-01-01:2024-01-01", "2024-02-10:2024-02-17", "2024-04-04:2024-04-06",
             "2024-05-01:2024-05-05", "2024-06-08:2024-06-10", "2024-09-15:2024-09-17",
             "2024-10-01:2024-10-07", "2025-01-01:2025-01-01", "2025-01-28:2025-02-04",
             "2025-04-04:2025-04-06", "2025-05-01:2025-05-05", "2025-05-31:2025-06-02",
             "2025-10-01:2025-10-08"]:
    _s, _e = _rng.split(":")
    _d, _last = date.fromisoformat(_s), date.fromisoformat(_e)
    while _d <= _last:
        HOLIDAYS.add(_d)
        _d += timedelta(days=1)

# 调休上班日(周末但正常生产)
MAKEUP_WORKDAYS = {
    date(2024, 2, 4), date(2024, 2, 18), date(2024, 4, 7), date(2024, 4, 28),
    date(2024, 5, 11), date(2024, 9, 14), date(2024, 9, 29), date(2024, 10, 12),
    date(2025, 1, 26), date(2025, 2, 8), date(2025, 4, 27), date(2025, 9, 28),
    date(2025, 10, 11),
}

# --------------------------------------------------------------------------
# 脏数据注入字典
# --------------------------------------------------------------------------
WORKSHOP_NAME_VARIANTS = {
    "熔炼车间": ["熔炼车间", "熔炼车间 ", "熔炼", "熔炼车间　", "熔炼车间"],
    "轧制车间": ["轧制车间", "轧制", "轧制车间 ", "轧制车间"],
    "热处理车间": ["热处理车间", "热处理", "热处理车间 ", "热处理车间"],
    "机加工车间": ["机加工车间", "机加工", "机加车间", "机加工车间"],
    "表面处理车间": ["表面处理车间", "表面处理", "表处车间", "表面处理车间"],
    "装配车间": ["装配车间", "装配", "总装车间", "装配车间"],
    "动力站": ["动力站", "动力车间", "动力站 ", "动力站"],
    "包装车间": ["包装车间", "包装", "包装车间 ", "包装车间"],
}

ENERGY_NAME_VARIANTS = {
    "电力": ["电力", "电", "外购电", "电力度"],
    "天然气": ["天然气", "天燃气", "天然气 ", "天然气"],
    "蒸汽": ["蒸汽", "蒸气", "蒸汽 ", "蒸汽"],
    "工业水": ["工业水", "自来水", "新水", "工业水 "],
    "压缩空气": ["压缩空气", "压缩风", "压缩空气 ", "压缩空气"],
    "柴油": ["柴油", "0#柴油", "柴油 ", "柴油"],
}

UNIT_VARIANTS = {
    "电力": ["kWh", "KWH", "kw·h", "度", "kWh"],
    "天然气": ["m³", "m3", "立方米", "m³"],
    "蒸汽": ["t", "T", "吨", "t"],
    "工业水": ["m³", "m3", "吨", "m³"],
    "压缩空气": ["m³", "m3", "立方米", "m³"],
    "柴油": ["kg", "KG", "千克", "kg"],
}


def season_factor(energy_code: str, month: int) -> float:
    if energy_code == "E01":
        return SEASON_ELEC[month]
    if energy_code == "E02":
        return SEASON_GAS[month]
    if energy_code == "E03":
        return SEASON_STEAM[month]
    return SEASON_FLAT[month]


def load_factor(ws: dict, d: date) -> float:
    """当日生产负荷率 0~1"""
    if d in HOLIDAYS:
        return 0.90 if ws["continuous"] else 0.06
    if d.weekday() == 6:  # 周日
        return 0.94 if ws["continuous"] else 0.20
    if d.weekday() == 5:  # 周六
        return 0.98 if ws["continuous"] else 0.62
    if d in MAKEUP_WORKDAYS and not ws["continuous"]:
        return 1.0
    return 1.0


def record_status(ws: dict, d: date) -> str:
    lf = load_factor(ws, d)
    if lf <= 0.10:
        return "停产"
    if lf < 0.70:
        return "低负荷"
    return "正常"


def build_clean_rows(rng: np.random.Generator) -> pd.DataFrame:
    """生成"干净"的业务数据(尚未注入脏数据)"""
    etype = {e["code"]: e for e in ENERGY_TYPES}
    rows = []
    d = START_DATE
    while d <= END_DATE:
        # 年化趋势: 产量 +5%/年, 单耗 -3%/年
        years_in = (d - START_DATE).days / 365.0
        output_trend = 1.0 + 0.05 * years_in
        intensity_trend = 1.0 - 0.03 * years_in
        temp = MONTH_TEMP[d.month] + rng.normal(0, 2.6)

        for ws in WORKSHOPS:
            lf = load_factor(ws, d)
            # eff = 负荷形状(含停产日基础负荷) × 产量年趋势
            # 使能耗随产量同步增长, 再叠加 intensity_trend, 即
            # 能耗年增 ≈ 5%(产量) - 3%(单耗) ≈ 1.9%
            eff = (ws["standby"] + (1.0 - ws["standby"]) * lf) * output_trend
            out_qty = ws["out"] * output_trend * lf * rng.normal(1.0, 0.055)
            out_qty = max(out_qty, 0.0)

            for ecode, base in ws["mix"].items():
                e = etype[ecode]
                noise = rng.normal(1.0, 0.05)
                qty = (base * eff * season_factor(ecode, d.month)
                       * intensity_trend * noise)
                # 连续型车间的天然气/电在停产日仍保底
                qty = max(qty, base * ws["standby"] * 0.45)
                price = e["price"] * rng.normal(1.0, 0.015)
                rows.append({
                    "record_date": d.isoformat(),
                    "workshop_code": ws["code"],
                    "workshop_name": ws["name"],
                    "energy_code": ecode,
                    "energy_name": e["name"],
                    "consumption": round(float(qty), 3),
                    "unit": e["unit"],
                    "unit_price": round(float(price), 4),
                    "cost": round(float(qty) * float(price), 2),
                    "output_qty": round(float(out_qty), 3),
                    "output_unit": ws["unit"],
                    "avg_temperature": round(float(temp), 2),
                    "record_status": record_status(ws, d),
                    "data_source": "MES",
                    # 记录在源系统里的最后修改时刻。
                    #
                    # 为什么需要这个字段: 事实表的唯一键是
                    # (record_date, workshop_code, energy_code), 只标识"哪条
                    # 业务记录", 不标识"哪个版本"。没有时间维时, 同一业务键
                    # 的两批数据无法判断谁更新 —— 上游修正一条历史记录后重新
                    # 装载, 数据库无从知道该不该覆盖。有了 updated_at, 装载层
                    # 才能用 upsert 正确落地历史修正。
                    #
                    # 取值: 由业务键确定性地派生出"当天第几分钟上报"。
                    #
                    # 为什么不用 rng: rng 是共享的全局序列, 多消耗一个随机数
                    # 会让它后面所有取值整体偏移, 于是"只是加一个元数据字段"
                    # 就改变了能耗、价格等全部业务数据 —— 那会让已有的基线
                    # 快照全部失效。数据变化应当只来自有意的口径调整, 不能
                    # 来自"加字段碰巧动了随机数"。这里从行内容派生, 一个随机
                    # 数都不消耗, 对既有数据做到逐字节无影响。
                    #
                    # 为什么用 md5 而不是内置 hash(): Python 对 str 的 hash
                    # 带随机化种子(PYTHONHASHSEED), 跨进程不稳定, 换个进程
                    # 重跑就会得到不同的时间戳。md5 在任何进程/机器上一致。
                    #
                    # 取值范围(下游增量装载依赖这个性质): `% 720` 分钟即 [0,12) 小时,
                    # 所以 updated_at 恒落在 record_date **当天**的 [08:00, 20:00) 内。
                    # 这使"按 updated_at 切"与"按天切"在整天边界上等价 —— 水位线
                    # 方案正是靠它把"每日一批"的语义与"时间戳水位线"对齐的。
                    # 若把这里改成 `% 86400`(跨天), 该性质立刻失效, 水位线会切到
                    # 半天上, 日边界不再与水位线重合。
                    "updated_at": (
                        datetime.combine(d, time(hour=8))
                        + timedelta(minutes=int(hashlib.md5(
                            f"{d.isoformat()}|{ws['code']}|{ecode}".encode()
                        ).hexdigest(), 16) % 720)
                    ).strftime("%Y-%m-%d %H:%M:%S"),
                })
        d += timedelta(days=1)
    return pd.DataFrame(rows)


def inject_dirt(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """注入脏数据"""
    df = df.copy()
    n = len(df)
    # 保留规范能源名, 供后续查表使用(名称在第 1 步会被改成别名)
    canonical_energy = df["energy_name"].copy()

    # 1) 车间名/能源名 别名与错别字 (8%)
    for col, variants in (("workshop_name", WORKSHOP_NAME_VARIANTS),
                          ("energy_name", ENERGY_NAME_VARIANTS)):
        mask = rng.random(n) < 0.08
        df.loc[mask, col] = [
            rng.choice(variants[v]) for v in df.loc[mask, col]
        ]

    # 2) 单位写法不统一 (12%)
    mask = rng.random(n) < 0.12
    df.loc[mask, "unit"] = [
        rng.choice(UNIT_VARIANTS[v]) for v in canonical_energy[mask]
    ]

    # 3) 能源编码缺失 (2%)
    mask = rng.random(n) < 0.02
    df.loc[mask, "energy_code"] = np.nan

    # 4) 日期格式不统一 (10%)
    mask = rng.random(n) < 0.10
    fmts = ["%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日", "%Y-%m-%d 00:00:00"]
    df.loc[mask, "record_date"] = [
        date.fromisoformat(v).strftime(f)
        for v, f in zip(df.loc[mask, "record_date"], rng.choice(fmts, mask.sum()))
    ]

    # 5) 数值缺失 (1.5% 消耗量, 1% 单价, 1.2% 产量)
    df.loc[rng.random(n) < 0.015, "consumption"] = np.nan
    df.loc[rng.random(n) < 0.010, "unit_price"] = np.nan
    df.loc[rng.random(n) < 0.012, "output_qty"] = np.nan

    # 6) 单价为 0 (0.3%)
    df.loc[rng.random(n) < 0.003, "unit_price"] = 0.0

    # 7) 负值 — 仪表倒走 (0.25%)
    mask = rng.random(n) < 0.0025
    df.loc[mask, "consumption"] = -df.loc[mask, "consumption"].abs()

    # 8) 极端离群值 — 消耗量放大 8~15 倍 (0.2%)
    mask = rng.random(n) < 0.002
    df.loc[mask, "consumption"] *= rng.uniform(8, 15, mask.sum())

    # 9) 完全重复行 (1.2%)
    dup = df.sample(n=int(n * 0.012), random_state=RANDOM_SEED)
    df = pd.concat([df, dup], ignore_index=True)

    # 10) 重新计算 cost 中一小部分, 制造 cost 与 量×价 不一致 (0.5%)
    mask = rng.random(len(df)) < 0.005
    df.loc[mask, "cost"] = (df.loc[mask, "cost"] * rng.uniform(1.05, 1.25, mask.sum())).round(2)

    return df.sample(frac=1.0, random_state=RANDOM_SEED).reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="生成工业能耗原始数据(模拟 ERP/MES 导出)")
    ap.add_argument("--since-updated-at", metavar="'YYYY-MM-DD HH:MM:SS'",
                    help="只写出 updated_at 严格大于该时刻的行(增量产出)")
    ap.add_argument("--out", help=f"输出路径(默认 {OUT_PATH})")
    args = ap.parse_args()

    rng = np.random.default_rng(RANDOM_SEED)
    clean = build_clean_rows(rng)
    print(f"[1/3] 生成业务明细 {len(clean):,} 行 "
          f"({START_DATE} ~ {END_DATE}, {len(WORKSHOPS)} 个车间)")

    dirty = inject_dirt(clean, rng)
    print(f"[2/3] 注入脏数据后 {len(dirty):,} 行 "
          f"(重复 {len(dirty) - len(clean):,} 行)")

    # 增量产出: 在**全部生成并注入脏数据之后**才过滤。
    #
    # 为什么必须后置过滤, 不能在生成阶段就少造行: rng 是单个共享的全局随机序列,
    # build_clean_rows 逐日/逐车间/逐能源地消耗它, inject_dirt 接着消耗同一个序列。
    # 让 build_clean_rows 跳过若干天, 它消耗的随机数个数就变了, 后面所有取值整体
    # 偏移 —— 于是"只产出一部分数据"会连带改变**全量路径**的能耗、价格与脏数据位置,
    # 29 份回归基线全部失效。数据变化应当只来自有意的口径调整, 不能来自"少造几天
    # 碰巧动了随机数"。(同样的坑在 docs/交接说明_第二阶段任务A.md 的"关键教训 1"
    # 里记录过一次, 那次是加 updated_at 时误用了 rng。这里不再重蹈。)
    #
    # 放在 inject_dirt 之后还有一层作用: 第 9 步 concat 出来的重复行也各自带
    # updated_at, 会被同一条规则一起选出或排除, 不会出现"孤立的重复行"。
    out_path = args.out or OUT_PATH
    if args.since_updated_at:
        cutoff = args.since_updated_at
        before = len(dirty)
        dirty = dirty[dirty["updated_at"] > cutoff]
        print(f"      [增量] updated_at > {cutoff} -> {len(dirty):,} 行 "
              f"(由 {before:,} 行筛出)")

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    dirty.to_csv(out_path, index=False, encoding="utf-8-sig", lineterminator="\n")
    print(f"[3/3] 已写出 -> {out_path}")


if __name__ == "__main__":
    main()
