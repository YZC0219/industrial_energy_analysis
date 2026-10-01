# CDC 业务数仓入口与验收

## 本次交付

2026-09-30 已使用真实 MySQL、Debezium 和 Kafka 验证业务结构的物理删除，
并复用项目原有 `10_dwd_energy.sql`、`20_dws.sql`、`30_ads.sql` 核验指标传播。
SQL 文件没有修改；实跑只将库名映射到新建的隔离数据库，DWD 的输入替换为
经过版本归并的 CDC 当前快照。原有 MySQL 装载、DataX 和 `energy_pipeline` 不受影响。

| 阶段 | DWD 有效行数 | DWS 日费用 | DWS 月费用 | ADS 全厂日费用 |
| --- | ---: | ---: | ---: | ---: |
| 真实插入两条业务事实 | 2 | 30.00 | 30.00 | 30.00 |
| 真实修正一条事实 | 2 | 37.00 | 37.00 | 37.00 |
| 真实物理删除该事实 | 1 | 20.00 | 20.00 | 20.00 |
| 向隔离 Kafka 主题末尾追加原来的旧插入事件并完整重放 | 1 | 20.00 | 20.00 | 20.00 |

证据：[源端与快照清单](../output/mysql_cdc_business_source_20260930203846.json)、
[DWD/DWS/ADS 验收报告](../output/mysql_cdc_business_warehouse_20260930203846.json)。
快照保留真实的完整 CDC envelope、删除前镜像、binlog 位点和 Kafka offset。
维表和产量是隔离测试夹具，源事实也是专门插入的业务样本；这些结果证明真实
删除机制和项目 SQL 的传播能力，不代表采集了某家企业的真实生产事实。

故障注入在 DWD 已写入、DWS/ADS 尚未更新时主动中断：提交水位线没有前进，
新快照的执行意图阻止旧快照回退；随后重跑新快照成功修复全部汇总。
旧的插入快照在正常完成后也被拒绝。

## 入口与版本规则

- `tools/export_mysql_cdc_batch.py` 冻结单分区主题的 end offset，只接收从 offset 0
  开始、无缺口的完整历史，生成独立的 `schema_version=2` 快照文件。
- **按 binlog 文件序号、position、row 判定业务版本**；Kafka offset 仅用于冻结
  读取边界与恢复进度。连接器重复发出的旧消息即使有更大的 Kafka offset，也不会
  覆盖更新的删除。删除保留旧 `updated_at`，不伪造修改时间。
- 业务键改变时同时保留旧键的删除版本和新键的当前版本。源主键、完整行镜像、
  数值、日期、业务键、source identity 和 binlog lineage 都会检查。
- `spark/apply_mysql_cdc_batch.py` 重建并验证原始事件所对应的快照，检查维表匹配，
  然后执行原有 DWD/DWS/ADS SQL，并核对业务键、删除状态和三层费用守恒。
- `cdc_snapshot_intent` 在写入 DWD 前记录目标边界；`cdc_snapshot_watermark` 在
  所有层与质量检查通过后记录提交边界。主题切换、边界回退、同边界内容冲突均阻断。
- 新入口采用**完整当前快照模式**，保留所有已知业务键的删除版本。首次接入时，
  快照若遗漏已有 DWD 键会停止，防止不完整引导静默覆盖仓库。

该 v2 文件不发布到现有 v1 `energy-events`，不直接用于既有 Flink 窗口或
`updated_at` 对账器。完整历史保存在内存并逐次重放，适合当前项目规模；大规模
常驻消费、事务性多表提交、跨分区排序、topic 重建恢复、DDL 演进与 HA 尚未覆盖。

## 手工运行

连接器必须从创建起使用 `snapshot.mode=initial`，且只捕获目标事实表；
`decimal.handling.mode=string`、`time.precision.mode=connect` 已加入注册配置。
主题必须单分区、保留完整历史。首次快照尚未结束、历史过期或被压缩出缺口时，
入口会停止。维表与产量需先同步为 ODS 的 `current` 快照。

```powershell
python -m tools.export_mysql_cdc_batch --connector energy-mysql-cdc --topic energy-cdc.industrial_energy.fact_energy_consumption --output output/mysql_cdc_snapshot_YYYYMMDDHHMMSS.json
```

在具备同版本项目文件、Spark 与 Hive catalog 的 Linux 环境执行：

```bash
PYSPARK_PYTHON=/path/to/python3.11 PYSPARK_DRIVER_PYTHON=/path/to/python3.11 \
  spark-submit --master 'local[2]' spark/apply_mysql_cdc_batch.py \
  --database energy --biz-date YYYY-MM-DD \
  --snapshot output/mysql_cdc_snapshot_YYYYMMDDHHMMSS.json \
  --output output/mysql_cdc_business_YYYYMMDDHHMMSS.json
```

`--database energy` 使用主业务库。上述命令是部署入口，**本次没有向主业务库执行**。
验证工具 `tools.verify_cdc_business_runtime` 只创建新的源端测试库、主题和独立 VM
工作目录，不覆盖现有 VM checkout；新文件和下载包在本地 D 盘，VM 数据位于
已有 D 盘虚拟磁盘。

## 调度入口

新增 `energy_cdc_warehouse` DAG，默认暂停、无自动计划、单次运行并发为 1：

```text
sync_current_dimensions → capture_complete_cdc → apply_cdc_dwd_dws_ads
```

部署环境使用可选 `docker-compose.cdc-warehouse.yml` 传入：

| 变量 | 用途 |
| --- | --- |
| `CDC_WAREHOUSE_ENABLED=1` | 启用新 DAG 的执行入口 |
| `CDC_EXCLUSIVE_SOURCE_CONFIRMED=1` | 部署方确认已停止旧 DataX 能耗写入及模拟源再装载 |
| `CDC_CONNECTOR` / `CDC_SOURCE_TOPIC` | 完整主业务事实的初始快照连接器及主题 |
| `CDC_PYSPARK_PYTHON` | Linux 上适配 Spark 的 Python 路径 |
| `CDC_BOOTSTRAP` / `CDC_CONNECT_URL` | Airflow worker 可访问的 Kafka 与 Connect |

默认前两个开关均为 0。SSH 模式还需现有 `docker-compose.lakehouse.yml` 的
连接配置、私钥及 pinned known_hosts。`run_remote_lakehouse --input` 仅允许
项目 `output/mysql_cdc_snapshot_*.json`；在写入前核对 VM 的 Git SHA，上传后
校验 SHA256，拒绝覆盖内容不同的已有文件。

2026-10-01 已完成该 DAG 的真实调度端到端验证：独立 Airflow 元数据库与
LocalExecutor 调度器运行同一 DAG 文件，维表经真实 MySQL → DataX → HDFS 同步，
再捕获 CDC 并通过 SSH 执行原 DWD/DWS/ADS SQL。新建源库在连接器注册前已有两条
记录，初始快照实际产生两条 `op=r`；首次三个任务全部成功，金额均为 30.00。
随后真实物理删除 id=1，第二次三个任务全部成功，有效行数由 2 降为 1，
日汇总、月汇总和 ADS 金额均降为 20.00，DWD 保留删除标记。

[调度证据](../output/mysql_cdc_business_scheduler_20261001204940.json)固定运行代码的
Git SHA；[归档审计](../output/mysql_cdc_business_scheduler_audit_20261001204940.json)
验证两批快照完整重放、数仓输入 SHA256，以及删除后的全部源字段对账。
独立调度初始阶段检查了实际快照事件及预设指标。
测试仅使用独立测试库、HDFS 路径、VM checkout 和调度实例；验证结束停止测试调度器
并暂停测试连接器。**主库写入切换仍未执行**。
不得让新 DAG 和原 DataX 能耗写入同时运行。
数仓多表写入不是跨表事务，运行失败时可能出现暂时不一致；执行意图与成功后才
推进的提交水位线用于阻止回退并允许重跑，消费方仍应遵循成功发布门禁。

[Airflow 状态报告](../output/mysql_cdc_business_dag_import_20260930.json)记录实际
metadata database 中的暂停状态。另使用现有 Airflow 镜像的 Paramiko，对
独立 VM Git checkout 实测 SHA 固定、快照上传、相同文件重复上传及内容冲突拒绝；
[SSH 输入报告](../output/mysql_cdc_business_ssh_input_20260930.json)保留测试的 Git SHA
与源快照 SHA256。这是输入传输验证，没有执行生产 DAG 或改动原 VM checkout。

复现实验使用 `python -m tools.verify_cdc_scheduler_runtime`；每次新建带时间戳的
隔离资源，并拒绝复用已有目标。`CDC_DAG_ID` 与 `CDC_TARGET_DATABASE` 仅允许
默认主流程名称或规定格式的独立测试名称，源库与目标 HDFS 路径必须对应。

## 主业务源全量初始快照对账（2026-10-01）

当前 Docker MySQL 的 `industrial_energy.fact_energy_consumption` 已有 20,704 条
记录。新增独立只读连接器，在不修改业务记录、不启动主数仓写入的情况下，捕获
全部 20,704 条 `op=r` 初始快照；按源主键逐条比较全部 14 个字段，读取前后源数据
相同且快照全部匹配。[对账报告](../output/mysql_cdc_business_main_reconciliation_20261001225051.json)
与[压缩原始快照](../output/mysql_cdc_business_main_raw_20261001225051.json.gz)保留行数、
内容摘要和完整事件。读取不加锁，结论仅适用于本次前后数据相同的窗口；这批数据
来自项目现有业务表，本验证不证明它是外部工业现场数据。

**源端对账通过，数仓切换门禁未通过。** 20,704 条记录对应 19,006 个业务键，
有 1,698 个键包含多个源 ID。当前 CDC 数仓按日期、车间、能源类型识别唯一业务行，
对此初始快照返回 `Conflicting images at the same binlog version`，拒绝产生歧义
数仓输入。本次没有删除、合并或覆盖这些记录；应先明确这些行代表历史版本还是
多笔可累加事实，再决定主键与版本策略。此前隔离删除验证仍然有效，但不能据此
宣称主业务源已完成切换。连接器在验证结束后已暂停。

只读对账复现入口为 `python -m tools.verify_main_cdc_snapshot`。
