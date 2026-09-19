# -*- coding: utf-8 -*-
"""
clean_data.py — 原始能耗数据清洗与结构化

输入: data/raw_energy_data.csv
输出:
  output/clean_energy.csv      能源消耗事实表(已清洗)
  output/clean_production.csv  产量事实表(按 日期×车间 去重后独立成型)
  output/dim_calendar.csv      日期维表(周末/法定节假日标记)
  output/clean_rejects.csv     剔除明细(可追溯)
  output/clean_fixed.csv       已修正明细(缺失插补/负值/单价异常)
  output/clean_report.txt      清洗报告

清洗动作:
  1. 日期格式归一 (4 种格式 -> YYYY-MM-DD)
  2. 字符串去首尾空格/全角空格
  3. 车间名、能源名 别名与错别字 -> 标准编码
  4. 单位写法归一
  5. 重复记录去重 (按 日期×车间×能源 业务键)
  6. 负消耗量 -> 取绝对值 (仪表倒走)
  7. 消耗量离群值 (Q3 + 3*IQR) -> 剔除
  8. 缺失 消耗量/单价 -> 按 车间×能源×年月 中位数插补
  9. 单价为 0 -> 按能源品种月度中位数插补
 10. 费用与 量×价 偏差 >5% -> 以 量×价 重算

处理顺序不是随意的, 三条原则:
  (1) 先定编码, 再剔除, 最后修正。车间编码/能源编码是所有分组统计的键, 必须在
      剔除与插补之前确定; 否则分组键为空会让中位数算错、甚至整组插补失败。
  (2) 离群值剔除放在缺失插补之前。若先插补, 用混有 10 倍离群值的组内中位数去
      填补缺失, 会把离群污染扩散到本来正常的记录上。
  (3) 去重放在离群检测之前。重复行会把分位数算歪, 导致按 IQR 划定的离群边界失真。

每一步剔除/修正的明细都会单独落盘(clean_rejects.csv / clean_fixed.csv),
不做"静默清洗", 便于事后复盘与回答"这条数据为什么变了"。
"""

import os
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_PATH = os.path.join(BASE_DIR, "data", "raw_energy_data.csv")
OUT_DIR = os.path.join(BASE_DIR, "output")

DATE_FORMATS = ["%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日",
                "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"]

# 车间名别名 -> 标准编码
WORKSHOP_ALIAS = {
    "熔炼车间": "W01", "熔炼": "W01",
    "轧制车间": "W02", "轧制": "W02",
    "热处理车间": "W03", "热处理": "W03",
    "机加工车间": "W04", "机加工": "W04", "机加车间": "W04",
    "表面处理车间": "W05", "表面处理": "W05", "表处车间": "W05",
    "装配车间": "W06", "装配": "W06", "总装车间": "W06",
    "动力站": "W07", "动力车间": "W07",
    "包装车间": "W08", "包装": "W08",
}

# 能源名别名/错别字 -> 标准编码
ENERGY_ALIAS = {
    "电力": "E01", "电": "E01", "外购电": "E01", "电力度": "E01",
    "天然气": "E02", "天燃气": "E02",
    "蒸汽": "E03", "蒸气": "E03",
    "工业水": "E04", "自来水": "E04", "新水": "E04",
    "压缩空气": "E05", "压缩风": "E05",
    "柴油": "E06", "0#柴油": "E06",
}

# 能源编码 -> 标准单位
CANONICAL_UNIT = {"E01": "kWh", "E02": "m³", "E03": "t",
                  "E04": "m³", "E05": "m³", "E06": "kg"}

# 法定节假日 (与国务院办公厅放假安排一致)
HOLIDAY_RANGES = [
    ("2024-01-01", "2024-01-01", "元旦"),
    ("2024-02-10", "2024-02-17", "春节"),
    ("2024-04-04", "2024-04-06", "清明节"),
    ("2024-05-01", "2024-05-05", "劳动节"),
    ("2024-06-08", "2024-06-10", "端午节"),
    ("2024-09-15", "2024-09-17", "中秋节"),
    ("2024-10-01", "2024-10-07", "国庆节"),
    ("2025-01-01", "2025-01-01", "元旦"),
    ("2025-01-28", "2025-02-04", "春节"),
    ("2025-04-04", "2025-04-06", "清明节"),
    ("2025-05-01", "2025-05-05", "劳动节"),
    ("2025-05-31", "2025-06-02", "端午节"),
    ("2025-10-01", "2025-10-08", "国庆节/中秋节"),
]

OUTLIER_IQR_MULT = 3.0
COST_TOLERANCE = 0.05


def log(msg: str) -> None:
    """统一输出入口(日后要换成 logging 或写日志文件, 只需改这一处)"""
    print(msg)


def norm_text(s: pd.Series) -> pd.Series:
    """
    字符串列归一: 清除全角空格、零宽字符与首尾空白, 空串转成缺失值。

    参数:
        s: 待处理的字符串列
    返回:
        StringDtype 列; 原为空串的元素变为 pd.NA

    说明: 全角空格(U+3000)与零宽字符(BOM/ZWNBSP U+FEFF、零宽空格 U+200B)在 ERP
          导出中很常见, 肉眼完全看不出区别, 却会让 "熔炼车间" 与 "熔炼车间 "
          变成两个不同的分组键, 在 SQL 里聚成两行。所以任何文本比对之前先统一清理。
          strip 放在最后, 保证"全角空格 + 半角空格"这种混排也能被彻底去干净。
    """
    return (s.astype("string")
             .str.replace("　", "", regex=False)
             .str.replace("﻿", "", regex=False)
             .str.replace("​", "", regex=False)
             .str.strip()
             .replace({"": pd.NA}))


def parse_dates(s: pd.Series) -> pd.Series:
    """
    按 DATE_FORMATS 逐种格式尝试解析日期。

    参数:
        s: 原始日期字符串列(可能混用多种格式, 也可能为空)
    返回:
        解析成功的为 Timestamp, 所有格式都失败的为 NaT(后续按"无法解析"剔除)

    说明: 逐格式显式尝试, 而不是交给 pandas 自动推断 —— 自动推断在不同 pandas
          版本下行为会变, 且可能把 "2024.01.05" 这类写法静默猜错。这里把系统
          真正接受的格式明确列出来, 可预期、可复现。
          每一轮用 mask 只处理上一轮尚未解析成功的行, 已解析的不再重复处理;
          全部解析完就提前 break, 不做无谓的遍历。
    """
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    for fmt in DATE_FORMATS:
        mask = out.isna() & s.notna()
        if not mask.any():
            break
        out.loc[mask] = pd.to_datetime(s[mask], format=fmt, errors="coerce")
    return out


def build_calendar() -> pd.DataFrame:
    """
    构造日期维表: 2024-01-01 ~ 2025-12-31 逐日, 并打上周末与法定节假日标记。

    返回:
        DataFrame, 含 calendar_date / year / quarter / month / year_month /
        day_of_week / weekday_name / is_weekend / holiday_name / is_holiday

    说明: HOLIDAY_RANGES 是中国节假日历在本项目中的唯一副本, 生成后的 CSV 由
          import_mysql.py 装载为 dim_calendar 表。分析 SQL 通过这张维表做
          "工作日 vs 周末 vs 节假日"的对比下钻(Q14/Q15), 因此节假日口径只需在此维护。
    """
    holiday_map = {}
    for start, end, name in HOLIDAY_RANGES:
        d, last = date.fromisoformat(start), date.fromisoformat(end)
        while d <= last:
            holiday_map[d] = name
            d += timedelta(days=1)

    days = pd.date_range("2024-01-01", "2025-12-31", freq="D")
    df = pd.DataFrame({"calendar_date": days})
    df["year"] = df["calendar_date"].dt.year
    df["quarter"] = df["calendar_date"].dt.quarter
    df["month"] = df["calendar_date"].dt.month
    df["year_month"] = df["calendar_date"].dt.strftime("%Y-%m")
    df["day_of_week"] = df["calendar_date"].dt.dayofweek + 1
    df["weekday_name"] = df["calendar_date"].dt.day_name()
    df["is_weekend"] = (df["calendar_date"].dt.dayofweek >= 5).astype(int)
    df["holiday_name"] = df["calendar_date"].dt.date.map(holiday_map)
    df["is_holiday"] = df["holiday_name"].notna().astype(int)
    df["calendar_date"] = df["calendar_date"].dt.strftime("%Y-%m-%d")
    return df


def main() -> None:
    """执行完整清洗流程: 读取 -> 编码标定 -> 剔除 -> 修正 -> 结构化 -> 落盘 -> 出报告"""
    os.makedirs(OUT_DIR, exist_ok=True)
    if not os.path.exists(RAW_PATH):
        raise SystemExit(f"找不到原始数据: {RAW_PATH}\n请先运行 python src/generate_data.py")

    # dtype=str: 整表按字符串读入, 不让 pandas 在读取阶段就自作主张地把空串转成 NaN、
    # 或把日期串猜成别的格式。类型的显式转换统一放到第 4 步, 保证"读进来什么样就是什么样"。
    raw = pd.read_csv(RAW_PATH, dtype=str, encoding="utf-8-sig")
    n_raw = len(raw)
    log(f"[读取] {RAW_PATH}")
    log(f"       原始记录 {n_raw:,} 行, {len(raw.columns)} 列")

    # stats 收集各步骤的处理条数, 最后统一汇总成清洗报告; 用 dict 而非一堆局部变量,
    # 是为了让"加一步新清洗动作"只需多写一行 stats[...] = ..., 报告部分再取用即可
    stats = {}
    df = raw.copy()  # 保留 raw 不动: 报告里的原始行数、以及排查问题时都要回看原始值

    # ---- 1. 文本归一 ------------------------------------------------------
    # 这些都是要和别名表做等值匹配的文本列, 必须先归一, 否则 "熔炼车间 " 匹配不上别名表
    for col in ["workshop_name", "energy_name", "unit", "record_status", "data_source"]:
        if col in df.columns:
            df[col] = norm_text(df[col])

    # ---- 2. 日期解析 ------------------------------------------------------
    df["record_date"] = parse_dates(norm_text(df["record_date"]))
    bad_date = df["record_date"].isna()
    stats["日期无法解析"] = int(bad_date.sum())

    # ---- 3. 编码回填 ------------------------------------------------------
    # 编码是权威来源(名称可能被人工改乱), 但编码本身也可能缺失或写错,
    # 因此策略是: 编码合法就用编码, 否则回退到"名称 -> 别名表 -> 编码"
    df["workshop_code"] = norm_text(df["workshop_code"])
    df["energy_code"] = norm_text(df["energy_code"])
    stats["能源编码缺失(按名称回填)"] = int(
        (~df["energy_code"].isin(CANONICAL_UNIT)).sum())

    mapped_ws = df["workshop_name"].map(WORKSHOP_ALIAS)
    mapped_en = df["energy_name"].map(ENERGY_ALIAS)

    # 用 where 而不是 fillna: fillna 只补"缺失"的值, 而这里还要覆盖"编码存在但
    # 不在合法集合内"的情况(即写错的编码), where(条件, 替换值) 正好两者都处理
    df["workshop_code"] = df["workshop_code"].where(
        df["workshop_code"].isin(set(WORKSHOP_ALIAS.values())), mapped_ws)
    df["energy_code"] = df["energy_code"].where(
        df["energy_code"].isin(CANONICAL_UNIT), mapped_en)

    # 编码和名称都认不出来的行, 缺失了分组键, 无法参与后续任何按车间/能源的统计
    bad_ws = df["workshop_code"].isna()
    bad_en = df["energy_code"].isna()
    stats["车间无法识别"] = int(bad_ws.sum())
    stats["能源品种无法识别"] = int(bad_en.sum())

    # ---- 4. 数值转换 ------------------------------------------------------
    # errors="coerce": 转不动的(如 "N/A"、空串)一律变 NaN, 不抛异常中断流程;
    # 这些 NaN 会在第 10 步按分组中位数插补, 而不是简单丢掉整行
    for col in ["consumption", "unit_price", "cost", "output_qty", "avg_temperature"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # ---- 5. 单位归一 ------------------------------------------------------
    # 以能源编码为准映射标准单位(kWh/m³/t/kg), 只有编码映射不出来时才保留原写法
    stats["单位写法归一"] = int(
        (df["unit"] != df["energy_code"].map(CANONICAL_UNIT)).sum())
    df["unit"] = df["energy_code"].map(CANONICAL_UNIT).fillna(df["unit"])

    # ---- 6. 剔除不可用记录 ------------------------------------------------
    # 缺日期/缺车间/缺能源品种的记录没有任何可用的分组键, 无法修补, 只能剔除。
    # 注意这里剔除的是"记录", 而数值缺失的记录会保留到第 10 步插补, 两者处理不同。
    reject_mask = df["record_date"].isna() | bad_ws | bad_en
    rejects = df.loc[reject_mask].copy()
    # np.select 按条件顺序取第一个命中的标签, 因此原因之间不会互相覆盖
    rejects["reject_reason"] = np.select(
        [rejects["record_date"].isna(), rejects["workshop_code"].isna(),
         rejects["energy_code"].isna()],
        ["日期无法解析", "车间无法识别", "能源品种无法识别"], default="其他")
    df = df.loc[~reject_mask].copy()
    stats["剔除-合计"] = int(reject_mask.sum())

    # ---- 7. 重复记录去重(按事实表业务键) -----------------------------------
    # 事实表的粒度是 日期×车间×能源品种, 所以去重必须按这三列组成的业务键来做,
    # 而不是按"整行完全相等": 两条记录只要业务键相同, 就只能保留一条。
    # 若按整行去重, 一条 cost 被改过、其余字段相同的记录会逃过检查, 之后被事实表的
    # 唯一键 uk_date_ws_energy 静默丢弃, 导致"清洗后行数"与"实际入库行数"对不上。
    # 本步必须早于离群检测: 重复行会改变分位数位置, 让 IQR 划出的离群边界偏移。
    key_cols = ["record_date", "workshop_code", "energy_code"]
    dup_mask = df.duplicated(subset=key_cols, keep="first")
    dup_rows = df.loc[dup_mask].copy()
    dup_rows["reject_reason"] = "业务键重复(日期×车间×能源)"
    rejects = pd.concat([rejects, dup_rows], ignore_index=True)
    stats["重复记录剔除"] = int(dup_mask.sum())
    df = df.loc[~dup_mask].copy()

    # ---- 8. 离群值剔除 (Q3 + 3*IQR, 按 车间×能源 分组) ---------------------
    # 按 车间×能源 分组而非全表: 熔炼车间的天然气用量天然是包装车间的几十倍,
    # 混在一起算分位数的话, 所有高耗能车间都会被误判成离群。
    grp = df.groupby(["workshop_code", "energy_code"])["consumption"]
    q1 = grp.transform(lambda s: s.quantile(0.25))
    q3 = grp.transform(lambda s: s.quantile(0.75))
    # 乘数取 3 而非经典的 1.5: 1.5 会把正常的季节高峰(冬季天然气翻倍)判成离群,
    # 3 倍只抓 8~15 倍的仪表故障级异常值, 与本数据的注入方式对应
    upper = q3 + OUTLIER_IQR_MULT * (q3 - q1)
    out_mask = df["consumption"] > upper
    out_rows = df.loc[out_mask].copy()
    out_rows["reject_reason"] = "消耗量离群(>Q3+3*IQR)"
    rejects = pd.concat([rejects, out_rows], ignore_index=True)
    stats["剔除-消耗量离群"] = int(out_mask.sum())
    df = df.loc[~out_mask].copy()
    # 说明: NaN > upper 恒为 False, 所以缺失值不会被误判为离群, 会顺利留到第 10 步插补

    # ---- 9. 负值修正 ------------------------------------------------------
    # 负消耗量来自仪表倒走/抄表口径切换, 物理上不存在, 数值本身仍有效, 故取绝对值保留
    neg_mask = df["consumption"] < 0
    stats["负值修正(取绝对值)"] = int(neg_mask.sum())
    df.loc[neg_mask, "consumption"] = df.loc[neg_mask, "consumption"].abs()

    # ---- 10. 缺失插补 -----------------------------------------------------
    df["year_month"] = df["record_date"].dt.strftime("%Y-%m")
    key = ["workshop_code", "energy_code", "year_month"]

    # 两级回退插补: 先用最贴近的口径(同车间+同能源+同月), 仍补不上再放宽到
    # (同车间+同能源) 全期。逐级放宽是为了在"口径尽量准"和"尽量补得上"之间取平衡
    miss_qty = df["consumption"].isna()
    df["consumption"] = df["consumption"].fillna(
        df.groupby(key)["consumption"].transform("median"))
    df["consumption"] = df["consumption"].fillna(
        df.groupby(["workshop_code", "energy_code"])["consumption"].transform("median"))
    stats["插补-消耗量"] = int(miss_qty.sum())

    # 单价为 0 或缺失 -> 按 能源×年月 中位数
    # 单价按能源品种插补(不分车间): 同一能源全厂是同一个采购价, 按车间分会引入无谓偏差。
    # 先把 <=0 的置为 NaN, 让"缺失"和"为0"走同一条 fillna 逻辑, 不必写两遍
    bad_price = df["unit_price"].isna() | (df["unit_price"] <= 0)
    df.loc[bad_price, "unit_price"] = np.nan
    df["unit_price"] = df["unit_price"].fillna(
        df.groupby(["energy_code", "year_month"])["unit_price"].transform("median"))
    df["unit_price"] = df["unit_price"].fillna(
        df.groupby("energy_code")["unit_price"].transform("median"))
    stats["插补-单价(含0值)"] = int(bad_price.sum())

    # ---- 11. 费用重算 -----------------------------------------------------
    # 以 量×价 为权威口径。采集系统里 cost 常常是手工录入或另一条链路算出来的,
    # 与 量×价 对不上时不可信; 偏差超过 COST_TOLERANCE 就整列重算。
    calc_cost = (df["consumption"] * df["unit_price"]).round(2)
    # cost 缺失时用 0 参与相减, 得到的偏差必然为 100%, 同样会被下面的阈值判定命中
    diff = (df["cost"].fillna(0) - calc_cost).abs()
    # 除零保护: 量为 0 时不计算相对偏差(置 0), 避免 NaN 影响后面的布尔比较
    dev = np.where(calc_cost.abs() > 0, diff / calc_cost.abs(), 0.0)
    cost_mask = (df["cost"].isna()) | (dev > COST_TOLERANCE)
    stats["费用重算(量×价)"] = int(cost_mask.sum())
    df.loc[cost_mask, "cost"] = calc_cost[cost_mask]

    # ---- 12. 修正明细留痕 -------------------------------------------------
    # 把被修正过的记录单独存一份, 便于审计"哪些数不是原始值"
    fixed_mask = miss_qty | bad_price | neg_mask | cost_mask
    fixed = df.loc[fixed_mask].copy()
    # 掩码是整表长度, 而 fixed 只是其中的子集, 所以必须用 .loc[fixed.index] 按子集
    # 索引取一遍, 否则长度对不上会直接报错(这里踩过坑)。一行同时命中多种修正时,
    # np.select 取第一个命中的原因作为主因
    fixed["fix_reason"] = np.select(
        [miss_qty.loc[fixed.index], neg_mask.loc[fixed.index],
         bad_price.loc[fixed.index], cost_mask.loc[fixed.index]],
        ["消耗量插补", "负值取绝对值", "单价插补", "费用重算"], default="其他")

    # ---- 13. 事实表成型 ---------------------------------------------------
    # 生产状态由源系统的 record_status 派生; 停产日仍有基础负荷, 这些行必须保留,
    # 因为"待机损耗"分析(Q13)正是要靠 is_production_day=0 的记录才算得出来
    df["is_production_day"] = (df["record_status"] != "停产").astype(int)

    energy_fact = df[[
        "record_date", "workshop_code", "energy_code", "consumption", "unit",
        "unit_price", "cost", "record_status", "avg_temperature",
        "data_source", "is_production_day",
    ]].copy()
    energy_fact["record_date"] = energy_fact["record_date"].dt.strftime("%Y-%m-%d")
    energy_fact["consumption"] = energy_fact["consumption"].round(3)
    energy_fact["unit_price"] = energy_fact["unit_price"].round(4)
    energy_fact["cost"] = energy_fact["cost"].round(2)
    energy_fact["avg_temperature"] = energy_fact["avg_temperature"].round(2)

    # 产量在原始明细中随能源品种重复(一天一个车间的产量被抄了 3~4 遍, 每个能源一行),
    # 所以要按 日期×车间 聚合成独立的事实表:
    #   - median 而非 sum: 同一天同一车间的产量本就该是同一个值, 取中位数是稳健去重,
    #     即使个别行缺失或被改错, 也不会像 sum 那样把产量翻几倍
    #   - output_unit 取 first: 同一车间的计量单位唯一, 取哪个都一样
    #   - dropna: 产量确实缺失的整行直接不要, 不插补 —— 产量是单耗的分母,
    #     凭空插补会直接污染"单位产品能耗"这个核心指标
    prod_fact = (df[["record_date", "workshop_code", "output_qty", "output_unit"]]
                 .dropna(subset=["output_qty"])
                 .groupby(["record_date", "workshop_code"], as_index=False)
                 .agg(output_qty=("output_qty", "median"),
                      output_unit=("output_unit", "first")))
    prod_fact["record_date"] = prod_fact["record_date"].dt.strftime("%Y-%m-%d")
    prod_fact["output_qty"] = prod_fact["output_qty"].round(3)
    stats["产量记录数"] = len(prod_fact)

    calendar = build_calendar()

    # ---- 14. 落盘 ---------------------------------------------------------
    # encoding="utf-8-sig" 会写入 BOM, 这样 Excel 双击打开中文表头不会乱码;
    # 两个留痕文件用 if len() 判断, 该步没有命中记录时不生成空文件
    energy_fact.to_csv(os.path.join(OUT_DIR, "clean_energy.csv"),
                       index=False, encoding="utf-8-sig")
    prod_fact.to_csv(os.path.join(OUT_DIR, "clean_production.csv"),
                     index=False, encoding="utf-8-sig")
    calendar.to_csv(os.path.join(OUT_DIR, "dim_calendar.csv"),
                    index=False, encoding="utf-8-sig")
    if len(rejects):
        rejects.to_csv(os.path.join(OUT_DIR, "clean_rejects.csv"),
                       index=False, encoding="utf-8-sig")
    if len(fixed):
        fixed.to_csv(os.path.join(OUT_DIR, "clean_fixed.csv"),
                     index=False, encoding="utf-8-sig")

    # ---- 15. 报告 ---------------------------------------------------------
    lines = []
    lines.append("=" * 62)
    lines.append("工业能耗数据清洗报告")
    lines.append("=" * 62)
    lines.append(f"原始记录数            : {n_raw:,}")
    lines.append(f"清洗后能源明细        : {len(energy_fact):,}")
    lines.append(f"清洗后产量明细        : {len(prod_fact):,}")
    lines.append(f"数据保留率            : {len(energy_fact) / n_raw:.2%}")
    lines.append("-" * 62)
    lines.append("【剔除】")
    lines.append(f"  日期无法解析        : {stats['日期无法解析']:,}")
    lines.append(f"  车间无法识别        : {stats['车间无法识别']:,}")
    lines.append(f"  能源品种无法识别    : {stats['能源品种无法识别']:,}")
    lines.append(f"  重复记录(业务键)    : {stats['重复记录剔除']:,}")
    lines.append(f"  消耗量离群值        : {stats['剔除-消耗量离群']:,}")
    lines.append(f"  合计                : {len(rejects):,}")
    lines.append("-" * 62)
    lines.append("【修正】")
    lines.append(f"  单位写法归一        : {stats['单位写法归一']:,}")
    lines.append(f"  能源编码回填        : {stats['能源编码缺失(按名称回填)']:,}")
    lines.append(f"  负值取绝对值        : {stats['负值修正(取绝对值)']:,}")
    lines.append(f"  消耗量缺失插补      : {stats['插补-消耗量']:,}")
    lines.append(f"  单价缺失/0 插补     : {stats['插补-单价(含0值)']:,}")
    lines.append(f"  费用按量×价重算     : {stats['费用重算(量×价)']:,}")
    lines.append("-" * 62)
    lines.append("【业务口径核对】")
    lines.append(f"  日期范围            : {energy_fact['record_date'].min()} ~ "
                 f"{energy_fact['record_date'].max()}")
    lines.append(f"  车间数              : {energy_fact['workshop_code'].nunique()}")
    lines.append(f"  能源品种数          : {energy_fact['energy_code'].nunique()}")
    lines.append(f"  停产记录占比        : "
                 f"{(energy_fact['is_production_day'] == 0).mean():.2%}")
    lines.append("=" * 62)

    report = "\n".join(lines)
    with open(os.path.join(OUT_DIR, "clean_report.txt"), "w", encoding="utf-8") as f:
        f.write(report + "\n")

    log("")
    log(report)
    log("")
    log(f"[输出] {OUT_DIR}")
    for name in ["clean_energy.csv", "clean_production.csv", "dim_calendar.csv",
                 "clean_rejects.csv", "clean_fixed.csv", "clean_report.txt"]:
        p = os.path.join(OUT_DIR, name)
        if os.path.exists(p):
            log(f"       {name}")


if __name__ == "__main__":
    main()
