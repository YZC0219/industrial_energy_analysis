# 首个可靠性实验：重复消费

2026-10-20 实测结论：生产 CDC 投影函数通过重复投递实验，事实快照不重复增加；实时 MySQL/Kafka 和 Spark/Hive 报表层尚未验收。当前 Docker Linux 引擎不可达，未启动或修改主业务服务。

## 链路与范围

选定链路：Debezium 单分区事件 → `tools/export_mysql_cdc_batch.py::project` → v2 完整快照 → `spark/apply_mysql_cdc_batch.py::apply` → 项目 DWD、DWS 日/月汇总和 ADS 工厂日报 SQL。

本次离线输入为明确标记的合成事件，调用真实生产投影函数，不模拟 Spark 执行，也不把合成输入说成实时 Kafka 证据。报表验收止于 ADS 数据表，不涵盖 HTML 报告或 API 发布。新增脚本不改业务实现，不触发 DAG，不修改现有源表、主题或有效主业务记录。

## 预先定义的正确答案

业务日期均为 2024-03-15，车间 W01。维表因子沿用现有隔离验收的 `seed_dimensions`。

| 顺序 | 操作 | 键 | 用量 | 费用 |
| --- | --- | --- | --- | --- |
| 1 | 新增 | E01 / id=1 | 1.000 | 10.00 |
| 2 | 新增 | E02 / id=2 | 2.000 | 20.00 |
| 3 | 更新 | E01 / id=1 | 1.700 | 17.00 |

投影前先保存独立的 `expected.json`：最终两条有效事实，费用 37.00，原始用量合计 3.700，业务键恰为 W01/E01、W01/E02。该原始用量合计只针对本实验同单位的输入，不作为跨能源业务指标。

重复投递：把上述三条事件再追加一遍，Kafka offset 从 0–2 延伸到 0–5，保留每条原事件的 binlog file/pos/row。重复的旧插入出现在最新更新之后，也不能把 E01 恢复为 10 元。

验收要求：费用、用量、有效条数、键集、完整行内容、binlog 行版本保持一致；Kafka 捕获边界从 3 前进到 6。负对照把全部 after-image 直接相加得到错误费用 94.00，必须与独立正确答案产生差异。不同图像占用相同 binlog 版本必须拒绝，不能作为普通重复静默丢弃。

## 本次对账

| 检查项 | 首次 | 重复后 | 结果 |
| --- | --- | --- | --- |
| 事实快照条数 / 有效条数 | 2 / 2 | 2 / 2 | 通过 |
| 费用 | 37.00 | 37.00 | 通过 |
| 原始用量 | 3.700 | 3.700 | 通过 |
| 完整行 / binlog 版本 | 基准 | 与基准相同 | 通过 |
| end_offset（右开边界） | 3 | 6 | 符合预期 |
| DWD / DWS / ADS 实际表 | 未运行 | 未运行 | 待运行 |

执行记录见 [report.json](evidence/report.json)，输入见 [input.json](evidence/input.json)，正确答案见 [expected.json](evidence/expected.json)。两份快照为 [original.json](evidence/original.json) 和 [duplicate.json](evidence/duplicate.json)。记录包含 UTC 开始/结束时间、故障注入时刻、各阶段耗时、逐项预期/实际值、源码与输入输出 SHA-256。此处耗时仅为小样本本地投影耗时，不是恢复时间或吞吐性能结论。

相关回归：`test_cdc_duplicate_experiment.py`、`test_cdc_business_bridge.py`、`test_soft_delete_contract.py` 共 15 passed、0 failed、0 skipped。包括错误投影被正确答案拦截、失败时保留证据、拒绝覆盖已有目录、拒绝生产库名、相同源版本冲突拒绝。

## 独立复现

在项目根目录执行，每次选择一个不存在的输出目录；脚本拒绝覆盖证据。Python 临时文件和缓存亦应配置到 D 盘。

```powershell
$env:PYTHONPYCACHEPREFIX = 'D:/industrial_energy_analysis/tmp/cdc-duplicate-check/pycache'
python -m tools.experiment_cdc_duplicate --output-dir D:/industrial_energy_analysis/output/cdc_duplicate_new_run
```

当前保存的结论状态为 `projection_passed_warehouse_not_run`，不代表全链路成功。

具备项目现有 Spark/Hive/HDFS 环境后，在该运行环境的项目根目录执行：

```text
python -m tools.experiment_cdc_duplicate --output-dir <新的证据目录> --warehouse --database energy_cdc_business_probe_YYYYmmddHHMMSS
```

数据库名末尾需换成实际 14 位时间戳。Windows 证据目录使用 D 盘；现有 Linux 数仓运行环境使用其隔离输出目录，运行后归档至 D 盘。此命令需要 Spark 环境变量和 Python 依赖已配置，不自动安装服务或依赖。

`--warehouse` 仅允许新建的隔离探针库；库或对应 HDFS 路径已存在时拒绝。执行顺序为首次快照、同一快照再次应用、新 offset 上的重复事件快照。每阶段调用真实项目 SQL，保存完整业务行，并检查：

- DWD 两行分别为 E01=1.700/17.00、E02=2.000/20.00，均未删除。
- DWS 日/月表和 ADS 日表各一行，费用 37.00、标煤 0.0057 tce、碳排 0.009 t；这是现有维表因子和 SQL 四舍五入下的独立正确答案。
- 各层完整业务列与首次结果完全一致；只排除每次重算自然变化的 `etl_time`。
- 提交水位线与 intent 一致，首次/相同快照为边界 3，重复事件快照为边界 6。

隔离表和证据在失败后保留，不自动删库。报错返回非零退出码，已有输入和错误写入 `report.json`；成功才会记录 warehouse `passed`。该模式仍使用合成事件，不证明真实 Kafka 断连重连或 offset 提交行为。

## 为什么有效，哪里会失效

投影按源 binlog 坐标判断同一 id 的版本；较旧事件不能覆盖新状态，相同版本相同图像会被跳过，相同版本冲突图像会被拒绝。业务键用于维护最终行状态。因此本次事件数量翻倍，业务状态不变。数仓代码采用完整快照及覆盖重算，并在所有层校验后发布水位线；这是代码机制描述，本次没有获得该段新运行证据。

可靠性条件包括：单 topic 单分区、从 offset 0 开始的连续完整历史、完整 before/after 图像、稳定 binlog lineage、精确小数字符串、完整维表匹配、独占数仓写入。多分区、历史被 retention 截断、schema 不兼容、binlog lineage 更换或竞争写入不在本次成功结论内；其中部分条件现有代码主动拒绝，不能据此声称具备自动恢复能力。

本次没有发现需要修改业务实现的失败。下一步实际运行隔离 Spark 模式，再做实时探针重复投递；服务中断追平、中途写入失败、历史跨日修正和长期准点性仍需独立实验。
