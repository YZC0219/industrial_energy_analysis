# -*- coding: utf-8 -*-
"""端到端验收 (#17) —— 真的起 Airflow、触发一次 DAG、校验五个 task 与产物。

    python tools/verify_dag_run.py
    python tools/verify_dag_run.py --timeout 900 --keep-going

**与 tests/test_end_to_end.py 的分工**（两者都要, 不是二选一）:

| | test_end_to_end.py | 本脚本 |
|---|---|---|
| 跑什么 | 顺序调四个脚本 | **经 Airflow 调度**跑同一批脚本 |
| 验什么 | 产物齐全 / 新鲜 / 三路自洽 | 同上 **+ 五个 task 全 success** |
| 速度 | ~15 秒 | 数十秒 ~ 数分钟(取决于调度器轮询) |
| 进 CI | 是(`-m "db and slow"`) | 否, 手动跑 |

前者证明**代码链路**正确, 后者证明**编排链路**正确 —— DAG 里 task 的依赖顺序、
`cd` 到了对的目录、容器内路径映射、retries 配置等等, 都是前者覆盖不到的。
README 说的"起 Airflow → 触发 DAG 全链路"指的是这一个。

**为什么用 `docker exec airflow` 而不是 REST API**:
默认的 `airflow.api.auth.backend.session` 只认浏览器会话, REST API 会一律
返回 401。要开 API 就得往 `docker-compose.yml` 里加 `AIRFLOW__API__AUTH_BACKENDS`,
那是**为了验收而改被测系统的配置** —— 验收脚本不该有这个副作用。走 CLI
不需要任何配置改动, 且验的正是生产路径(调度器自己派发任务)。

**它不重跑装载以外的任何东西, 也不会污染工作区**: DAG 是全量重建的
(`DROP TABLE IF EXISTS` + `--init`), 所以重复跑结果一致; 产物会被覆盖成
新鲜的一份, 这正是本脚本要验的。

退出码: 0 = 全部通过; 1 = 有断言失败(会打印失败明细)。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time

# Windows 控制台默认 GBK, 打不出 ✓ 等字符 —— 与其它 tools/ 脚本一致
sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(BASE_DIR, "output")

DAG_ID = "energy_pipeline"
# 与 dags/energy_pipeline_dag.py 里的 task_id 及依赖顺序一致
TASKS = ["generate_raw_data", "clean_data", "load_warehouse",
         "run_analysis", "build_report"]
EXPECTED_QUERIES = [f"Q{i:02d}" for i in range(1, 30)]
TOL = 0.05

# 找调度器容器: 名字随 compose 项目名变化(带目录前缀), 按后缀匹配更稳
SCHEDULER_SUFFIX = "airflow-scheduler-1"


def find_scheduler() -> str:
    """返回调度器容器名。找不到就明确报错, 并给出启动命令。"""
    try:
        out = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}"],
            capture_output=True, text=True, timeout=20,
        ).stdout
    except FileNotFoundError:
        fail("找不到 docker 命令。本脚本需要 Docker Desktop 与 `docker compose up -d`。")
    for line in out.splitlines():
        name, _, status = line.partition("\t")
        if name.endswith(SCHEDULER_SUFFIX) and "Up" in status:
            return name
    fail(
        "没有正在运行的 Airflow 调度器容器。\n"
        "       先执行: docker compose up -d\n"
        "       然后等 webserver 健康: curl -s http://localhost:8080/health"
    )


def airflow(container: str, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    """在调度器容器里跑一条 airflow CLI 命令。"""
    return subprocess.run(
        ["docker", "exec", container, "airflow", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )


FAILURES: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    """记录一条断言结果并打印。失败的会进 FAILURES, 最后汇总。"""
    if ok:
        print(f"  [✓] {label}")
    else:
        print(f"  [✗] {label}")
        if detail:
            for ln in detail.splitlines():
                print(f"       {ln}")
        FAILURES.append(label)
    return ok


def fail(msg: str):
    print(f"[错误] {msg}")
    raise SystemExit(1)


def section(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


# ---------------------------------------------------------------------------
# 1. 触发一次 DAG run, 等它跑完
# ---------------------------------------------------------------------------

def _json_blob(text: str):
    """从 CLI 输出里取出 JSON(前后可能有提示行)。取 [ 或 { 到对应的收尾符。"""
    text = (text or "").strip()
    starts = [k for k in (text.find("["), text.find("{")) if k != -1]
    if not starts:
        return None
    i = min(starts)
    j = max(text.rfind("]"), text.rfind("}"))
    if j <= i:
        return None
    try:
        return json.loads(text[i:j + 1])
    except json.JSONDecodeError:
        return None


def read_run_state(container: str, run_id: str) -> str:
    """读某个 run 的状态。用 list-runs 而非 `dags state` —— 后者只吃
    execution_date 位置参数, 在 run_id 带自定义前缀时对不上。"""
    p = airflow(container, "dags", "list-runs", "-d", DAG_ID, "-o", "json", timeout=60)
    runs = _json_blob(p.stdout)
    if not isinstance(runs, list):
        return ""
    for r in runs:
        if r.get("run_id") == run_id:
            return r.get("state", "")
    return ""


def trigger_and_wait(container: str, timeout: int) -> str:
    """触发一次 DAG run 并轮询到终态, 返回 run_id。"""
    run_id = f"verify__{time.strftime('%Y%m%dT%H%M%S')}"
    print(f"  触发 {DAG_ID}, run_id={run_id} ...")
    p = airflow(container, "dags", "trigger", DAG_ID, "-r", run_id)
    if p.returncode != 0:
        fail(f"触发失败:\n{p.stdout}\n{p.stderr}")

    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        state = read_run_state(container, run_id)
        if state != last:
            print(f"    state = {state or '(等待调度)'}")
            last = state
        if state in ("success", "failed"):
            return run_id
        time.sleep(5)
    fail(f"等待 {timeout}s 后 DAG run 仍未结束(最后状态 {last!r})")


# ---------------------------------------------------------------------------
# 2. 五个 task 必须全部 success
# ---------------------------------------------------------------------------

def read_task_states(container: str, run_id: str) -> dict:
    """读一次 run 的每个 task 状态, 返回 {task_id: state}。

    `airflow tasks states-for-dag-run -o json` 返回的是**数组**:
    [{dag_id, task_id, state, ...}, ...]
    """
    p = airflow(container, "tasks", "states-for-dag-run", DAG_ID, run_id,
                "-o", "json", timeout=60)
    rows = _json_blob(p.stdout)
    if not isinstance(rows, list):
        return {}
    return {r["task_id"]: r.get("state", "") for r in rows if "task_id" in r}


def check_tasks(container: str, run_id: str) -> None:
    section("1/3  五个 task 的最终状态")
    states = read_task_states(container, run_id)
    if not states:
        fail(f"读不到 {run_id} 的 task 状态")

    for t in TASKS:
        st = states.get(t, "missing")
        check(st == "success", f"{t:<20} {st}",
              "" if st == "success" else f"{t} 的状态是 {st}, 期望 success")
    # 反向: 不该有定义外的 task
    extra = set(states) - set(TASKS)
    check(not extra, "无定义外的 task", f"多出: {sorted(extra)}")


# ---------------------------------------------------------------------------
# 3. 产物齐全 + 新鲜
# ---------------------------------------------------------------------------

def check_artifacts() -> None:
    section("2/3  产物齐全与新鲜度")

    report = os.path.join(OUT_DIR, "report.html")
    clean_rpt = os.path.join(OUT_DIR, "clean_report.txt")
    check(os.path.exists(clean_rpt), "clean_report.txt 已生成")
    check(os.path.exists(report), "report.html 已生成")

    have = {f.split("_")[0] for f in os.listdir(OUT_DIR)
            if f.endswith(".csv") and re.match(r"^Q\d+_", f)}
    missing = [q for q in EXPECTED_QUERIES if q not in have]
    check(not missing, "29 条查询的 CSV 全部生成", f"缺失: {missing}")

    # 新鲜度: 报告必须比它读的每一个 Q*.csv 都新(否则显示的是上一轮的数)
    if os.path.exists(report):
        rpt = os.path.getmtime(report)
        newer = []
        for q in EXPECTED_QUERIES:
            m = [f for f in os.listdir(OUT_DIR)
                 if f.startswith(q + "_") and f.endswith(".csv")]
            if m and os.path.getmtime(os.path.join(OUT_DIR, m[0])) > rpt:
                newer.append(m[0])
        check(not newer, "report.html 比所有 Q*.csv 新",
              f"以下 CSV 比报告还新(报告是旧数据): {newer}")

        ref = os.path.join(OUT_DIR, "clean_energy.csv")
        if os.path.exists(ref):
            older = []
            for q in EXPECTED_QUERIES:
                m = [f for f in os.listdir(OUT_DIR)
                     if f.startswith(q + "_") and f.endswith(".csv")]
                if m and os.path.getmtime(os.path.join(OUT_DIR, m[0])) < os.path.getmtime(ref):
                    older.append(m[0])
            check(not older, "Q*.csv 比 clean_energy.csv 新",
                  f"以下 CSV 比清洗产物还旧(不同源): {older}")


# ---------------------------------------------------------------------------
# 4. 三路数字自洽
# ---------------------------------------------------------------------------

def _read_csv(name: str) -> list[dict]:
    path = os.path.join(OUT_DIR, name)
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [{(k or "").strip(): (v or "").strip() for k, v in row.items()}
                for row in csv.DictReader(f)]


def _f(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _report_payload() -> dict:
    """读回 report.html 内联的 JSON。

    产物里只剩起始标记 `/*__DATA__*/`(make_report.py 把整个区间连标记一起
    替换掉了), 所以用括号配平扫描出 JSON 的结尾。
    """
    with open(os.path.join(OUT_DIR, "report.html"), encoding="utf-8") as f:
        html = f.read()
    marker = "/*__DATA__*/"
    i = html.find(marker)
    if i == -1:
        fail("report.html 里找不到 /*__DATA__*/ 标记")
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
    fail("report.html 里的内联 JSON 括号不配平, 文件可能被截断")


def check_consistency() -> None:
    section("3/3  三路数字自洽 (Q01 == Q28 求和 == 报告首屏)")

    q01 = _read_csv("Q01_能源消费总览.csv")[0]
    total = _f(q01["综合能耗_tce"])
    cube = sum(_f(r["综合能耗_tce"]) for r in _read_csv("Q28_各车间日度能耗与产量.csv"))
    payload = _report_payload()
    hero = _f(payload["totals"]["tce"])
    front = sum(r["tce"] for r in payload["ws_daily"])

    print(f"  Q01 直接查库      {total:>14,.2f} tce")
    print(f"  Q28 立方体求和    {cube:>14,.2f} tce")
    print(f"  报告首屏(JSON)    {hero:>14,.2f} tce")
    print(f"  前端 KPI 底座     {front:>14,.2f} tce")

    check(abs(total - cube) <= TOL, "Q01 == Q28 求和",
          f"{total} vs {cube} (差 {abs(total - cube):.4f})")
    check(abs(total - hero) <= TOL, "Q01 == 报告首屏",
          f"{total} vs {hero} (差 {abs(total - hero):.4f})")
    check(abs(total - front) <= TOL, "Q01 == 前端 KPI 底座",
          f"{total} vs {front} (差 {abs(total - front):.4f})")

    # 剔除口径也要自洽 —— 它单独来自 Q02
    q02 = _read_csv("Q02_剔除公用工程后的能耗总览.csv")[0]
    check(abs(_f(payload["totals"]["tce_ex"]) - _f(q02["综合能耗_tce"])) <= TOL,
          "报告 tce_ex == Q02",
          f"{payload['totals']['tce_ex']} vs {q02['综合能耗_tce']}")

    # 与回归基线对表: 编排跑出来的结果必须仍等于基线
    base = os.path.join(BASE_DIR, "tests", "baseline", "Q01_能源消费总览.csv")
    if os.path.exists(base):
        with open(base, encoding="utf-8-sig", newline="") as f:
            exp = next(csv.DictReader(f))
        check(abs(_f(exp["综合能耗_tce"]) - total) <= TOL,
              "端到端结果 == 回归基线 Q01",
              f"基线 {exp['综合能耗_tce']} vs 实际 {total}")
    else:
        print("  [ ] 跳过基线对表 (tests/baseline 不存在)")


def main() -> None:
    ap = argparse.ArgumentParser(description="触发 Airflow DAG 并校验端到端产物")
    ap.add_argument("--timeout", type=int, default=600,
                    help="等待 DAG run 结束的秒数 (默认 600)")
    ap.add_argument("--keep-going", action="store_true",
                    help="即使 task 有失败也继续校验产物 (默认失败即停)")
    args = ap.parse_args()

    section("端到端验收: Airflow DAG 全链路 (#17)")
    container = find_scheduler()
    print(f"  调度器容器: {container}")

    run_id = trigger_and_wait(container, args.timeout)
    check_tasks(container, run_id)

    if FAILURES and not args.keep_going:
        print(f"\n[中止] task 有失败 ({len(FAILURES)} 项), 未继续校验产物。\n"
              f"       加 --keep-going 可强制继续。")
        raise SystemExit(1)

    check_artifacts()
    check_consistency()

    section("结论")
    if FAILURES:
        print(f"  失败 {len(FAILURES)} 项:")
        for f in FAILURES:
            print(f"    - {f}")
        raise SystemExit(1)
    print(f"  全部通过 —— DAG run {run_id} 的五个 task 均为 success,")
    print(f"  29 份产物齐全且同源, 三路数字自洽。")
    print(f"  可在 Airflow UI 查看: http://localhost:8080/dags/{DAG_ID}/grid")


if __name__ == "__main__":
    main()
