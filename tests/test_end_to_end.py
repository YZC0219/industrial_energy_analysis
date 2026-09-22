# -*- coding: utf-8 -*-
"""
端到端集成验收 (#17) —— 需要 MySQL, 且会**真的重跑整条管道**, 默认跳过。

    python -m pytest -m db -m slow tests/test_end_to_end.py

**为什么需要它**: 这个仓库已经有两层测试, 但都够不到"整条链路"这一层:

  - `test_clean_data.py` / `test_metric_dictionary.py` 是**单元级**, 离线,
    只断言单个函数的输出或文档与代码的一致性;
  - `test_analysis_snapshot.py` 是**回归级**, 逐行比对 29 条查询与基线 ——
    但它只读 `output/Q*.csv`, 不关心这些 CSV 是**哪一次**跑出来的。

于是有一个盲区: 每一步单独看都对, 但**步骤之间的衔接**错了。实际上已经踩过
不止一次 —— README 的「一个必须遵守的流程」那一节记的就是其中一类:

  - **张冠李戴**: `report.html` 是用**上一次**的 Q*.csv 生成的, 而 Q*.csv 又
    是用**上上次**的 clean_*.csv 生成的。每个文件本身都"合法", 但拼起来是
    一份不属于同一次运行的报告。
  - **产物缺失/陈旧**: 某个 Q*.csv 没生成成功, 或时间戳停在上一轮, 而回归
    基线恰好因为环境不同步也跟着"绿"了 —— 假象。

这份测试守的就是这些: **从零跑一遍完整管道, 然后断言产物齐全、新鲜、且三路
数字自洽**。它不重复回归基线的工作(逐行比数值), 而是回答回归基线回答不了的
问题: "这些产物是不是同一次跑出来的、是不是全都在?"

### 三路自洽是什么

README 与字典都声明了一个不变式 —— 同一个合计有三条独立路径可以得到它:

    路径 A  Q01 全厂总览(直接聚合事实表)
    路径 B  Q28 各车间日度立方体, sum(tce)     (前端 KPI 卡的底座)
    路径 C  report.html 内联 JSON 的 totals.tce (报告首屏显示的数)

三路必须相等。路径 A 与 B 相等证明"立方体没有丢行/多行"; B 与 C 相等证明
"报告用的就是这份立方体, 不是上一轮的"。任何一步衔接错位, 这三条就会分叉。

### 怎么跑

管道脚本的输出路径是**写死的**(没有可配置的输出目录), 所以本测试用
`preserve_outputs` 夹具先把 `data/` 与 `output/` 整体备份, 跑完原样还原 ——
绝不污染工作区里已有的产物。
"""

from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys

import pytest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(BASE_DIR, "src")
OUT_DIR = os.path.join(BASE_DIR, "output")
DATA_DIR = os.path.join(BASE_DIR, "data")

# 整条管道, 与 dags/energy_pipeline_dag.py 的 5 个 task 一一对应。
# 顺序即依赖顺序, 不能换。
#
# 这里的命令必须与 DAG 里的**逐字一致** —— 这份 PIPELINE 的意义就是"用与生产
# 相同的命令行跑一遍"。曾经 load_warehouse 是 `--init`, 水位线落地后改成了
# `--incremental`, 且 `--init` 单独使用会被运行期拒绝(它会清空事实表却留下水位线,
# 之后的数据被永久跳过)。
#
# 但端到端测试要的是**从零可复现**: 它必须能在一个已有水位线的库上重跑并得到
# 完全相同的结果。所以这里用 `--init --full` —— 显式重建 + 全量装载 + 重置水位线,
# 这正是"首次部署"的语义, 也是唯一能让测试不依赖历史状态的组合。
PIPELINE = [
    ("generate_raw_data", ["src/generate_data.py"]),
    ("clean_data",        ["src/clean_data.py", "--batch-all"]),
    ("load_warehouse",    ["src/import_mysql.py", "--init", "--full"]),
    ("run_analysis",      ["src/import_mysql.py", "--run-analysis"]),
    ("build_report",      ["src/make_report.py"]),
]

pytestmark = [pytest.mark.db, pytest.mark.slow]

# 29 条查询的产物名(不含扩展名)。Q24 在 analysis.sql 里排在 Q28/Q29 之后,
# 但产物文件的编号是完整的 Q01..Q29, 少任何一个都是链路断了一节。
EXPECTED_QUERIES = [f"Q{i:02d}" for i in range(1, 30)]


def _run(script_args: list[str], env: dict) -> subprocess.CompletedProcess:
    """在项目根目录下跑一个管道脚本, 失败即抛(带完整 stdout/stderr)。"""
    proc = subprocess.run(
        [sys.executable, *script_args],
        cwd=BASE_DIR, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        pytest.fail(
            f"管道步骤 `{' '.join(script_args)}` 退出码 {proc.returncode}\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        )
    return proc


# 整条管道只跑一次, 结果缓存供本模块所有测试共用。
_RUN_CACHE: dict = {}


@pytest.fixture(scope="module")
def pipeline_run(db_params):
    """完整跑一遍管道, 返回每一步的 stdout 供后续断言引用(整模块只跑一次)。

    **为什么自己备份/还原, 而不用 conftest 的 `preserve_outputs`**:
    那个夹具是 function-scoped 的 —— 它在**每个测试函数结束后**就把
    `data/`+`output/` 还原成原样。但本模块的设计是"跑一次、后面十几个
    断言都读同一批新鲜产物", 于是第二次测试开始时, 产物已被还原成**旧文件**,
    而缓存的日志还停留在新跑的那次 —— 断言的依据与磁盘上的东西对不上,
    表现为随机失败(取决于哪个测试先跑)。

    所以这里在 **module 作用域**上做同样的事: 进入时备份一次, 整模块共享
    跑出来的新鲜产物, 退出时还原一次。语义与 `preserve_outputs` 相同
    (绝不污染工作区), 但生命周期与"读同一批产物"的需求匹配。
    """
    import shutil
    import tempfile

    if "logs" in _RUN_CACHE:
        return _RUN_CACHE["logs"]

    env = dict(os.environ)
    env["MYSQL_HOST"] = db_params["host"]
    env["MYSQL_PORT"] = str(db_params["port"])
    env["MYSQL_USER"] = db_params["user"]
    env["MYSQL_PWD"] = db_params["password"]
    env.setdefault("PYTHONIOENCODING", "utf-8")

    with tempfile.TemporaryDirectory(prefix="energy_e2e_") as tmp:
        for name, src in (("data", DATA_DIR), ("output", OUT_DIR)):
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(tmp, name))
        try:
            logs = {}
            for task_id, args in PIPELINE:
                logs[task_id] = _run(args, env).stdout
            _RUN_CACHE["logs"] = logs
            yield logs
        finally:
            for name, dst in (("data", DATA_DIR), ("output", OUT_DIR)):
                backup = os.path.join(tmp, name)
                if os.path.isdir(backup):
                    if os.path.isdir(dst):
                        shutil.rmtree(dst)
                    shutil.copytree(backup, dst)


def _read_csv(name: str) -> list[dict]:
    path = os.path.join(OUT_DIR, name)
    assert os.path.exists(path), f"产物缺失: output/{name}"
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [{(k or "").strip(): (v or "").strip() for k, v in row.items()}
                for row in csv.DictReader(f)]


def _f(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _report_payload() -> dict:
    """把 make_report.py 内联进 report.html 的那段 JSON 读回来。

    报告的契约是「自包含单文件」: 页面不 fetch 任何外部数据, 所有图都从这段
    内联 JSON 渲染。所以读它 = 读到用户实际看到的每一个数。

    **为什么不能靠 `/*__END__*/` 收尾**: 生成时 make_report.py 把整个区间
    (含两个标记本身)替换成 JSON, 所以产物里**只剩起始标记** `/*__DATA__*/`,
    后面直接跟 JSON、再跟 `;`。这里用括号配平扫描出 JSON 的结尾 ——
    比找 `;` 稳, 因为字符串里可能带分号。
    """
    path = os.path.join(OUT_DIR, "report.html")
    assert os.path.exists(path), "产物缺失: output/report.html"
    with open(path, encoding="utf-8") as f:
        html = f.read()
    marker = "/*__DATA__*/"
    i = html.find(marker)
    assert i != -1, (
        "report.html 里找不到 /*__DATA__*/ 标记 —— "
        "模板被改坏了, 或报告是用旧版 make_report.py 生成的"
    )
    start = html.index("{", i + len(marker))
    depth, in_str, esc = 0, False, False
    for k in range(start, len(html)):
        ch = html[k]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(html[start:k + 1])
    raise AssertionError("report.html 里的内联 JSON 括号不配平, 文件可能被截断")


# =============================================================================
# 1. 全链路跑通: 每一步都成功, 且产出它该产出的东西
# =============================================================================

class TestPipelineCompletes:

    def test_all_steps_succeeded(self, pipeline_run):
        """五个步骤全部执行且退出码为 0(失败会在 _run 里直接 fail)。"""
        assert set(pipeline_run) == {t for t, _ in PIPELINE}, (
            f"实际跑到的步骤与 DAG 定义不一致: {sorted(pipeline_run)}"
        )

    def test_clean_report_exists(self, pipeline_run):
        """clean_report.txt 是清洗阶段的留痕, 它必须生成且非空。

        这是数据管道的"可解释性"凭据: 出问题时需要能回答"这条记录去哪了"。
        """
        path = os.path.join(OUT_DIR, "clean_report.txt")
        assert os.path.exists(path), "产物缺失: output/clean_report.txt"
        with open(path, encoding="utf-8") as f:
            body = f.read()
        for key in ["原始记录数", "数据保留率", "剔除", "修正"]:
            assert key in body, f"clean_report.txt 缺少「{key}」段落"

    def test_all_29_query_csvs_exist(self, pipeline_run):
        """29 条查询必须一条不落地导出。

        少一条 = 某条 SQL 报错了, 而 import_mysql.py 是"单条失败不中断整批"
        的设计 —— 它会继续跑完并打印「完成 N/29」, **但退出码仍是 0**。
        所以这里必须显式数文件, 不能只靠 _run 的退出码。
        """
        have = {f.split("_")[0] for f in os.listdir(OUT_DIR)
                if f.endswith(".csv") and re.match(r"^Q\d+_", f)}
        missing = [q for q in EXPECTED_QUERIES if q not in have]
        assert not missing, (
            f"以下查询的结果 CSV 未生成: {missing}\n"
            f"run_analysis 日志末尾:\n{pipeline_run['run_analysis'][-800:]}"
        )

    def test_run_analysis_reports_full_success(self, pipeline_run):
        """import_mysql.py 自己打印的「完成 N/29」必须是 29/29。

        与上一条互补: 上一条查文件系统, 这一条查脚本的自述 —— 两者不一致
        (比如文件在但脚本说 28/29)说明有查询静默失败但留下了旧文件。
        """
        log = pipeline_run["run_analysis"]
        m = re.search(r"完成\s+(\d+)/(\d+)\s+条查询", log)
        assert m, f"run_analysis 输出里找不到完成计数:\n{log[-800:]}"
        done, total = int(m.group(1)), int(m.group(2))
        assert done == total == 29, f"分析查询完成 {done}/{total}, 应为 29/29"

    def test_report_generated(self, pipeline_run):
        """report.html 必须生成, 且自包含(数据内联, 不依赖外部文件)。"""
        path = os.path.join(OUT_DIR, "report.html")
        assert os.path.exists(path), "产物缺失: output/report.html"
        payload = _report_payload()
        for key in ["totals", "workshops", "energy_mix", "monthly",
                    "unit_series", "baseline", "cusum", "ws_daily",
                    "energy_daily", "filter_meta"]:
            assert key in payload, f"报告内联数据缺少 `{key}`"


# =============================================================================
# 2. 产物新鲜度: 全都属于同一次运行
# =============================================================================

class TestArtifactFreshness:
    """守"张冠李戴" —— 每个文件都合法, 但拼起来不属于同一次运行。

    README「一个必须遵守的流程」给的判据是看时间戳: 上游产物必须比下游旧。
    这里把它形式化 —— 因为这是本项目**反复踩过**的坑, 值得一条自动断言,
    而不是靠人记得 `ls -la`。
    """

    def test_q_csvs_newer_than_clean_csvs(self, pipeline_run):
        """Q*.csv(分析产物)必须比 clean_*.csv(清洗产物)新。

        反了就意味着: 分析跑的是新的清洗结果, 但清洗文件后来又被重跑覆盖过
        —— 此时 Q*.csv 与干净数据已经脱钩, 比对基线会得到无意义的结论。
        """
        ref = os.path.getmtime(os.path.join(OUT_DIR, "clean_energy.csv"))
        for q in EXPECTED_QUERIES:
            matches = [f for f in os.listdir(OUT_DIR)
                       if f.startswith(q + "_") and f.endswith(".csv")]
            assert matches, f"{q} 的产物不存在"
            mt = os.path.getmtime(os.path.join(OUT_DIR, matches[0]))
            assert mt >= ref, (
                f"{matches[0]} 比 clean_energy.csv 还旧 —— "
                f"分析产物与清洗数据不同源, 属于「张冠李戴」"
            )

    def test_report_newer_than_all_queries(self, pipeline_run):
        """report.html 必须比它读的每一个 Q*.csv 都新。

        这条正是报告"用旧数据出图"的直接判据: 报告比某个 CSV 旧, 说明那个
        CSV 是报告生成之后才重跑的 —— 报告里显示的是上一轮的数。
        """
        rpt = os.path.getmtime(os.path.join(OUT_DIR, "report.html"))
        newer = []
        for q in EXPECTED_QUERIES:
            matches = [f for f in os.listdir(OUT_DIR)
                       if f.startswith(q + "_") and f.endswith(".csv")]
            if matches and os.path.getmtime(os.path.join(OUT_DIR, matches[0])) > rpt:
                newer.append(matches[0])
        assert not newer, (
            f"以下 CSV 比 report.html 还新, 报告显示的是上一轮的数: {newer}"
        )


# =============================================================================
# 3. 三路数字自洽: Q01 == Q28 求和 == 报告首屏
# =============================================================================

TOL = 0.05   # Q*.csv 里的折标煤带 6 位小数, 5,848 行累加的截断误差落在此内


class TestThreeWayConsistency:
    """回归基线锁的是"每一条查询的每一行", 这里锁的是"查询之间对不对得上"。

    两者互补: 基线能发现"某条 SQL 算错了", 但发现不了"两条 SQL 口径不一致
    —— 各自都按自己的定义算对了, 合起来却互相矛盾"。本项目 #14 之后, 前端
    KPI 卡完全依赖 Q28 立方体, 一旦它与 Q01 分叉, 页面首屏与总览就会打架。
    """

    def test_q01_equals_q28_sum(self, pipeline_run):
        """路径 A == 路径 B: 全厂总览 == 车间日立方体求和(综合能耗)。"""
        q01 = _read_csv("Q01_能源消费总览.csv")[0]
        total = _f(q01["综合能耗_tce"])
        cube = sum(_f(r["综合能耗_tce"]) for r in _read_csv("Q28_各车间日度能耗与产量.csv"))
        assert abs(total - cube) <= TOL, (
            f"Q01 全厂综合能耗 {total} != Q28 立方体求和 {cube:.4f} "
            f"(差 {abs(total - cube):.4f}, 容差 {TOL})"
        )

    def test_q01_equals_q28_sum_for_cost_and_co2(self, pipeline_run):
        """费用与碳排也要对上 —— 三个可加量同源, 不能只有能耗对。"""
        q01 = _read_csv("Q01_能源消费总览.csv")[0]
        rows = _read_csv("Q28_各车间日度能耗与产量.csv")
        for col, cube_col in (("能源费用_元", "能源费用_元"),
                              ("碳排放_tCO2", "碳排放_tCO2")):
            total = _f(q01[col])
            cube = sum(_f(r[cube_col]) for r in rows)
            assert abs(total - cube) <= max(TOL, abs(total) * 1e-6), (
                f"{col}: Q01 {total} != Q28 求和 {cube:.4f}"
            )

    def test_q28_equals_q29_sum(self, pipeline_run):
        """两个立方体必须一致: Q28(车间×日) 与 Q29(车间×日×能源) 同源。

        Q29 多了能源维度, 所以它按 (日期,车间) 汇总后应与 Q28 逐格相等。
        """
        q28 = sum(_f(r["综合能耗_tce"]) for r in _read_csv("Q28_各车间日度能耗与产量.csv"))
        q29 = sum(_f(r["折标煤_tce"]) for r in _read_csv("Q29_各车间日度能耗结构.csv"))
        assert abs(q28 - q29) <= TOL, (
            f"Q28 求和 {q28:.4f} != Q29 求和 {q29:.4f} —— 两个立方体不同源"
        )

    def test_report_hero_equals_q01(self, pipeline_run):
        """路径 C == 路径 A: 报告首屏显示的总量 == Q01 直接查库的结果。

        这条同时守住"报告是用本次产物生成的": 若报告是上一轮的, 而数据变了,
        这里就会分叉。
        """
        q01 = _read_csv("Q01_能源消费总览.csv")[0]
        totals = _report_payload()["totals"]
        assert abs(_f(totals["tce"]) - _f(q01["综合能耗_tce"])) <= TOL, (
            f"报告首屏 {totals['tce']} != Q01 {q01['综合能耗_tce']}"
        )
        assert int(totals["days"]) == int(_f(q01["统计天数"])), (
            f"报告天数 {totals['days']} != Q01 {q01['统计天数']}"
        )

    def test_report_hero_equals_q28_cube(self, pipeline_run):
        """路径 C == 路径 B: 报告首屏 == 前端 KPI 卡的立方体底座。

        报告首屏与页面筛选后的 KPI 走的是两套代码路径(一个 Python 直读 CSV,
        一个浏览器里聚合 ws_daily)。它们必须给出同一个数, 否则"筛选前后
        数字对得上"这个承诺 (#14) 就不成立。
        """
        totals = _report_payload()["totals"]
        cube = sum(r["tce"] for r in _report_payload()["ws_daily"])
        assert abs(_f(totals["tce"]) - cube) <= TOL, (
            f"报告首屏 {totals['tce']} != 前端立方体求和 {cube:.4f}"
        )

    def test_report_excluded_scope_equals_q02(self, pipeline_run):
        """报告的"剔除公用工程"口径必须来自 Q02, 不是前端现算的。"""
        q02 = _read_csv("Q02_剔除公用工程后的能耗总览.csv")[0]
        totals = _report_payload()["totals"]
        assert abs(_f(totals["tce_ex"]) - _f(q02["综合能耗_tce"])) <= TOL, (
            f"报告 tce_ex {totals['tce_ex']} != Q02 {q02['综合能耗_tce']}"
        )

    def test_filter_meta_matches_cube_extent(self, pipeline_run):
        """筛选栏的日期锚点必须等于立方体的实际首末日。

        锚点取自数据(而非报告生成日)是刻意的 —— 报告是离线快照, 用今天当
        锚点会筛出一个空集。这里断言这个契约没被破坏。
        """
        payload = _report_payload()
        ws_daily = payload["ws_daily"]
        meta = payload["filter_meta"]
        assert meta["min_date"] == min(r["d"] for r in ws_daily), "filter_meta.min_date 与立方体不符"
        assert meta["max_date"] == max(r["d"] for r in ws_daily), "filter_meta.max_date 与立方体不符"


# =============================================================================
# 3b. 报告"散文里的数字"必须由查询算出来, 不能手写死
# =============================================================================

class TestProseNumbersAreDerived:
    """讲结论的段落里那些数字, 必须能在 `derived` 里找到来源。

    **为什么需要这一组**: 报告正文的数字原来全是手写死的字面量, 与图表用的是
    同一个数据源却各写各的 —— 改了清洗/口径之后, 图表跟着变、正文不动,
    **且没有任何测试会报警**(见 docs/系统设计文档.md §9 第 6 条)。
    现在改成从 `derived` / `energy_mix` 插值, 这一组测试守住那个改造:
    既防"有人改回手写", 也防"插值的键算错了"。

    **注意边界**: 这里只测**能从查询算出来**的量。报告里还有一类"论断型数字"
    (如"CUSUM 12 段里 9 段偏高"), 是人工判读的结论, 算不出来, 仍由人核对 ——
    本测试**不**覆盖它们, 也不该假装覆盖。
    """

    def test_derived_prose_fields_exist(self, pipeline_run):
        """插值依赖的派生字段必须齐全 —— 缺一个, 正文就填个"—"。"""
        dv = _report_payload()["derived"]
        need = [
            "top4_pct", "standby_tce", "standby_cost",
            "tce_peak_ym", "tce_peak", "tce_trough_ym", "tce_trough",
            "ci_max", "ci_min", "dt_weekend_pct", "dt_holiday_pct", "mom_nov",
            "mom_nov_daily",
        ]
        missing = [k for k in need if k not in dv]
        assert not missing, f"derived 缺字段, 正文会填空白: {missing}"

    def test_top4_pct_equals_q03_top4(self, pipeline_run):
        """正文"前四名合计占 X%"必须等于 Q03 前四名的占比之和。"""
        rows = _read_csv("Q03_各车间综合能耗排名.csv")
        want = sum(_f(r["能耗占比_pct"]) for r in rows[:4])
        got = _report_payload()["derived"]["top4_pct"]
        assert abs(got - want) <= 0.05, f"top4_pct {got} != Q03 前四名之和 {want}"

    def test_standby_totals_equal_q13(self, pipeline_run):
        """正文的待机合计必须等于 Q13 明细之和。"""
        rows = _read_csv("Q13_停产日待机损耗分析.csv")
        dv = _report_payload()["derived"]
        want_tce = sum(_f(r["待机能耗_tce"]) for r in rows if r["待机能耗_tce"])
        want_cost = sum(_f(r["待机浪费_元"]) for r in rows if r["待机浪费_元"])
        assert abs(_f(dv["standby_tce"]) - want_tce) <= 0.05, (
            f"standby_tce {dv['standby_tce']} != Q13 之和 {want_tce}"
        )
        assert abs(_f(dv["standby_cost"]) - want_cost) <= 0.5, (
            f"standby_cost {dv['standby_cost']} != Q13 之和 {want_cost}"
        )

    def test_peak_trough_match_q05(self, pipeline_run):
        """正文点名的峰谷月与数值, 必须与 Q05 的 argmax/argmin 一致。

        连**年月**一起断言: 只测数值的话, 数值随数据变了而月份还停在旧值,
        读者看到的就是"2025-12 显示 3000 tce"这种对不上的组合。
        """
        rows = _read_csv("Q05_月度能耗趋势与环比.csv")
        rows = [r for r in rows if r["综合能耗_tce"]]
        peak = max(rows, key=lambda r: _f(r["综合能耗_tce"]))
        trough = min(rows, key=lambda r: _f(r["综合能耗_tce"]))
        dv = _report_payload()["derived"]
        assert dv["tce_peak_ym"] == peak["年月"], (
            f"峰值月 {dv['tce_peak_ym']} != Q05 argmax {peak['年月']}"
        )
        assert abs(_f(dv["tce_peak"]) - _f(peak["综合能耗_tce"])) <= 0.005
        assert dv["tce_trough_ym"] == trough["年月"], (
            f"谷值月 {dv['tce_trough_ym']} != Q05 argmin {trough['年月']}"
        )
        assert abs(_f(dv["tce_trough"]) - _f(trough["综合能耗_tce"])) <= 0.005

    def test_mix_pcts_match_q04(self, pipeline_run):
        """正文点名的天然气/电力占比必须等于 Q04。"""
        rows = {r["能源"]: r for r in _read_csv("Q04_能源结构_折标煤与费用双口径.csv")}
        mix = {e["name"]: e for e in _report_payload()["energy_mix"]}
        for name in ("天然气", "电力"):
            assert abs(_f(mix[name]["tce_pct"]) - _f(rows[name]["折标煤占比_pct"])) <= 0.005, (
                f"{name} 折标煤占比 与 Q04 不符"
            )
            assert abs(_f(mix[name]["cost_pct"]) - _f(rows[name]["费用占比_pct"])) <= 0.005, (
                f"{name} 费用占比 与 Q04 不符"
            )

    def test_daytype_ratio_matches_q14(self, pipeline_run):
        """正文"周末/节假日保有工作日的 X%"必须能从 Q14 的日均重算出来。"""
        rows = {r["日型"].split("_")[-1]: r for r in _read_csv("Q14_工作日与周末节假日能耗对比.csv")}
        base = _f(rows["工作日"]["日均能耗_tce"])
        dv = _report_payload()["derived"]
        assert abs(dv["dt_weekend_pct"] - round(100.0 * _f(rows["周末"]["日均能耗_tce"]) / base)) <= 1
        assert abs(dv["dt_holiday_pct"] - round(100.0 * _f(rows["法定节假日"]["日均能耗_tce"]) / base)) <= 1

    def test_mom_nov_matches_q05(self, pipeline_run):
        """正文引用的"11 月全厂环比"必须等于 Q05 对应月份的环比。"""
        rows = {r["年月"]: r for r in _read_csv("Q05_月度能耗趋势与环比.csv")}
        mom = _report_payload()["derived"]["mom_nov"]
        assert mom, "mom_nov 为空 —— 正文里的 11 月环比会填不出来"
        for ym, v in mom.items():
            assert ym.endswith("-11"), f"mom_nov 混入了非 11 月: {ym}"
            assert abs(_f(v) - _f(rows[ym]["环比_pct"])) <= 0.005, (
                f"{ym} 环比 {v} != Q05 {rows[ym]['环比_pct']}"
            )

    def test_mom_nov_daily_agrees_with_decompose_mom(self, pipeline_run):
        """正文的"剔除月长后真实抬升"必须与 tools/decompose_mom.py 同源。

        正文现在并排给出原始环比与日均环比, 好让读者看出月长那 3.9pp 有多大。
        这两个数是**同一件事的两种算法**、分处两个文件(make_report.py 的
        `_mom_daily_nov` 与 tools/decompose_mom.py), 极易各改一半 ——
        所以这里用**独立的第三份实现**(不 import 任何一方)重算并比对。

        **符号也要一起断言**: 11 月比上月短(30 < 31), 月长是**负向**的,
        所以日均环比必须**大于**原始环比。若有人把它改成"31 天的月更高"那种
        直觉式实现, 符号会反过来 —— 数值比对不一定抓得住, 这一条能。
        """
        import calendar

        rows = _read_csv("Q05_月度能耗趋势与环比.csv")
        bym = {r["年月"]: _f(r["综合能耗_tce"]) for r in rows}
        raw = {r["年月"]: _f(r["环比_pct"]) for r in rows}
        dv = _report_payload()["derived"]
        daily = dv.get("mom_nov_daily")
        assert daily, "mom_nov_daily 缺失 —— 正文那句'剔除月长后更强'会填空白"

        for ym, got in daily.items():
            y, m = map(int, ym.split("-"))
            prev = f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"
            days = calendar.monthrange(y, m)[1]
            pdays = calendar.monthrange(*map(int, prev.split("-")))[1]
            base = bym[prev] / pdays
            want = (bym[ym] / days - base) / base * 100
            assert abs(_f(got) - want) <= 0.005, f"{ym} 日均环比 {got} != 复算 {want}"
            # 11 月月长为负向 -> 日均环比必须高于原始环比
            assert _f(got) > raw[ym], (
                f"{ym} 日均环比 {got} 未高于原始 {raw[ym]} —— "
                f"11 月比上月短, 月长是负向的, 剔除后应当更强"
            )

    def test_nov_len_gap_matches_the_two_mom_values(self, pipeline_run):
        """正文那句"剔除约 X pp"的 X, 必须等于原始环比与日均环比之差。

        这个 X 是正文里**唯一**还靠"算出来"的月长数字(其余月长结论都在
        decompose_mom.py 里)。它若与并排的两个环比对不上, 读者一减就能发现
        —— 所以在这里锁死, 而不是指望有人手动核对。
        """
        dv = _report_payload()["derived"]
        ks = sorted(dv["mom_nov"])
        assert ks, "mom_nov 为空"
        # 正文引用的是最早的 11 月(2024), 用它的差值即可
        gap = abs(_f(dv["mom_nov"][ks[0]]) - _f(dv["mom_nov_daily"][ks[0]]))
        assert 3.0 < gap < 5.0, (
            f"11 月月长贡献 {gap:.2f}pp 不在合理范围(约 3.9pp) —— "
            f"原始与日均有一个算错了"
        )

    # ---- 2026-09-22 补: 图表脚注 / 检测卡片里漏掉的 B 类手写数字 ----
    #
    # 上一轮插值改造按"卡片"划范围, 漏掉了**图表脚注**这一类位置, 于是 §4 气温关系
    # 的相关系数、§5 检测卡片的车间明细、§6 清洗量级都是手写死的, 且已经发到线上。
    # 这一组守住它们的来源。注意 `cmp_*` 一律**按车间名取**, 不按下标 ——
    # Q26 的行序由 SQL 的 ORDER BY 决定, 按下标会在加/减车间时静默错位。

    def test_corr_matches_q12_by_name(self, pipeline_run):
        """§4 脚注的三个相关系数必须等于 Q12 同能源的值。

        符号也要对: 天然气/蒸汽是负相关, 电力是正相关。若插值时丢了负号,
        数值容差比对不一定抓得住(0.956 vs 0.956), 所以顺带断言符号。
        """
        # Q12 是**单行宽表**: 列名形如 `电力_r` / `天然气_r` / `蒸汽_r`, 没有"能源"列。
        rows = _read_csv("Q12_气温与各能源相关系数.csv")[0]
        dv = _report_payload()["derived"]
        for key, name in (("elec", "电力"), ("gas", "天然气"), ("steam", "蒸汽")):
            got = _f(dv[f"corr_{key}"])
            want = _f(rows[f"{name}_r"])
            assert abs(got - want) <= 0.0005, f"corr_{key} {got} != Q12 {name}_r {want}"
        assert _f(dv["corr_elec"]) > 0, "电力应为正相关"
        assert _f(dv["corr_gas"]) < 0, "天然气应为负相关"
        assert _f(dv["corr_steam"]) < 0, "蒸汽应为负相关"

    def test_cmp_workshop_values_match_q26_by_name(self, pipeline_run):
        """§5 各车间的固定阈值 / 产量基线检出数, 必须按**车间名**对上 Q26。

        按下标取会在车间增减时静默错位 —— 这正是这一组存在的理由。
        """
        byname = {r["车间"]: r for r in _read_csv("Q26_三种检测方法对比.csv")}
        assert set(byname) >= {"熔炼车间", "轧制车间", "装配车间", "表面处理车间",
                               "机加工车间"}, f"Q26 缺车间: {sorted(byname)}"
        dv = _report_payload()["derived"]
        for name, short in (("熔炼车间", "熔炼"), ("轧制车间", "轧制"),
                            ("装配车间", "装配"), ("表面处理车间", "表面处理"),
                            ("机加工车间", "机加工")):
            src = byname[name]
            assert _f(dv[f"cmp_{short}_fixed"]) == _f(src["固定阈值检出"]), (
                f"{name} 固定阈值检出 {dv[f'cmp_{short}_fixed']} != Q26 {src['固定阈值检出']}"
            )
            assert _f(dv[f"cmp_{short}_base"]) == _f(src["产量基线检出"]), (
                f"{name} 产量基线检出 与 Q26 不符"
            )
            assert _f(dv[f"cmp_{short}_both"]) == _f(src["两法一致"]), (
                f"{name} 两法一致 与 Q26 不符"
            )

    def test_cmp_noise_range_matches_q26(self, pipeline_run):
        """正文"噪声压低比 0.25~0.74"必须等于 Q26 该列的最小/最大值。

        这两个数被正文**反算**成"解释掉 26%~75% 的波动", 所以 min/max 一旦
        互换, 正文会写出一句方向相反的话(压低比越小 = 解释得越多)。
        """
        vals = [_f(r["噪声压低比"]) for r in _read_csv("Q26_三种检测方法对比.csv")
                if r["噪声压低比"]]
        dv = _report_payload()["derived"]
        assert abs(_f(dv["cmp_noise_min"]) - min(vals)) <= 0.005
        assert abs(_f(dv["cmp_noise_max"]) - max(vals)) <= 0.005
        assert _f(dv["cmp_noise_min"]) < _f(dv["cmp_noise_max"])

    def test_white_cut_names_match_q26_worst_two(self, pipeline_run):
        """脚注点名的两个"白化后 S 掉得最多"的车间, 必须真是 Q26 里的前两名。

        名字一起插值(而不是只插数字), 否则改天换了名次, 正文会把 A 车间的
        数字安到 B 车间头上 —— 数字对、主语错, 这种错最难看出来。
        """
        rows = [r for r in _read_csv("Q26_三种检测方法对比.csv") if r["未白化最大S"]]
        rows.sort(key=lambda r: _f(r["白化最大S"]) / _f(r["未白化最大S"]))
        dv = _report_payload()["derived"]
        for i in (0, 1):
            src = rows[i]
            # 正文写"热处理车间", 但"车间"二字在句子模板里, 所以派生值只留前缀。
            assert dv[f"white_cut{i}_name"] == src["车间"].replace("车间", ""), (
                f"第 {i} 名应为 {src['车间']}, 实际 {dv[f'white_cut{i}_name']}"
            )
            assert abs(_f(dv[f"white_cut{i}_raw"]) - _f(src["未白化最大S"])) <= 0.05
            assert abs(_f(dv[f"white_cut{i}_white"]) - _f(src["白化最大S"])) <= 0.05

    def test_clean_volumes_match_ledgers(self, pipeline_run):
        """§6 的清洗量级必须能由原始数据与两本台账**数行数**数出来。

        这几个数是"流水账"性质的(处理了 19234 行、修了 708 行……), 最容易在
        台账增删后过期, 而正文只是平铺陈述, 没有任何交叉验算能暴露它。
        分类计数用 `==` 而不是 `in` —— "消耗量离群置空后插补"里含"插补"二字,
        用子串匹配会把它同时算进"消耗量插补", 两个数一起虚高。
        """
        dv = _report_payload()["derived"]
        n_raw = sum(1 for _ in open(os.path.join(BASE_DIR, "data", "raw_energy_data.csv"),
                                    encoding="utf-8")) - 1
        assert _f(dv["clean_raw"]) == n_raw, f"raw {dv['clean_raw']} != 行数 {n_raw}"

        rej = _read_csv("clean_rejects.csv")
        assert _f(dv["clean_dup"]) == sum(1 for r in rej if "重复" in r["reject_reason"]), (
            "业务键重复计数与 clean_rejects.csv 不符"
        )
        fixed = _read_csv("clean_fixed.csv")
        assert _f(dv["clean_fixed_n"]) == len(fixed)
        for key, reason in (("clean_impute", "消耗量插补"), ("clean_price", "单价插补"),
                            ("clean_recalc", "费用重算")):
            assert _f(dv[key]) == sum(1 for r in fixed if r["fix_reason"] == reason), (
                f"{key} 与 clean_fixed.csv 里 '{reason}' 的行数不符"
            )
        assert _f(dv["clean_days"]) == 731, "统计天数应为 731(见 Q14 计数)"

    def test_prose_has_no_stale_literals(self, pipeline_run):
        """模板里这些数字**不该**再以字面量出现 —— 防止有人改回手写。

        只查我们确实改掉的那几个特征串, 不做通用的"正文不许有数字"检查:
        论断型数字(如"12 段""9 段")本来就必须留在正文, 一刀切会误伤。
        """
        path = os.path.join(BASE_DIR, "src", "report_template.html")
        with open(path, encoding="utf-8") as f:
            tpl = f.read()
        # 剥掉 JS 注释再查: 注释里**点名**旧字面量是好事(如 `// 原来写死 "14 / 8 / 7"`),
        # 那是在解释这行为什么长这样, 不是在渲染。不剥的话注释会被当成正文误伤。
        body = re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", tpl, flags=re.S))
        # 这些是改造前的手写值, 现在都该由 data-fill 生成
        stale = ["64.09%", "49.24%", "24.18%", "39.61%", "88.8%",
                 "3,489.12", "2,159.25", "173.27 tce", "+17.10%", "+17.49%",
                 # 2026-09-22 第二轮的 B 类(图表脚注/检测卡片)
                 "+0.167", "−0.951", "−0.905", "14 / 8 / 7", "15 / 8 / 7",
                 "21 vs 6", "22 vs 6", "24 个", "329", "267", "243",
                 "44.6", "37.2", "39.0", "11.0", "11.7"]
        hit = [s for s in stale if s in body]
        assert not hit, (
            f"这些数字又在模板里手写出现了, 应改用 data-fill 插值: {hit}"
        )


# =============================================================================
# 4. 与回归基线接上: 端到端跑出来的结果必须仍然等于基线
# =============================================================================

class TestAgainstBaseline:
    """端到端跑完的产物, 必须仍然逐行等于 `tests/baseline/`。

    这一步把两层测试缝合起来: 说明"从零重跑整条管道"与"只跑分析"给出同样的
    结果 —— 即管道是**确定性**的(固定种子 + 幂等装载)。若这里分叉, 说明
    管道里混进了不确定性(时间戳、并行顺序、随机性), 那基线也就不可信了。
    """

    def test_q01_matches_baseline(self, pipeline_run):
        base = os.path.join(BASE_DIR, "tests", "baseline", "Q01_能源消费总览.csv")
        if not os.path.exists(base):
            pytest.skip("基线不存在")
        with open(base, encoding="utf-8-sig", newline="") as f:
            expected = next(csv.DictReader(f))
        actual = _read_csv("Q01_能源消费总览.csv")[0]
        for col in ("综合能耗_tce", "能源费用_元", "碳排放_tCO2", "统计天数"):
            assert abs(_f(actual[col]) - _f(expected[col])) <= TOL, (
                f"端到端重跑后 {col} 与基线不符: "
                f"基线 {expected[col]}, 实际 {actual[col]}"
            )

    def test_baseline_count_is_29(self):
        """基线必须覆盖 29 条 —— 端到端验收以"29 份基线全绿"为通过标准。"""
        bdir = os.path.join(BASE_DIR, "tests", "baseline")
        files = [f for f in os.listdir(bdir) if f.endswith(".csv")]
        assert len(files) == 29, f"基线应有 29 份, 实际 {len(files)} 份"
