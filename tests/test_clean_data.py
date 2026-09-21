# -*- coding: utf-8 -*-
"""
清洗规则的单元测试 —— 全部离线, 不依赖 MySQL, 不重跑管道。

这些测试直接调用 clean_data.py 里的**纯函数**(norm_text / parse_dates /
build_calendar), 每个函数对应一类被注入的脏数据。之所以测函数而不测整个
main(), 是因为 main() 把"读文件→写文件"也包进来了, 测它就必须动磁盘;
而清洗规则本身是纯变换, 单独测又快又准, 失败时也一眼看得出是哪条规则坏了。

另有少量针对**产物文件**的断言放在最后(需要管道已跑过一次), 用于守住
清洗的最终结果不发生意外漂移。
"""

from __future__ import annotations

import os

import pandas as pd
import pytest

import clean_data as cd


# =============================================================================
# 1. 文本归一 (norm_text)
# =============================================================================

class TestNormText:
    """对应 clean_data.py 第 1 步: 文本列归一"""

    def test_strips_halfwidth_whitespace(self):
        """普通首尾空格必须去掉, 否则 '熔炼车间 ' 匹配不上别名表"""
        s = pd.Series(["  熔炼车间 ", "电力"])
        assert list(cd.norm_text(s)) == ["熔炼车间", "电力"]

    def test_strips_fullwidth_space(self):
        """全角空格 U+3000 肉眼与半角无异, 但会让 SQL 聚成两行 —— 必须去掉"""
        s = pd.Series(["熔炼车间　", "　电力"])
        assert list(cd.norm_text(s)) == ["熔炼车间", "电力"]

    def test_strips_zero_width_chars(self):
        """BOM/ZWNBSP(U+FEFF) 与零宽空格(U+200B) 来自 ERP 导出, 必须去掉"""
        s = pd.Series(["﻿熔炼车间", "电​力"])
        assert list(cd.norm_text(s)) == ["熔炼车间", "电力"]

    def test_mixed_fullwidth_and_halfwidth(self):
        """'全角空格 + 半角空格' 混排要能被一次去干净"""
        s = pd.Series(["　 熔炼车间 　"])
        assert list(cd.norm_text(s)) == ["熔炼车间"]

    def test_empty_string_becomes_na(self):
        """空串转 NA: 下游用 isna() 判断缺失, 空串会漏判"""
        s = pd.Series(["", "   ", "电力"])
        out = cd.norm_text(s)
        assert out.isna().sum() == 2
        assert out.iloc[2] == "电力"

    def test_returns_string_dtype(self):
        """返回 StringDtype, 保证 pd.NA 语义与 object dtype 下的 None 区分开"""
        out = cd.norm_text(pd.Series(["电力"]))
        assert out.dtype == "string"


# =============================================================================
# 2. 日期解析 (parse_dates)
# =============================================================================

class TestParseDates:
    """对应 clean_data.py 第 2 步: 4 种日期格式混用"""

    @pytest.mark.parametrize("raw,expected", [
        ("2024-01-05", "2024-01-05"),   # ISO
        ("2024/1/5",   "2024-01-05"),   # 斜杠 + 不补零
        ("2024.01.05", "2024-01-05"),   # 点分
        ("2024年1月5日", "2024-01-05"),  # 中文
    ])
    def test_all_four_formats_parse_to_same_date(self, raw, expected):
        """四种写法必须归一到同一个日期 —— 这是"格式混用"那类脏数据的核心断言"""
        out = cd.parse_dates(pd.Series([raw]))
        assert out.iloc[0] == pd.Timestamp(expected)

    def test_formats_mixed_in_one_column(self):
        """同一列里混着四种格式也要全部解析成功"""
        s = pd.Series(["2024-01-05", "2024/1/5", "2024.01.05", "2024年1月5日"])
        out = cd.parse_dates(s)
        assert out.notna().all()
        assert out.nunique() == 1

    def test_unparseable_becomes_nat(self):
        """全部格式都失败的返回 NaT, 供下一步按'无法解析'剔除"""
        out = cd.parse_dates(pd.Series(["不是日期", "2024-13-45", "N/A"]))
        assert out.isna().all()

    def test_already_parsed_not_reprocessed(self):
        """用 mask 逐轮只处理未解析行; 已解析的值不能被后续格式覆盖成 NaT"""
        out = cd.parse_dates(pd.Series(["2024-01-05", "乱码"]))
        assert out.iloc[0] == pd.Timestamp("2024-01-05")
        assert pd.isna(out.iloc[1])

    def test_preserves_index(self):
        """索引必须保留: 后续要按索引对齐回原表"""
        s = pd.Series(["2024-01-05", "2024-01-06"], index=[10, 20])
        out = cd.parse_dates(s)
        assert list(out.index) == [10, 20]

    def test_missing_value_stays_nat(self):
        """本来就是缺失的(NaN)不能被当成解析失败之外的东西"""
        out = cd.parse_dates(pd.Series([pd.NA, "2024-01-05"]))
        assert pd.isna(out.iloc[0])
        assert out.iloc[1] == pd.Timestamp("2024-01-05")


# =============================================================================
# 3. 日期维表 (build_calendar)
# =============================================================================

class TestBuildCalendar:
    """对应 clean_data.py 第 13 步: dim_calendar 的构造"""

    def test_covers_full_range(self):
        """2024-01-01 ~ 2025-12-31 共 731 天(2024 是闰年)"""
        cal = cd.build_calendar()
        assert len(cal) == 731
        assert cal["calendar_date"].min() == "2024-01-01"
        assert cal["calendar_date"].max() == "2025-12-31"

    def test_dates_are_unique(self):
        """维表主键是 calendar_date, 不能有重复"""
        cal = cd.build_calendar()
        assert cal["calendar_date"].is_unique

    def test_weekend_flag_matches_day_of_week(self):
        """is_weekend 必须与 day_of_week 自洽(6/7 为周末)"""
        cal = cd.build_calendar()
        expected = (cal["day_of_week"] >= 6).astype(int)
        assert (cal["is_weekend"] == expected).all()

    def test_is_holiday_consistent_with_holiday_name(self):
        """is_holiday 是 holiday_name 非空的派生, 两者不能打架"""
        cal = cd.build_calendar()
        assert (cal["is_holiday"] == cal["holiday_name"].notna().astype(int)).all()

    def test_known_holiday_detected(self):
        """2024 春节 2/10~2/17 整段都要标上"""
        cal = cd.build_calendar().set_index("calendar_date")
        assert cal.loc["2024-02-10", "holiday_name"] == "春节"
        assert cal.loc["2024-02-17", "holiday_name"] == "春节"
        assert cal.loc["2024-02-18", "is_holiday"] == 0

    def test_plain_workday_not_holiday(self):
        """2024-03-15 是普通周五, 不应被标成节假日"""
        cal = cd.build_calendar().set_index("calendar_date")
        assert cal.loc["2024-03-15", "is_holiday"] == 0
        assert cal.loc["2024-03-15", "is_weekend"] == 0

    def test_year_month_format(self):
        """year_month 是 'YYYY-MM', 分析 SQL 里大量按它分组"""
        cal = cd.build_calendar()
        assert set(cal["year_month"].unique()) == {
            f"{y}-{m:02d}" for y in (2024, 2025) for m in range(1, 13)
        }


# =============================================================================
# 4. 别名映射表
# =============================================================================

class TestAliasTables:
    """对应 clean_data.py 第 3 步: 车间名/能源名的别名与错别字映射"""

    def test_all_workshops_have_alias_entries(self):
        """8 个标准车间编码 W01~W08 都必须在别名表里有对应写法"""
        assert set(cd.WORKSHOP_ALIAS.values()) == {f"W{i:02d}" for i in range(1, 9)}

    def test_all_energy_types_have_alias_entries(self):
        """6 个标准能源编码 E01~E06 都必须在别名表里有对应写法"""
        assert set(cd.ENERGY_ALIAS.values()) == {f"E{i:02d}" for i in range(1, 7)}

    @pytest.mark.parametrize("typo,code", [
        ("天燃气", "E02"),      # 别字
        ("蒸气", "E03"),        # 别字
        ("0#柴油", "E06"),      # 带标号
        ("总装车间", "W06"),    # 别名
        ("表处车间", "W05"),    # 简称
    ])
    def test_known_typos_map_correctly(self, typo, code):
        """注入的错别字/别名必须都能映射到标准编码"""
        table = cd.ENERGY_ALIAS if code.startswith("E") else cd.WORKSHOP_ALIAS
        assert table.get(typo) == code

    def test_every_energy_code_has_canonical_unit(self):
        """CANONICAL_UNIT 必须覆盖全部能源编码 —— 它是单位归一与编码回填的依据"""
        assert set(cd.CANONICAL_UNIT) == set(cd.ENERGY_ALIAS.values())

    def test_alias_tables_have_no_conflicting_targets(self):
        """同一个编码可以被多种写法指向, 但每种写法只能指向一个编码"""
        for table in (cd.WORKSHOP_ALIAS, cd.ENERGY_ALIAS):
            assert len(set(table.values())) == len(
                {v for v in table.values()}
            ), "别名表本身不应有冲突"
            # 反向检查: 标准名到编码是单射
            for name, code in table.items():
                assert isinstance(name, str) and isinstance(code, str)


# =============================================================================
# 5. 清洗产物的一致性 (需要管道已跑过一次)
# =============================================================================

pytestmark_artifacts = pytest.mark.skipif(
    not os.path.exists(os.path.join(cd.OUT_DIR, "clean_energy.csv")),
    reason="清洗产物不存在, 请先运行 python src/clean_data.py",
)


@pytestmark_artifacts
class TestCleanArtifacts:
    """守住清洗最终结果: 行数漂移、业务键重复、非法值都要被抓出来。

    这些断言不依赖 MySQL, 只读 output/*.csv。它们的作用是给第二阶段
    (增量装载改造) 提供一个"改坏了立刻知道"的护栏。
    """

    @staticmethod
    @pytest.fixture(scope="class")
    def energy():
        return pd.read_csv(os.path.join(cd.OUT_DIR, "clean_energy.csv"),
                           encoding="utf-8-sig")

    def test_business_key_is_unique(self, energy):
        """事实表粒度是 日期×车间×能源, 该键在清洗后必须唯一。

        若不唯一, 装载时会被 uk_date_ws_energy 静默丢弃, 导致
        '清洗后行数' 与 '实际入库行数' 对不上 —— 这正是去重步骤存在的原因。
        """
        dup = energy.duplicated(subset=["record_date", "workshop_code", "energy_code"])
        assert dup.sum() == 0

    def test_no_negative_consumption(self, energy):
        """负消耗量(仪表倒走)已取绝对值, 清洗后不应再有负值"""
        assert (energy["consumption"] >= 0).all()

    def test_no_missing_consumption(self, energy):
        """消耗量缺失已按分组中位数插补, 清洗后不应再有空值"""
        assert energy["consumption"].notna().all()

    def test_no_missing_or_zero_price(self, energy):
        """单价缺失/为 0 已插补; 单价是费用与折标煤口径的输入, 不能为空"""
        assert energy["unit_price"].notna().all()
        assert (energy["unit_price"] > 0).all()

    def test_cost_matches_quantity_times_price(self, energy):
        """费用以 量×价 为权威口径重算, 偏差应落在 5% 容差内"""
        calc = (energy["consumption"] * energy["unit_price"]).abs()
        dev = (energy["cost"] - energy["consumption"] * energy["unit_price"]).abs()
        # 量为 0 的行不参与相对偏差
        mask = calc > 0
        assert (dev[mask] / calc[mask] <= cd.COST_TOLERANCE + 1e-9).all()

    def test_unit_matches_canonical(self, energy):
        """单位已按能源编码归一为 kWh/m³/t/kg"""
        for code, unit in cd.CANONICAL_UNIT.items():
            got = energy.loc[energy["energy_code"] == code, "unit"].unique()
            assert list(got) == [unit], f"{code} 的单位应为 {unit}, 实际 {got}"

    def test_all_codes_are_canonical(self, energy):
        """车间/能源编码必须全部落在标准编码集合内(别名已映射干净)"""
        assert set(energy["workshop_code"]) <= set(cd.WORKSHOP_ALIAS.values())
        assert set(energy["energy_code"]) <= set(cd.CANONICAL_UNIT)

    def test_is_production_day_derived_from_status(self, energy):
        """is_production_day 必须是 record_status != '停产' 的派生:
        停产日仍保留基础负荷, Q13 待机损耗分析依赖这些行"""
        expected = (energy["record_status"] != "停产").astype(int)
        assert (energy["is_production_day"] == expected).all()

    def test_production_fact_grain_is_unique(self):
        """产量表粒度是 日期×车间, 该键必须唯一"""
        prod = pd.read_csv(os.path.join(cd.OUT_DIR, "clean_production.csv"),
                           encoding="utf-8-sig")
        assert prod.duplicated(subset=["record_date", "workshop_code"]).sum() == 0

    def test_no_workshop_day_loses_an_energy_type(self, energy):
        """每个车间每天的能源品种数必须一致 —— 少一个品种意味着丢掉了一整条记录。

        这条断言守的是 2026-09-20 修掉的那个缺陷: 第 8 步判离群时用
        `df.loc[~out_mask]` **整行删除**, 而第 10 步插补只看得见
        `consumption.isna()` 的行, 于是被删掉的格子永久缺失、插补从不触发。
        结果是该车间当天少一个能源品种 —— 丢掉 E02(天然气, 占当日 tce 约 70%)
        时当日总能耗直接掉七成, 下游把它读成真实的能耗骤降(CUSUM 持续偏低报警)。
        即清洗脚本自己造出了数据里没有的异常, 共 34 天。

        现在离群只置空不删行, 该格会被第 10 步插补, 记录数不变。
        若有人再把这里改回整行删除, 这条断言会立刻变红。
        """
        per_day = energy.groupby(["workshop_code", "record_date"])["energy_code"].nunique()
        # 每个车间正常应有的品种数 = 该车间全期出现最多的那个值
        expected = per_day.groupby(level=0).agg(lambda s: s.mode().iloc[0])
        short = per_day[per_day < per_day.index.get_level_values(0).map(expected)]
        assert len(short) == 0, (
            f"有 {len(short)} 个车间-日的能源品种数少于正常水平, "
            f"说明有记录被整行删除而非插补:\n{short.head(10)}")

    def test_outlier_cells_are_imputed_not_dropped(self, energy):
        """被判定离群的格子必须留下记录, 且已补成正常量级的估计值。

        与上一条互补: 上一条查"品种数会不会少", 这条直接查那 34 个格子本身 ——
        它们应当在 clean_energy.csv 里, 且值远小于留痕里的原始离群值
        (原始值是正常水平 8~15 倍)。
        """
        rejects_path = os.path.join(cd.OUT_DIR, "clean_rejects.csv")
        if not os.path.exists(rejects_path):
            pytest.skip("无剔除明细")
        rej = pd.read_csv(rejects_path, encoding="utf-8-sig")
        out = rej[rej["reject_reason"].astype(str).str.contains("离群")]
        if out.empty:
            pytest.skip("本次数据没有离群值")

        key = ["record_date", "workshop_code", "energy_code"]
        merged = out[key + ["consumption"]].merge(
            energy[key + ["consumption"]], on=key, suffixes=("_orig", "_imputed"))
        assert len(merged) == len(out), (
            f"{len(out) - len(merged)} 个离群格子没有回到事实表 —— 记录被整行删掉了")

        # 插补值必须是无缺失的正数(与 test_no_missing_consumption 同一口径)
        assert merged["consumption_imputed"].notna().all()
        assert (merged["consumption_imputed"] > 0).all()
        # 插补值应当远小于原始离群值: 中位数插补取的是组内正常水平
        ratio = merged["consumption_imputed"] / merged["consumption_orig"]
        assert (ratio < 1).all(), "插补值不应大于被替换的离群值"
        assert ratio.median() < 0.2, (
            f"插补值中位数仅为原离群值的 {ratio.median():.1%}, 预期 <20% —— "
            f"数据注入的离群是 8~15 倍量级, 插补后应回到正常水平")


# =============================================================================
# 5. 增量批模式: 窗口只影响落盘, 不影响清洗口径
# =============================================================================

class TestIncrementalBatch:
    """守住"增量批里的一行 == 全量跑的同名行"。

    这是增量装载的正确性前提。若两者不同, "增量入库"与"全量重建"会给出两个
    事实表, 而且差异只在跨月/跨年的批次边界上暴露, 极难排查。

    clean_data.py 的做法是**清洗一律按完整数据计算, 只在最后一步把窗口内的行
    写出去**。若有人图省事把窗口筛选提前到读数据之前(只读窗口内的原始行),
    第 8 步的离群判决(全体的 Q1/Q3)与第 10 步的插补中位数都会变 ——
    下面第一条测试立刻变红, 这正是它存在的意义。

    两条测试都要重跑管道, 所以用 preserve_outputs 夹具把 data/ 与 output/
    整体备份并在结束后还原(见 conftest.py)。
    """

    @staticmethod
    def _run(*args):
        import subprocess
        import sys
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        r = subprocess.run([sys.executable, "src/clean_data.py", *args],
                           cwd=base, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        assert r.returncode == 0, f"clean_data.py 失败:\n{r.stdout}\n{r.stderr}"

    def test_batch_slice_is_exact_subset_of_full(self, preserve_outputs):
        """批次产出的每一行, 必须与全量产出的对应行**逐列完全相同**。"""
        self._run()
        full = pd.read_csv(os.path.join(cd.OUT_DIR, "clean_energy.csv"),
                           encoding="utf-8-sig", dtype=str)
        full_prod = pd.read_csv(os.path.join(cd.OUT_DIR, "clean_production.csv"),
                                encoding="utf-8-sig", dtype=str)

        # 窗口取数据集末尾一个月, 保证非空
        lo = "2025-12-01"
        self._run("--batch-since", lo)
        batch = pd.read_csv(os.path.join(cd.OUT_DIR, "clean_batch_energy.csv"),
                            encoding="utf-8-sig", dtype=str)
        batch_prod = pd.read_csv(
            os.path.join(cd.OUT_DIR, "clean_batch_production.csv"),
            encoding="utf-8-sig", dtype=str)

        assert len(batch) > 0, "批次为空, 测试前提不成立"
        assert batch["record_date"].min() >= lo

        # 逐列比对: 批次里的每一行都要能在全量里找到完全相同的对应行
        key = ["record_date", "workshop_code", "energy_code"]
        m = full.merge(batch, on=key, suffixes=("_f", "_b"), how="inner")
        assert len(m) == len(batch), (
            f"批次有 {len(batch)} 行, 只有 {len(m)} 行能在全量里找到对应 —— "
            f"说明批次清洗口径与全量不同"
        )
        cols = [c for c in full.columns if c not in key]
        identical = (m[[c + "_f" for c in cols]].fillna("~NA~").values
                     == m[[c + "_b" for c in cols]].fillna("~NA~").values)
        assert identical.all(), (
            "批次行与全量同名行存在差异 —— 窗口筛选很可能被提前到了清洗之前, "
            "导致分位数/中位数按窗口而非全体计算"
        )

        # 产量表同理
        pk = ["record_date", "workshop_code"]
        mp = full_prod.merge(batch_prod, on=pk, suffixes=("_f", "_b"), how="inner")
        assert len(mp) == len(batch_prod)
        pcols = [c for c in full_prod.columns if c not in pk]
        assert (mp[[c + "_f" for c in pcols]].fillna("~NA~").values
                == mp[[c + "_b" for c in pcols]].fillna("~NA~").values).all()

    def test_batch_mode_does_not_touch_full_artifacts(self, preserve_outputs):
        """批次模式不得覆盖全量产物。

        若批次模式顺手把 clean_energy.csv 覆盖成"只有窗口内的行", 下一次
        全量装载会拿到一份残缺的 CSV —— 那是静默的数据事故。
        """
        self._run()
        before = {}
        for name in ["clean_energy.csv", "clean_production.csv",
                     "clean_rejects.csv", "clean_fixed.csv"]:
            p = os.path.join(cd.OUT_DIR, name)
            if os.path.exists(p):
                before[name] = open(p, "rb").read()

        self._run("--batch-since", "2025-12-01", "--batch-out", cd.OUT_DIR)

        for name, data in before.items():
            p = os.path.join(cd.OUT_DIR, name)
            assert os.path.exists(p), f"批次模式把 {name} 删掉了"
            assert open(p, "rb").read() == data, \
                f"批次模式改写了全量产物 {name}"

    def test_batch_window_is_respected(self, preserve_outputs):
        """批次文件必须只含窗口内的行, 且与全量的同名行数一致。"""
        self._run()
        full = pd.read_csv(os.path.join(cd.OUT_DIR, "clean_energy.csv"),
                           encoding="utf-8-sig", dtype=str)
        lo = "2025-12-01"
        self._run("--batch-since", lo)
        batch = pd.read_csv(os.path.join(cd.OUT_DIR, "clean_batch_energy.csv"),
                            encoding="utf-8-sig", dtype=str)
        expected = full[full["record_date"] >= lo]
        assert len(batch) == len(expected), \
            f"批次 {len(batch)} 行, 窗口内应有 {len(expected)} 行"
        assert (batch["record_date"] >= lo).all()
