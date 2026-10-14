# 按改动选择检查

路径相对实际项目根目录。下面列出候选测试，不要求每行全部执行；读实际涉及的功能和 fixture 后选择。

| 改动区域 | 相关测试 | 需要时才做的运行验收 |
|---|---|---|
| 清洗、日期范围、输入守卫 | `tests/test_clean_data.py`、`tests/test_input_guard_regressions.py`、`tests/test_data_quality.py` | `tools/check_data_quality.py`、`tools/check_energy_coverage.py` |
| MySQL 装载/幂等/水位线 | `tests/test_import_mysql_helpers.py`、`tests/test_import_mysql.py` | `tools/verify_mysql_import_replay.py`；先确认目标库和副作用 |
| SQL 指标/字典/回归 | `tests/test_metric_dictionary.py`、`tests/test_analysis_snapshot.py`、`tests/test_analysis_formulas.py` | SQL 快照需要 `-m db`；不是默认离线验收 |
| 语义目录、API、消费 | `tests/test_semantic_metric_validation.py`、`tests/test_semantic_consumers.py`、`tests/test_api.py` | `tools/verify_semantic_runtime.py` 读取当前 MySQL 并写独立导出及汇总证据 |
| 前端指标/公式/筛选 | `tests/test_analysis_formulas.py`、`tests/test_browser_semantics.py`、`tests/test_report_evidence.py` | `tools/verify_browser_semantics.py`、`tools/verify_analysis_report.py`；先检查浏览器条件及输入 |
| 原报告入口 | `tests/test_report_evidence.py`、`tests/test_analysis_formulas.py` | `tools/verify_report_entry.py`；涉及当前 Airflow 容器中的报告操作器，不是整条 DAG 验收 |
| 报告版本、回退、重放 | `tests/test_report_delivery.py`、`tests/test_report_evidence.py` | `tools/verify_report_delivery.py` 在独立版本 store 中切换/回退/重放并运行浏览器 |
| 异常诊断与反馈 | `tests/test_diagnostics.py`、`tests/test_api.py`、`tests/test_ml_pipeline.py` | `tools/verify_diagnostics_browser.py`；在线模型单独核验 |
| 预测/归因/时间切分 | `tests/test_ml_pipeline.py` | `python -m ml.evaluate_attribution --output D:/industrial_energy_analysis/tmp/attribution-review.json` 为离线守卫评测；在线模式另需配置与授权 |
| CDC、删除和调度隔离 | `tests/test_cdc_business_bridge.py`、`tests/test_cdc_scheduler_isolation.py`、`tests/test_soft_delete_contract.py`、`tests/test_mysql_cdc_probe.py` | 先核对当前部署状态；隔离源实验、主业务批次与 DAG 触发的权限/副作用不同 |
| Outbox/重试/隔离/投递 | `tests/test_alert_outbox.py`、`tests/test_outbox_operations.py`、`tests/test_extension_sinks.py` | `tools/verify_outbox_runtime.py`、`tools/verify_quarantine_runtime.py`、`tools/verify_sink_runtime.py`；先核对接收端/主题/端口 |
| Agent 工作台/队列/适配器 | `agent-workbench/test/workbench.test.mjs` | 在 `agent-workbench/` 运行 `npm test`，使用注入适配器，不代表真实模型调用成功 |

## 常用组合示例

公式与报告改动：

```powershell
python -m pytest tests/test_analysis_formulas.py tests/test_browser_semantics.py tests/test_report_evidence.py -q --basetemp D:/industrial_energy_analysis/tmp/check-formula-本次唯一标识 -o cache_dir=D:/industrial_energy_analysis/.pytest_cache
```

数据库快照检查（连接及目标测试库已确认）：

```powershell
$env:MYSQL_PORT = '3307'
python -m pytest tests/test_analysis_snapshot.py -m db -q --basetemp D:/industrial_energy_analysis/tmp/check-sql-本次唯一标识 -o cache_dir=D:/industrial_energy_analysis/.pytest_cache
```

需要全库离线回归时再运行 `python -m pytest -q`，沿用 D 盘临时目录设置并指定独立 `--basetemp`。
文档中的用例数会过期，以本次执行结果为准。
