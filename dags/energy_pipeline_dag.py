"""
### 工业能耗数据管道

从模拟原始数据到可视化报告, 五个阶段串成一条**可调度、可重试、可回补**的管道。

```
generate_raw_data → clean_data → load_warehouse → run_analysis → build_report
```

| 阶段 | 做什么 | 产出 |
|---|---|---|
| generate_raw_data | 按固定种子生成带脏数据的原始 CSV | `data/raw_energy_data.csv` |
| clean_data | 10 类质量问题逐一清洗并留痕 | `output/clean_*.csv` + `clean_report.txt` |
| load_warehouse | 建库建表 + 装载进 MySQL 星型模型 | 3 张维表 + 2 张事实表 |
| run_analysis | 执行 23 条业务查询 | `output/Q01..Q23_*.csv` |
| build_report | 结果内联进模板, 生成自包含报告 | `output/report.html` |

#### 关于幂等

`load_warehouse` 走 `import_mysql.py --init`, 该脚本执行的 `create_table.sql`
用 `DROP TABLE IF EXISTS` 重建所有表, 所以**整条管道重跑任意次结果都一致**,
不会出现数据翻倍。配合 `max_active_runs=1`, 也排除了两次运行互相踩踏的可能。

下一步的改造方向是把这里的全量重建换成增量装载 + 水位线, 那时幂等要靠
`INSERT ... ON DUPLICATE KEY UPDATE` 来保证, 而不是靠 DDL 重建。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator

# 项目在容器里的挂载点, 由 docker-compose.yml 的 PROJECT_DIR 提供
PROJECT_DIR = os.getenv("PROJECT_DIR", "/opt/airflow/project")


def on_task_failure(context) -> None:
    """失败告警钩子。

    演示环境只往日志里写一行结构化记录。要接 Slack / 钉钉 / 邮件, 在这里换成
    对应的 webhook 调用即可 —— 之所以留成钩子而不是写死, 是因为告警渠道属于
    部署环境的事, 不该烧进 DAG 本身。
    """
    ti = context["task_instance"]
    print(
        f"[ALERT] dag={ti.dag_id} task={ti.task_id} "
        f"run={context.get('ds')} attempt={ti.try_number} log={ti.log_url}"
    )


DEFAULT_ARGS = {
    "owner": "data-engineering",
    "depends_on_past": False,
    # 失败自动重试: 数据库还没就绪、网络抖动这类瞬时故障不该让整条管道挂掉
    "retries": 2,
    "retry_delay": timedelta(minutes=1),
    "retry_exponential_backoff": True,   # 1min → 2min → 4min, 避免瞬时故障被重试打垮
    "max_retry_delay": timedelta(minutes=10),
    "execution_timeout": timedelta(minutes=30),
    "on_failure_callback": on_task_failure,
}


def project_task(task_id: str, command: str, doc: str, **kwargs) -> BashOperator:
    """在项目目录下执行一条命令的 BashOperator。

    统一带上 `cd`, 因为脚本内部用的是相对于项目根目录的路径。
    """
    return BashOperator(
        task_id=task_id,
        bash_command=f"cd {PROJECT_DIR} && python {command}",
        doc_md=doc,
        **kwargs,
    )


with DAG(
    dag_id="energy_pipeline",
    description="工业能耗数据管道: 模拟数据 → 清洗 → 数仓 → 分析 → 报告",
    doc_md=__doc__,
    default_args=DEFAULT_ARGS,
    start_date=datetime(2024, 1, 1),
    schedule="0 2 * * *",          # 每天凌晨 2 点
    catchup=False,                 # 不回补历史: 管道是全量重建, 回补没有意义
    max_active_runs=1,             # 全量重建期间不允许并发, 否则两次运行会互相覆盖
    tags=["energy", "etl", "mysql"],
) as dag:

    generate_raw_data = project_task(
        "generate_raw_data",
        "src/generate_data.py",
        """
        **生成模拟原始数据**

        随机种子固定为 `20240918`, 同样的种子永远产出同样的数据, 所以这一步是
        可复现的。生成的 CSV 里刻意注入了 10 类质量问题(日期格式混用、别名错别字、
        重复记录、离群值、负消耗、缺失值……), 供下一步清洗。

        > 真实项目里这一步不存在 —— 数据来自上游业务系统。放在 DAG 里是为了让
        > 整条管道自包含, 能一键从零跑通。
        """,
    )

    clean_data = project_task(
        "clean_data",
        "src/clean_data.py",
        """
        **清洗与结构化**

        逐类处理脏数据, 并且**留痕**: 被剔除的记录进 `clean_rejects.csv`,
        被修正的记录进 `clean_fixed.csv`, 最后输出 `clean_report.txt` 记录每一步
        的处理条数与数据保留率。

        留痕比清洗本身更重要 —— 数据管道出问题时, 你需要能回答"这条记录去哪了"。
        """,
    )

    load_warehouse = project_task(
        "load_warehouse",
        "src/import_mysql.py --init",
        """
        **建库建表 + 装载**

        执行 `sql/create_table.sql`: 建 3 张维表(车间/能源品种/日历)、2 张事实表,
        以及 3 个封装口径计算的视图。

        装载走 `LOAD DATA LOCAL INFILE` 批量导入, 比逐行 INSERT 快 1~2 个数量级;
        若服务端未开 `local_infile`, 自动退回批量 `executemany`。
        事实表在此基础上做 upsert: 先灌临时表, 再按 `updated_at` 判新旧写入,
        使上游修正的历史记录能覆盖旧值, 而过期数据重放不会把新值改回去。

        因为 DDL 里是 `DROP TABLE IF EXISTS`, 这一步**重复执行结果一致**。
        """,
        retries=3,   # 数据库冷启动可能还没就绪, 多给一次机会
    )

    run_analysis = project_task(
        "run_analysis",
        "src/import_mysql.py --run-analysis",
        """
        **执行分析查询**

        跑 `sql/analysis.sql` 里的 23 条业务查询, 结果逐条导出到 `output/Q*.csv`。

        单条查询失败不会中断整批(记录失败继续跑), 这样一条写错的 SQL 不至于让
        另外 22 条的结果都拿不到。
        """,
    )

    build_report = project_task(
        "build_report",
        "src/make_report.py",
        """
        **生成可视化报告**

        把上一步的查询结果作为 JSON 内联进 `src/report_template.html`, 产出
        `output/report.html` —— 单文件、零依赖、双击即开。
        """,
    )

    generate_raw_data >> clean_data >> load_warehouse >> run_analysis >> build_report
