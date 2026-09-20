# -*- coding: utf-8 -*-
"""
指标字典一致性测试 —— 全部离线, 不依赖 MySQL。

**为什么需要它**: `docs/指标字典.md` 是"指标定义"的唯一出处, 论文、答辩、
报告正文都引用它。但文档有个天然缺陷 —— 它不会跟着代码变。
改了折标煤系数、改了 CUSUM 的 h、改了 Q28 的小数位数, 代码跑得好好的,
字典却还写着旧值, 而**没有任何东西会报错**。

这份测试守的就是这件事: 把字典里那些"必须与代码一致"的断言抽出来,
直接从**代码**读真值再比对, 而不是从字典里抄一遍。

守三类东西:
  1. 折标煤 / 碳排系数 (改系数会让 29 份基线全变, 字典必须同步)
  2. CUSUM 的 k / h / 白化轮廓口径 (改参数会改变所有报警结论)
  3. 字典里引用的**结构事实** (视图名、查询号、口径归属)

不做的事: 不校验字典里的**推导性**实测值(如"天然气费用占比 49.24%"这类)。
那些是从某次运行算出的快照, 换了数据就该变, 写进测试只会变成需要天天维护的假绿。

**但要校验「口径速查表」里的基准数字**(§7: 全厂 / 剔除公用工程两个总量)。
它们与上面那些快照不同 —— 是字典作为"权威口径表"的立论基础, 论文和答辩
直接引用。而且它们**有唯一正确答案**: 就是 `tests/baseline/Q01,Q02`。
第 8 轮(#17)补上这条断言, 起因是发现它们曾静默漂移:
字典写着 `63,759.70` / `46,400.59`, 而基线是 `63,759.63` / `46,400.52` ——
29 份回归基线一份没红, 因为它们只锁 `output/Q*.csv`, 管不到 `docs/` 里的手写数字。
"""

from __future__ import annotations

import csv
import os
import re

import pytest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC_PATH = os.path.join(BASE_DIR, "docs", "指标字典.md")
SQL_DIR = os.path.join(BASE_DIR, "sql")
BASELINE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "baseline")


@pytest.fixture(scope="module")
def doc() -> str:
    if not os.path.exists(DOC_PATH):
        pytest.skip("docs/指标字典.md 不存在")
    with open(DOC_PATH, encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def create_table_sql() -> str:
    with open(os.path.join(SQL_DIR, "create_table.sql"), encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def analysis_sql() -> str:
    with open(os.path.join(SQL_DIR, "analysis.sql"), encoding="utf-8") as f:
        return f.read()


# =============================================================================
# 1. 折标煤 / 碳排系数
# =============================================================================

class TestFactors:
    """字典 §1 的系数表必须与 create_table.sql 的 dim_energy_type 逐条一致。

    这六个能源品种的系数是所有能耗/碳排数字的乘数, 改一个就会让全部
    29 份基线漂移。字典与建表脚本不一致 = 有人在两处之一改了却没同步。
    """

    @staticmethod
    def _parse_insert(sql: str) -> dict:
        """从 `INSERT INTO dim_energy_type (...) VALUES (...);` 抽出各行。"""
        m = re.search(
            r"INSERT INTO dim_energy_type\b.*?VALUES\s*(.*?);",
            sql, re.S)
        assert m, "create_table.sql 里找不到 dim_energy_type 的 INSERT"
        rows = {}
        for line in m.group(1).splitlines():
            line = line.strip().rstrip(",").strip()
            if not line.startswith("("):
                continue
            vals = [v.strip().strip("'") for v in
                    re.findall(r"'[^']*'|[-\d.]+", line)]
            # ('E01', '电力', 'kWh', 0.1229, 0.5703, 0.6800)
            rows[vals[0]] = {
                "name": vals[1], "unit": vals[2],
                "std_coal": float(vals[3]), "co2": float(vals[4]),
            }
        return rows

    def test_energy_factors_documented(self, doc, create_table_sql):
        """字典里必须出现每个能源品种的折标煤系数与碳排因子。"""
        table = self._parse_insert(create_table_sql)
        assert len(table) == 6, f"应解析出 6 个能源品种, 实际 {len(table)}"
        missing = []
        for code, v in table.items():
            for label, val in (("折标煤", v["std_coal"]), ("碳排", v["co2"])):
                if f"{val:.4f}" not in doc:
                    missing.append(f"{code} {v['name']} 的{label}系数 {val:.4f}")
        assert not missing, (
            "字典 §1 的系数表与 create_table.sql 不一致, 缺以下值:\n  - "
            + "\n  - ".join(missing)
        )

    def test_compressed_air_co2_is_zero(self, doc, create_table_sql):
        """压缩空气碳排因子必须是 0, 且字典说明了为什么。

        这是本项目一个**容易被认为是 bug** 的有意设计 (二次能源不重复计入),
        字典必须留一段解释, 否则读的人会以为漏填了。
        """
        table = self._parse_insert(create_table_sql)
        assert table["E05"]["co2"] == 0.0, "压缩空气碳排因子应保持 0"
        assert "压缩空气" in doc and "二次能源" in doc, (
            "字典必须解释压缩空气碳排因子为 0 的原因(二次能源不重复计入)"
        )

    def test_coal_factor_is_equivalent_value(self, doc):
        """字典必须写明用的是当量值而非等价值, 并给出 GB/T 2589 出处。

        这两个口径差 2.6 倍, 不写明会被问倒。
        """
        assert "当量值" in doc and "等价值" in doc, (
            "字典必须区分当量值/等价值"
        )
        assert "GB/T 2589" in doc, "字典必须给出折标煤系数的标准出处"


# =============================================================================
# 2. CUSUM 参数
# =============================================================================

class TestCusumParams:
    """字典 §5.3 的 k / h 必须与视图里写死的值一致。

    h=9.9 这个数是**标定出来的**, 不是常数: 换数据长度就要重标。
    字典写了 9.9 而视图改成别的值, 会让论文里的结论与代码对不上。
    """

    def test_k_and_h_match_view(self, doc, create_table_sql):
        # 视图里: `z_w - 0.5 AS cw_hi` 与 `lim AS (SELECT 9.9 AS h)`
        m_h = re.search(r"lim AS \(SELECT\s+([\d.]+)\s+AS h\)", create_table_sql)
        assert m_h, "在 v_unit_energy_cusum 里找不到决策限 h 的定义"
        h = m_h.group(1)
        assert f"**{h}**" in doc or f"h = {h}" in doc or f"取 9.9" in doc, (
            f"视图里的 h={h} 未在字典中出现"
        )

        # 松弛量 k: 视图里写死的 0.5
        assert "0.5" in create_table_sql, "视图里应能找到松弛量 k=0.5"
        assert re.search(r"松弛量.*0\.5|\*\*0\.5\*\*", doc), (
            "字典 §5.3 必须写明松弛量 k=0.5"
        )

    def test_h_is_not_textbook_five(self, doc):
        """字典必须解释"为什么不是教科书的 5σ" —— 这是最常被追问的一点。"""
        assert "9.9" in doc and "教科书的 5" in doc, (
            "字典必须解释 h 为什么取 9.9 而不是教科书的 5σ"
        )
        assert "零假设" in doc or "白噪声" in doc, (
            "字典必须说明 h 由零假设仿真标定"
        )

    def test_calibration_script_referenced(self, doc):
        """标定脚本必须存在且被字典引用 —— 否则 h=9.9 不可复核。"""
        script = os.path.join(BASE_DIR, "tools", "calibrate_cusum_h.py")
        assert os.path.exists(script), (
            "tools/calibrate_cusum_h.py 应存在, 否则 h=9.9 无法复核"
        )
        assert "calibrate_cusum_h.py" in doc, (
            "字典必须给出 h 的复现脚本路径"
        )

    def test_whitening_uses_calendar_month(self, doc, create_table_sql):
        """白化必须按"自然月"而非"年+月", 字典要写明理由。

        用年+月会把样本量砍半; 年际差异属趋势, 归 CUSUM 抓, 不该混进季节。
        """
        assert re.search(r"MONTH\(record_date\)", create_table_sql), (
            "视图里白化轮廓应按 MONTH() 分组"
        )
        assert "自然月" in doc, "字典必须写明白化按自然月估计"
        # 字典应说明"分年估计"是被否决的方案及其理由
        assert "样本量砍半" in doc or "砍半" in doc, (
            "字典应说明为什么不按 年+月 分开估计"
        )


# =============================================================================
# 3. 口径归属 (哪些查询用哪个口径)
# =============================================================================

class TestScopeAttribution:
    """字典 §0 的口径表列了每条查询用哪个口径。这张表一旦写错,
    读者会拿错误的数字去回答答辩问题。"""

    @staticmethod
    def _queries_with(sql: str, predicate) -> set:
        """按 `-- @@name Qxx_...` 切分, 返回满足 predicate 的查询号集合。"""
        out = set()
        blocks = re.split(r"-- @@name\s+(Q\d+)_", sql)
        # blocks: [前言, Q01, body, Q02, body, ...]
        for i in range(1, len(blocks) - 1, 2):
            qid, body = blocks[i], blocks[i + 1]
            if predicate(body):
                out.add(qid)
        return out

    def test_excluded_scope_queries_really_exclude(self, analysis_sql):
        """字典说是"剔除公用工程"的查询, SQL 里必须真的写了那个条件。"""
        excluded = self._queries_with(
            analysis_sql, lambda b: "process_type <> '公用工程'" in b)
        # 字典 §0 明确列出的一批(视图内部剔除的 Q25/Q26/Q27 不在此列,
        # 它们的剔除发生在 v_unit_energy_baseline 里)
        should = {"Q02", "Q07", "Q08", "Q16", "Q20", "Q24"}
        assert should <= excluded, (
            f"字典标注为剔除口径但这些查询的 SQL 里没有剔除条件: "
            f"{sorted(should - excluded)}"
        )

    def test_continuous_only_queries_use_is_continuous(self, analysis_sql):
        """字典把 Q11/Q12 单列为"仅连续型"口径, 它们的判据是 is_continuous=1。

        这一条与"剔除公用工程"**不是同一个条件**: is_continuous=1 恰好排除了
        W07(它是 continuous), 但语义是"只取全年不停产的车间" —— 目的是排除
        停产日对气温信号的干扰, 不是为了去重复计算。两者在本数据集上结论一致,
        但理由不同, 字典必须分开列(§0 已经分开)。
        """
        cont = self._queries_with(analysis_sql, lambda b: "is_continuous = 1" in b)
        should = {"Q11", "Q12"}
        assert should <= cont, (
            f"字典标注为仅连续型但这些查询没有 is_continuous=1: "
            f"{sorted(should - cont)}"
        )
        # 反向: Q11/Q12 **不应**同时带剔除条件, 否则字典的归类是错的
        excl = self._queries_with(
            analysis_sql, lambda b: "process_type <> '公用工程'" in b)
        assert not (should & excl), (
            f"Q11/Q12 若已用 is_continuous, 字典不应把它们归到剔除口径: "
            f"{sorted(should & excl)}"
        )

    def test_baseline_view_excludes_utility(self, doc, create_table_sql):
        """Q25/Q26/Q27 的剔除在视图里, 字典必须说清这一点。"""
        m = re.search(
            r"CREATE VIEW v_unit_energy_baseline AS(.*?);\s*\n--",
            create_table_sql, re.S)
        assert m, "找不到 v_unit_energy_baseline 的定义"
        assert "process_type <> '公用工程'" in m.group(1), (
            "v_unit_energy_baseline 应剔除公用工程"
        )
        assert "v_unit_energy_baseline" in doc

    def test_utility_workshop_code_documented(self, doc, create_table_sql):
        """字典必须点明 W07 就是公用工程 —— 全篇讲口径都要用到它。"""
        assert "W07" in doc and "动力站" in doc
        m = re.search(r"'W07',\s*'([^']+)',\s*'([^']+)'", create_table_sql)
        assert m, "create_table.sql 里找不到 W07"
        assert m.group(2) == "公用工程", f"W07 的工序应为公用工程, 实际 {m.group(2)}"
        assert m.group(1) in doc, "字典应使用 W07 的标准车间名"


# =============================================================================
# 4. 字典自身的结构完整性
# =============================================================================

class TestDocStructure:
    """字典要能当论文附录用, 结构上必须自洽。"""

    def test_has_required_sections(self, doc):
        for sec in ["## 1. 基础物理量", "## 2. 总量类指标", "## 3. 强度类指标",
                    "## 4. 结构类指标", "## 5. 异常检测类指标",
                    "## 6. 前端动态筛选", "## 7. 口径速查表"]:
            assert sec in doc, f"字典缺少章节: {sec}"

    def test_units_always_given(self, doc):
        """每个指标都要能被回答"单位是什么"。

        本项目单位混用严重(kgce/tce/t/kg/m³/kWh/元/tCO2), 列名不带单位
        是这类项目最容易犯的错。
        """
        for unit in ["tce", "kgce", "tCO2", "元"]:
            assert unit in doc, f"字典应出现单位 {unit}"

    def test_null_semantics_documented(self, doc):
        """单耗的 NULL 语义必须写明 —— 误当 0 会让单耗变无穷大。"""
        assert "NULL" in doc and "无穷大" in doc, (
            "字典必须写明'单耗可为 NULL 但不可为 0'及其后果"
        )

    def test_additivity_rule_documented(self, doc):
        """单耗不可加、前端只收可加量 —— 这是 #14 的数据契约。"""
        assert "可加" in doc, "字典必须写明可加量契约"
        assert "3,214" in doc and "3,658" in doc, (
            "字典应给出费用/能耗比值的实测摆动区间, 说明为何不能传预算系数"
        )

    def test_report_hardcoded_numbers_warning(self, doc):
        """报告正文数字是手写死的, 这条维护约定必须在字典里。

        这是 README 更正横幅里记过的一个真实事故类别。
        """
        assert "手写死" in doc or "手写" in doc, (
            "字典必须提醒: 报告正文数字是手写的, 改数据后要逐个核对"
        )

    def test_matches_source_paths(self, doc):
        """字典引用的文件必须真实存在。"""
        for rel in ["sql/create_table.sql", "sql/analysis.sql",
                    "src/generate_data.py", "src/clean_data.py"]:
            assert rel in doc, f"字典应引用 {rel}"
            assert os.path.exists(os.path.join(BASE_DIR, *rel.split("/"))), (
                f"字典引用了不存在的文件: {rel}"
            )


# =============================================================================
# 5. 口径速查表的基准数字必须与回归基线一致
# =============================================================================

class TestMeasuredBaselineNumbers:
    """字典 §0 与 §7 引用的两个总量, 必须逐位等于 Q01/Q02 回归基线。

    **为什么这条断言不该被"实测值不校验"那条豁免掉**:

    字典里绝大多数实测值(占比、降幅、检出数)确实不该写进测试 —— 换数据就变。
    但"全厂用能 / 真实用能"这两个数不一样:

      1. 它们是字典的**立论基础**(§7 速查表第一、二行), 论文和答辩会直接引用;
      2. 它们有**唯一正确答案** —— Q01/Q02 基线。没有"换数据就该变"的余地:
         数据换了基线也会跟着更新, 两者始终应当一致。

    换句话说, 一条断言该不该写, 判据是"这个数有没有唯一权威来源",
    而不是"它是不是实测值"。有权威来源的就必须锁。

    守得住的实际缺陷(#17 发现): 字典曾写着 63,759.70 / 46,400.59,
    而基线是 63,759.63 / 46,400.52。四个数字错了三个量级之外的小数位,
    却没有任何测试报错 —— 因为 29 份回归基线只管 output/Q*.csv。
    """

    # 基线文件 -> 字典里对应"口径"的行标签
    CASES = [
        ("Q01_能源消费总览.csv", "全厂"),
        ("Q02_剔除公用工程后的能耗总览.csv", "剔除公用工程"),
    ]

    @staticmethod
    def _baseline_row(fname: str) -> dict:
        path = os.path.join(BASELINE_DIR, fname)
        if not os.path.exists(path):
            pytest.skip(f"基线 {fname} 不存在")
        with open(path, encoding="utf-8-sig", newline="") as f:
            return {(k or "").strip(): (v or "").strip()
                    for k, v in next(csv.DictReader(f)).items()}

    def test_speedtable_totals_match_baseline(self, doc):
        """§7 速查表里两个总量(tce)必须等于基线, 且千分位写法一致。"""
        for fname, scope in self.CASES:
            row = self._baseline_row(fname)
            # 基线给出 6 位小数(63759.63), 字典用千分位 + 2 位(63,759.63)
            tce = float(row["综合能耗_tce"])
            shown = f"{tce:,.2f}"
            assert shown in doc, (
                f"字典未出现 {scope} 口径的正确综合能耗 {shown} tce "
                f"(来自 {fname})。若字典写的是别的值, 说明它漂移了 —— "
                f"这正是本测试要守的缺陷类别。"
            )

    def test_scope_ratio_matches_baseline(self, doc):
        """字典里的"剔除/全厂"比值必须由基线现算出来, 不能手写。"""
        full = float(self._baseline_row("Q01_能源消费总览.csv")["综合能耗_tce"])
        excl = float(self._baseline_row("Q02_剔除公用工程后的能耗总览.csv")["综合能耗_tce"])
        assert full > 0, "基线全厂能耗应大于 0"
        ratio = excl / full * 100
        assert f"{ratio:.2f}%" in doc, (
            f"字典应出现由基线现算的比值 {ratio:.2f}% "
            f"(剔除 {excl} / 全厂 {full})"
        )

    def test_no_stale_measured_values(self, doc):
        """字典里**不得**残留任何与基线矛盾的"两位小数"全厂总量。

        反向断言: 若字典出现一个"看起来就是全厂总量"的两位小数写法
        (如 63,759.70) 而不等于基线, 直接报错并点名。防的是"改了速查表、
        别处还漏了一处"。

        **为什么只查两位小数**: 字典里刻意会引用**错误的**近似写法作反面例子
        (如"只留 4 位就成了 63,759.6")。那类写法位数不同、语义上是"被截断的
        例子"而不是"结论", 不该被误报。而两位小数 `63,759.xx` 正是速查表
        和报告正文使用的格式 —— 出现就代表一个被引用的结论。
        """
        row = self._baseline_row("Q01_能源消费总览.csv")
        tce = float(row["综合能耗_tce"])
        prefix = f"{int(tce):,}"          # "63,759"
        suspects = set(re.findall(prefix + r"\.\d{2}(?!\d)", doc))
        allowed = {f"{tce:,.2f}"}
        stale = suspects - allowed
        assert not stale, (
            f"字典残留了与基线不符的全厂总量: {sorted(stale)} "
            f"(基线是 {sorted(allowed)} 即 {tce})"
        )
