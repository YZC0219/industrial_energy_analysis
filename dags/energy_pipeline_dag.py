"""
### 工业能耗数据管道

从模拟原始数据到可视化报告, 五个阶段串成一条**可调度、可重试、可回补**的管道。

```
generate_raw_data → clean_data → load_warehouse → run_analysis → build_report
                          └→ build_features → run_validation → verify_ml_artifacts → run_deep_validation
```

| 阶段 | 做什么 | 产出 |
|---|---|---|
| generate_raw_data | 按固定种子生成带脏数据的原始 CSV | `data/raw_energy_data.csv` |
| clean_data | 10 类质量问题逐一清洗并留痕 | `output/clean_*.csv` + `clean_report.txt` |
| load_warehouse | 增量装载进 MySQL 星型模型(维护水位线) | 3 张维表 + 2 张事实表 + `etl_watermark` |
| run_analysis | 执行 24 条业务查询 | `output/Q01..Q24_*.csv` |
| build_report | 结果内联进模板, 生成自包含报告 | `output/report.html` |
| build_features / run_validation | 重建预测特征并执行滚动基线 | `output/ml_*.csv/json` |
| run_deep_validation | 可选运行 LSTM/Transformer 并核验滚动测试键 | `output/ml_deep_*.csv/json` |

#### 关于幂等

`load_warehouse` 走 `import_mysql.py --incremental`, 幂等由**三层**共同保证:

1. **装载是 upsert** —— `INSERT ... SELECT ... ON DUPLICATE KEY UPDATE`, 冲突时按
   `updated_at` 判新旧。重放同一批数据不产生重复行。
2. **水位线只前进** —— 每次装载后把已入库数据的最大 `updated_at` 记进
   `etl_watermark`, 下一批只取水位线之后的行。水位线用 `GREATEST` 推进,
   重放旧批次不会让它倒退。
3. **装载与水位线推进在同一事务** —— 这是最关键的一条。若分两个事务提交,
   "装载写了一半就崩溃"会让水位线**领先于数据**, 下次从崩溃点之后取数,
   那半批记录**永久丢失且不报错**, 管道每次还报 success。同事务之后, 所有不一致
   都退化成"数据领先于水位线"这种可自愈形态: 下批重放同一区间, upsert 消化掉。

配合 `max_active_runs=1` —— 这里拦住的不再是"两次全量重建互相覆盖",
而是**水位线的读-改-写序列不许交错**: 两个 run 同时读到同一个旧水位线、
各自装载、各自推进, 后写的那个会把先写的进度覆盖掉。

首次部署需要手工执行一次 `src/import_mysql.py --init --full`; 之后每天走增量。

#### 一个诚实的说明: 模拟环境下水位线不会自己前进

`generate_raw_data` 每次都是**全量重新生成**, 且 `updated_at` 是业务键的确定函数
(见 `src/generate_data.py`), 所以重跑产出的 CSV 与上一批逐字节相同 ——
没有一行是"新的", 水位线自然不会推进, 每次增量装载都是 0 行。

这不代表增量逻辑没生效, 而是模拟数据源**不具备"上游有新数据"这个前提**。
真实场景把这一步换成上游导出即可(上游的 ERP/MES 导出天然带新的 `updated_at`)。
在模拟环境里验证增量走的是测试注入, 见 `tools/` 下的验收脚本与
`tests/test_import_mysql.py` 的水位线测试。
"""

from __future__ import annotations

import os
import json
import urllib.request
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
    webhook = os.getenv("ENERGY_ALERT_WEBHOOK")
    if webhook:
        payload = json.dumps({
            "text": f"energy_pipeline 失败: {ti.task_id}",
            "dag_id": ti.dag_id, "task_id": ti.task_id,
            "logical_date": str(context.get("logical_date")), "log_url": ti.log_url,
        }).encode("utf-8")
        try:
            urllib.request.urlopen(urllib.request.Request(
                webhook, data=payload, headers={"Content-Type": "application/json"}
            ), timeout=10)
        except Exception as exc:  # 告警发送失败不能覆盖原始任务异常
            print(f"[ALERT_DELIVERY_FAILED] {exc}")


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


def lakehouse_task(task_id: str, command: str, doc: str, **kwargs) -> BashOperator:
    """按部署开关运行 Hadoop 侧任务；本地开发默认只跑原 MySQL 基线。"""
    return BashOperator(
        task_id=task_id,
        bash_command=(
            f"cd {PROJECT_DIR} && "
            "if [ \"${LAKEHOUSE_ENABLED:-0}\" != \"1\" ]; then "
            "echo '[SKIP] LAKEHOUSE_ENABLED!=1'; exit 0; fi && " + command
        ),
        doc_md=doc,
        **kwargs,
    )


def optional_deep_task(task_id: str, command: str, doc: str, **kwargs) -> BashOperator:
    """Torch 环境按部署开关运行；关闭时不制造或复用伪新鲜的深度模型产物。"""
    return BashOperator(
        task_id=task_id,
        bash_command=(
            f"cd {PROJECT_DIR} && "
            "if [ \"${DEEP_LEARNING_ENABLED:-0}\" != \"1\" ]; then "
            "echo '[SKIP] DEEP_LEARNING_ENABLED!=1'; exit 0; fi && " + command
        ),
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
    catchup=False,                 # 不回补历史: 回补交给手工重放, 不靠 Airflow 补齐调度区间
    max_active_runs=1,             # 水位线的读-改-写序列不许交错, 并发会让进度互相覆盖
    tags=["energy", "etl", "mysql", "hive", "spark", "datax"],
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
        "src/clean_data.py --batch-all",
        """
        **清洗与结构化**

        逐类处理脏数据, 并且**留痕**: 被剔除的记录进 `clean_rejects.csv`,
        被修正的记录进 `clean_fixed.csv`, 最后输出 `clean_report.txt` 记录每一步
        的处理条数与数据保留率。

        留痕比清洗本身更重要 —— 数据管道出问题时, 你需要能回答"这条记录去哪了"。

        产出额外写一份 `clean_batch_*.csv` 给下一步装载用。`--batch-all` 表示
        **不设窗口**, 写出全量范围的批次文件 —— 这一步是**无状态**的, 它不知道
        数据库里的水位线停在哪, 也不该知道(否则换数据源就得改清洗脚本)。按水位线
        过滤是装载端的事, 它本来就要做。

        > 注意清洗口径与窗口无关: 离群判决用全体分位数、插补用分组中位数,
        > 这些统计量一律从**完整**数据算, 窗口只影响落盘范围。有一条测试守着
        > "批次行必须是全量行的逐字节子集"。
        """,
    )

    load_warehouse = project_task(
        "load_warehouse",
        "src/import_mysql.py --incremental",
        """
        **增量装载**

        读 `etl_watermark` 里记的水位线, 只装载 `updated_at` 在水位线之后的行,
        装载完把水位线推进到已入库数据的最大 `updated_at` —— 装载与推进在**同一个
        事务**内完成。首次运行(水位线为空)等价于全量装载。

        装载走 `LOAD DATA LOCAL INFILE` 批量导入, 比逐行 INSERT 快 1~2 个数量级;
        若服务端未开 `local_infile`, 自动退回批量 `executemany`。
        事实表写入是 upsert: 先灌临时表, 再按 `updated_at` 判新旧, 使上游修正的
        历史记录能覆盖旧值, 而过期数据重放不会把新值改回去。

        水位线列选 `updated_at` 而不是 `record_date`, 是因为上游**修正**一条历史记录时
        `record_date` 不变而 `updated_at` 变晚 —— 用 `record_date` 做水位线会让修正
        记录永远落在水位线左侧, 永远进不了增量批次。

        这一步**不做建表**。建表走 `sql/create_table.sql`, 只在首次部署时手工执行一次
        (`import_mysql.py --init --full`), 之后不再进每晚路径:
        `create_table.sql` 会把事实表 DROP 重建, 而 `etl_watermark` **故意不在
        DROP 清单里** —— 若每晚重建事实表却留着水位线, 水位线会领先于数据,
        之后的数据被永久跳过。所以 `--init` 单独使用会被运行期直接拒绝。
        """,
        retries=3,   # 数据库冷启动可能还没就绪, 多给一次机会
    )

    run_analysis = project_task(
        "run_analysis",
        "src/import_mysql.py --incremental --run-analysis",
        """
        **执行分析查询**

        跑 `sql/analysis.sql` 里的 29 条业务查询, 结果逐条导出到 `output/Q*.csv`。

        单条查询失败不会中断整批(记录失败继续跑), 这样一条写错的 SQL 不至于让
        另外 28 条的结果都拿不到。

        > 为什么和装载是**两个 task**却各自都带装载 flag: `--run-analysis` 本身
        > 不装载任何数据, 但 import_mysql.py 是"装载+分析"一条链, 分析那一段在
        > `main()` 末尾。上面 load_warehouse 已经装过并推进了水位线, 这里若不带
        > `--incremental`, 默认(增量)会去找 `clean_batch_energy.csv` 再装一遍;
        > 带 `--incremental` 是把它写**明**。两种都指向增量, 但显式写法不会因为
        > 上游 clean_data 换成全量模式就静默改语义 —— CI 上正是这么错的。
        > 拆两个 task 是为了重跑分析时不必重跑装载(装载可能很贵)。
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

    build_features = project_task(
        "build_features",
        "-m ml.feature_pipeline --energy output/clean_batch_energy.csv "
        "--production output/clean_batch_production.csv --output output/ml_features.csv",
        "从本次全量清洗批次重建车间日特征；滞后和滚动统计仅使用目标日前数据。",
    )
    run_validation = project_task(
        "run_validation",
        "-m ml.model_benchmark --features output/ml_features.csv "
        "--predictions output/ml_model_predictions.csv --metrics output/ml_model_metrics.json",
        "使用同一连续自然日滚动折运行季节基线与 LightGBM，并输出可复算预测明细和指标。",
    )
    verify_ml_artifacts = project_task(
        "verify_ml_artifacts",
        "tools/verify_ml_artifacts.py --energy output/clean_batch_energy.csv "
        "--production output/clean_batch_production.csv "
        "--features output/ml_features.csv --predictions output/ml_model_predictions.csv "
        "--metrics output/ml_model_metrics.json",
        "验证特征键与当前清洗批次一致，且 MAE/RMSE 可由预测明细复算。",
    )
    run_deep_validation = optional_deep_task(
        "run_deep_validation",
        "python -m ml.deep_benchmark --features output/ml_features.csv "
        "--predictions output/ml_deep_predictions.csv --metrics output/ml_deep_metrics.json && "
        "python tools/verify_deep_artifacts.py "
        "--baseline-predictions output/ml_model_predictions.csv "
        "--deep-predictions output/ml_deep_predictions.csv --metrics output/ml_deep_metrics.json",
        "配置 Torch 并设置 DEEP_LEARNING_ENABLED=1 后，运行 LSTM/Transformer，随后核验其测试键、时间顺序和指标。",
    )

    sync_ods_dimensions = lakehouse_task(
        "sync_ods_dimensions",
        "for t in dim_workshop dim_energy_type dim_calendar fact_production; do "
        "python datax/run_sync.py --table $t --biz-date {{ ds }} || exit $?; done",
        "DataX 全量覆盖同步小维表与当前产量快照；稳定 current 分区可幂等重跑。",
    )
    sync_ods_energy = lakehouse_task(
        "sync_ods_energy_incremental",
        "python datax/run_sync.py --table fact_energy_consumption --biz-date {{ ds }} "
        "--window-start '{{ data_interval_start | ts }}' --window-end '{{ data_interval_end | ts }}'",
        "DataX 按 updated_at 半开区间增量同步能耗事实；窗口由 Airflow 数据区间决定。",
    )
    build_dwd = lakehouse_task(
        "build_dwd",
        "python spark/run_sql.py spark/sql/10_dwd_energy.sql --biz-date {{ ds }} --load-mode incremental",
        "Spark SQL 去重、校验并关联维度，生成车间×日×能源明细。",
    )
    quality_dwd = lakehouse_task(
        "quality_dwd",
        "python tools/check_hive_quality.py --stage dwd --biz-date {{ ds }} --load-mode incremental",
        "检查批次键覆盖、业务主键唯一、必填字段和非负值；合法空批次可通过。",
    )
    build_dws = lakehouse_task(
        "build_dws",
        "python spark/run_sql.py spark/sql/20_dws.sql --biz-date {{ ds }} --load-mode incremental",
        "生成车间日/月主题汇总；动态覆盖目标分区使补数幂等。",
    )
    quality_dws = lakehouse_task(
        "quality_dws",
        "python tools/check_hive_quality.py --stage dws --biz-date {{ ds }} --load-mode incremental",
        "以 DWD 折标煤汇总与 DWS tce 的守恒关系作为发布门禁。",
    )
    build_ads = lakehouse_task(
        "build_ads",
        "python spark/run_sql.py spark/sql/30_ads.sql --biz-date {{ ds }} --load-mode incremental",
        "生成全厂日看板应用表。",
    )

    generate_raw_data >> clean_data >> load_warehouse >> run_analysis >> build_report
    clean_data >> build_features >> run_validation >> verify_ml_artifacts >> run_deep_validation
    clean_data >> [sync_ods_dimensions, sync_ods_energy]
    [sync_ods_dimensions, sync_ods_energy] >> build_dwd >> quality_dwd
    quality_dwd >> build_dws >> quality_dws >> build_ads
