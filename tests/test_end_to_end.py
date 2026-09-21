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
