# 项目入口地图

以下为 2026-10-13 核对的定位线索；不代表服务此刻在线。路径相对实际项目根目录。

| 用户用语/症状 | 优先读的代码入口 | 查口径或运行边界时再读 |
|---|---|---|
| 清洗、缺失能源品种、插补、批次窗口 | `src/clean_data.py`、`src/generate_data.py` | `docs/数据清洗质量说明.md` |
| 重复装载、历史修正、日期维表、增量水位线 | `src/import_mysql.py`、`sql/create_table.sql` | `docs/问题发现与工程复盘.md` |
| 能耗、费用、碳排、单耗、基线、CUSUM 的数字不对 | `sql/analysis.sql`、`governance/metrics.yaml`、`governance/catalog.py` | `docs/指标字典.md` |
| 月环比、七日均值、帕累托、残差、节能情景 | `governance/analysis_formulas.yaml`、`governance/analysis_formulas.py`、`src/analysis_formulas.js`、`src/analysis_scenarios.js` | `docs/残差_CUSUM与节能情景统一.md` |
| 报告图表、筛选、手机显示、导出 HTML | `src/report_template.html`、`src/make_report.py`、`governance/analysis_report.py`、`src/semantic_metrics.js`、`src/mobile.css` | `docs/分析公式接入原报告入口.md` |
| 报告归档、版本指针、回退、重放 | `governance/report_delivery.py`、`tools/report_delivery.py` | `docs/报告版本归档与回退.md` |
| 接口返回、指标 API、筛选参数、快照目录 | `src/api.py`、`governance/catalog.py`、`governance/consumers.py` | `docs/语义层消费迁移_报告与API.md` |
| 诊断工作台、异常问答、反馈，通常端口 8010 | `src/diagnostics.py`、`src/diagnostics.html`、`src/diagnostics.js`、`ml/attribution_assistant.py` | `docs/智能诊断工作台.md` |
| 预测、滚动验证、训练泄漏、归因评测 | `ml/feature_pipeline.py`、`ml/rolling_validation.py`、`ml/model_benchmark.py`、`ml/evaluate_attribution.py` | `docs/阶段二_预测与智能归因.md` |
| Agent 工作台、角色、任务队列、取消，通常端口 3210 | `agent-workbench/server.mjs`、`agent-workbench/public/` | `agent-workbench/README.md` |
| Gemini/Antigravity 安装、登录、启动工作台 | `agent-workbench/gemini-entry.mjs`、`agent-workbench/gemini-login.mjs`、`agent-workbench/launch.mjs`、`agent-workbench/tools/antigravity-install.cmd` | `agent-workbench/README.md` |
| Airflow、主业务 CDC、定时失败、旧链路竞争 | `dags/energy_cdc_warehouse_dag.py`、`dags/energy_pipeline_dag.py`、`tools/main_cdc_health.py`、`tools/main_cdc_operator.py` | `docs/阶段三_CDC业务数仓接入.md` |
| Hive/Spark/DataX、软删除、迟到修正 | `spark/apply_mysql_cdc_batch.py`、`datax/run_sync.py`、`hive/`、`sql/migrations/001_soft_delete_view.sql` | `docs/阶段一_分层数仓与分布式计算.md` |
| Kafka、持久化告警、重试、隔离、重复投递 | `streaming/alert_outbox.py`、`streaming/outbox_supervisor.py`、`streaming/dispatch.py`、`streaming/alert_quarantine.py` | `docs/告警与整体验收复验_20261004.md` |
| 多设备实时功率模拟、日电量对账 | `streaming/device_simulator.py`、`streaming/reconcile_device_day.py`、`tools/verify_device_day_runtime.py` | `docs/工业数据生命周期_四方向扩展.md` |
| 真实数据、能效、节能措施、工程估算 | `tools/phase5_energy_assessment.py`、`tools/phase5_real_data_diagnosis.py`、`tools/analyze_asset_energy.py`、`optimization/` | `docs/阶段五_能效基线与节能情景.md` |

## 常见误定位

- 页面数字异常：追踪字段及计算来源。不要先编辑生成的 `output/report.html`，也不要把 SQL 窗口结果当原始事实重新聚合。
- “装载后报告没变”：先确认实际库/端口、维表日期覆盖、分析产物生成时间和报告输入路径；不立即重训模型。
- “工作台失败”：先区分 8010 的能耗诊断与 3210 的多模型任务服务。若仍不明确，先检查相关入口及配置，再询问所指页面。
- “改了没生效”：检查服务启动目录和部署副本。`tmp/cdc_main_daily_*/project/` 可能固定旧源码；修改根目录不能证明副本已更新。
- 文档中的最新批次、测试数量和部署名是历史记录；运行状态必须读取当前证据。
