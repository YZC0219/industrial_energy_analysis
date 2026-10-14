---
name: energy-pipeline-debug
description: "排查能迹项目清洗、MySQL 装载、Airflow/CDC、Hive/Spark/DataX 和持久化告警的运行故障。用于批次失败、数据没更新、重复装载、软删除不传播、重复投递或恢复失败；Agent 工作台角色队列错误不属于能源数据管道。"
---

# 能迹数据链路排障

先找第一处失败或不一致，再完成用户授权范围内的修复。路径相对实际项目根目录；本机默认为 `D:\industrial_energy_analysis`。

## 缩小故障范围

| 症状 | 优先核对 |
|---|---|
| 缺数据、异常突然增加 | `src/clean_data.py`、能源品种覆盖、插补台账、日期维表覆盖 |
| 装载成功但结果不变/重复 | `src/import_mysql.py`、实际库及端口、业务键、水位线、分析导出及报告输入 |
| 主 CDC 定时批次失败 | 当前部署目录、`tools/main_cdc_health.py`、`tools/main_cdc_operator.py`、`dags/energy_cdc_warehouse_dag.py` |
| 源变化没传播到数仓 | 批次快照的 offset/键/删除事件、`tools/export_mysql_cdc_batch.py`、`spark/apply_mysql_cdc_batch.py`、投影及对账结果 |
| 告警重复、重试停滞、坏消息 | `streaming/alert_outbox.py`、`streaming/outbox_supervisor.py`、`streaming/outbox_inspect.py`、`streaming/dispatch.py`、`streaming/alert_quarantine.py` |

主库、隔离探针和模拟数据不得混用。先确认日志对应的服务、源码副本和输入批次，查看现有日志与状态，不把重跑整个管道当默认诊断。

## 本项目的运行约束

- 默认回归库指向 Docker 映射的 3307；宿主 3306 可能是旧实例。以当前连接配置核实，不写死错误端口，也不在输出中打印口令或密钥。
- 有效主业务 CDC 写入期间保持旧 `energy_pipeline` 暂停，确认没有 queued/running 的竞争批次。不能为了演示而删除有效主业务记录。
- 先定位实际部署目录；历史文档中的 `cdc_main_daily_20261003103045` 仅是线索。多个候选时结合启动配置和运行记录确认，不能仅挑文件名最新的目录。
- 确认目录后，可执行只读状态检查：

```powershell
$deployment = 'D:/industrial_energy_analysis/tmp/替换为已确认的部署目录'
python -m tools.main_cdc_health --deployment $deployment
python -m tools.main_cdc_operator status --deployment $deployment
```

- health 检查可能生成本地证据，但不触发或修复批次。`main_cdc_operator trigger` 会写入调度状态；`verify_dag_run.py` 等验收也可能触发任务。按本次授权执行，不能因为名称含 verify 就视为只读。
- 水位线、删除历史和快照必须相互对应。发现差异时先保留证据，定位源端/导出/投影哪一步失真；不通过清空状态、跳过 offset 或刷新基线来掩盖问题。
- 项目源码修复不等于运行中的冻结副本更新；核对实际副本再验证部署效果。

## 修复与验证

根据证据完成必要的本地修复，使用匹配模块的既有测试。需要服务验证时先读相应脚本，确定目标库/主题、写入范围与环境条件；故障注入优先使用已有隔离验收路径。
生成物与临时备份放在 D 盘。不因普通排障重配 Docker 全局存储。
交付时说明故障证据、修复、验证及尚未验证的运行条件；历史成功 JSON 不能证明当日运行正常，批次完成也不能证明准点性或长期稳定。
