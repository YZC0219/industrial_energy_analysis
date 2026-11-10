# 两台 VMware 分布式实验集群

2026-11-03 已部署 Hadoop 3.3.6、Spark 3.5.1，现已收敛为 Ubuntu、Ubuntu2 两个运行节点。两节点完整业务批次和最终在线验收均已通过；完成批次后重启 DataNode，再次核对磁盘块报告及完整性，结果正常。最终证据为 `output/two_node_cluster_final_20261103.json`。

| 节点 | 地址 | 职责 |
|---|---|---|
| Ubuntu | 192.168.21.131 | MySQL、HDFS NameNode/Checkpoint、Spark Master |
| Ubuntu2 | 192.168.21.132 | HDFS DataNode、Spark Worker（1 核、1 GB 任务资源） |

所有新配置、数据和日志位于各节点 `/home/yzc/energy-cluster-20261103`。
原 `/home/yzc/hadoop_data`、旧 Hive/CDC 目录未修改。新 NameNode 仅初始化了新的独立目录，收敛拓扑时继续使用该目录及现有 namespace，不重新格式化。

`tools/configure_vm_cluster.py` 的默认节点为 `.131`、`.132`：HDFS 默认副本数为 1，Spark 单个应用最多使用 1 核。Hadoop/Spark `workers` 仅包含 `.132`，HDFS 主机配置排除 `.133`。Ubuntu3 已完成 HDFS 退役，集群服务已禁用，虚拟机已正常关机并从 VMware 列表移除。完整验收通过后，已删除其独立目录 `D:\Virtual Machines\Ubuntu3`（27 个文件，40,428,914,923 字节）。有效数据块均位于 Ubuntu2，现有文件已收敛为单副本。

Ubuntu2 及持久配置已启用 `dfs.datanode.synconclose=true`，使 DataNode 在关闭数据块时同步数据。DataX 运行器也已增加可选的副本数配置：调度器通过 `LAKEHOUSE_REMOTE_HDFS_REPLICATION=1` 转发 `HDFS_REPLICATION=1`，显式注入 HDFS Writer 的 `hadoopConfig["dfs.replication"]`，避免插件使用自己的默认副本数。未设置时保留旧默认；相关 30 项离线测试已通过。实际新批次的文件副本数已核实为 1，缺块、损坏块和副本不足均为 0。

宿主 Windows 在三台虚拟机运行期间曾出现资源不足错误 1450、分页文件不足错误 1455，并观测到约 96% 的内存占用。主节点和 Ubuntu2 曾连接超时。完成两节点收敛后，17:35 两台虚拟机和 VMware 再次关闭，正在运行的验收中断；18:00 仅重新启动 Ubuntu、Ubuntu2，未启动 Ubuntu3。历史成功记录不能代替当前恢复后的在线验收。

## 使用

在主节点执行：

```bash
source /home/yzc/energy-cluster-20261103/env.sh
hdfs dfsadmin -report
hdfs fsck /energy/validation-20261103 -files -blocks -locations
hdfs fsck /warehouse -files -blocks -locations
spark-submit --master spark://192.168.21.131:7077 --class org.apache.spark.examples.SparkPi /usr/local/spark/examples/jars/spark-examples_2.12-3.5.1.jar 20
```

状态页：HDFS `http://192.168.21.131:19870`；Spark `http://192.168.21.131:18080`。

## 服务管理

主节点使用 `energy-namenode`、`energy-checkpoint`、`energy-spark-master`；Ubuntu2 使用 `energy-datanode`、`energy-spark-worker`。

```bash
systemctl --user status 'energy-*.service'
journalctl --user -u energy-namenode -n 50
```

服务已启用启动、故障自动重启及用户持久运行。当前两台虚拟机已运行，完整业务批次和在线状态均已确认。关闭虚拟机前应等待正在运行的批次结束，再正常关闭客户机。当前 IP 来自 VMware NAT 的 DHCP 租约，地址变化时需要调整配置。

## 两节点已完成验收

- HDFS 恰有 Ubuntu2 一个 Live DataNode，业务数据无缺块、无损坏块，副本数符合单从节点设置。
- Spark 恰有 Ubuntu2 一个存活 Worker；应用使用该 Worker，任务资源最多 1 核。
- MySQL 数据、Kafka 持久化目录、CDC 水位与新 metastore 连续，恢复时保留原 namespace 和历史状态。
- Airflow 调度器心跳正常，DAG 实际解析为 Asia/Shanghai 每日 02:00，旧写入器暂停且无竞争任务；Connector 及其任务均 RUNNING。
- API 经 SSH 隧道实际读取 Linux MySQL，`/health` 同时返回 `status=ok`、`data_source=mysql`。

上述在线检查、调度器实际心跳及 DAG 导入检查均已通过；下一次自动运行时间为北京时间 2026-11-04 02:00。完整批次返回 19,006 条有效记录，DWS 日度、DWS 月度、ADS 日度费用均为 215,364,002.57 元。完成批次后正常重启 DataNode，其新块报告和全量 fsck 均通过。证据为 `output/two_node_validation_run_20261103.json`、`output/datanode_persistence_restart_20261103.json` 和 `output/two_node_cluster_final_20261103.json`。

## 两节点关机后的产量恢复与批次进度

此前第一次中断留下的 710 个坏文件均位于一个废弃 Spark staging 目录，已按精确目录清理并通过全量 fsck，随后完成第三节点退役。原始检查保存在 `output/fsck_before_two_node_repair_20261103.txt`。

17:35 再次关机后，18:06 的全量 fsck 发现 `energy_dwd.dwd_production_detail` 已提交目录内的 185 个日分区各有一个坏文件，共 185 个坏块、0 个缺块；抽样原因是 `SIZE_MISMATCH`。ODS、能耗 DWD、DWS、ADS 及 CDC 水位目录均无坏块。本次证据单独保存在 `output/fsck_post_shutdown_20261103.txt`。

18:11 已用原产量 SQL 从健康 ODS 仅重建这 185 个日期，恢复 1,480 行；产量表共 5,848 行，业务键及数量与 ODS 精确一致。原 CDC 快照保留，`cdc_snapshot_intent` 与 `cdc_snapshot_watermark` 的 topic、offset 19,006 和内容哈希保持一致。恢复证据为 `output/production_partition_recovery_20261103.json`。后续完整批次和在线健康现已通过。

完整验收运行 `two_node_acceptance_20261103092717` 的三个任务均已成功。关机后的自动接管重试曾在 18:03 因 NameNode 安全模式阻止覆盖 `cdc_snapshot_intent` 失败，当时仅报告 5,980/6,165 个块。产量恢复后，仅清除了该运行对应的失败 apply 任务状态，并恢复 DAG，继续使用同一份不可变快照重试，现已完成。未清空 offset、重置基线或删除有效 CDC 水位。

## 原三节点历史验收

以下记录均发生在 Ubuntu3 移除之前：

- HDFS 两个 DataNode 在线，能耗 CSV 两副本，缺块/损坏块为零。
- Spark 两个 Worker 在线；SparkPi 执行器分别位于两台从节点。
- Spark SQL 从 HDFS 读取清洗后文件：19006 行、8 个车间。
- 持久化服务启动后再次完成跨节点 SparkPi。
- 证据：`output/vm_cluster_verification_20261103.json`。

当前是同一物理电脑上的两虚拟机实验环境，单主节点、单 DataNode；HDFS 单副本不提供节点冗余。Airflow/CDC 的独立切换记录见下节，现有指标 API 仍读取 Linux MySQL。集群服务仅用于当前本机虚拟网络，不配置公网入口。

## Airflow/CDC 切换（2026-11-03）

新的运行目录是 `/home/yzc/energy-cluster-20261103/pipeline`，容器组名为 `energy-cluster-pipeline`。该目录中的私有配置和密钥只保存在权限受限的 `secrets/` 下，不提交版本库。

链路为 Linux MySQL → Debezium → Kafka → Airflow → Spark → 新 HDFS。原验收使用两个 Spark Worker，当前目标改为 Ubuntu2 一个 Worker。Kafka 数据明确持久化到 `pipeline/kafka-data`。原 Windows Kafka 没有可运行的迁移实例，故以新 topic 的完整初始快照重建基线，不修改旧数仓的水位记录。旧 HDFS 数据目录和旧 metastore 保留，新表使用独立 metastore 和新 HDFS 地址。

当前运维命令（不依赖 Windows Docker）：

```powershell
python -m tools.cluster_pipeline status
python -m tools.cluster_pipeline trigger
```

手动触发会检查旧写入器已暂停、旧任务和当前批次均空闲，避免并发写入。历史 Windows 部署的 `main_cdc_operator` 命令继续保留用于旧环境，不用于这套 Linux 集群。

切换验收文件 `output/cluster_pipeline_cutover_20261103.json` 保留原三节点完整批次及相同边界重放成功的报告，并已追加当前两节点完整验收、真实调度器心跳及磁盘持久化结果。当前主用证据为 `output/two_node_cluster_final_20261103.json`。实际 cron 为 Asia/Shanghai 每日 02:00，DAG 未暂停，旧写入器保持暂停且无竞争任务。启用时可能补跑最近一个到期区间，`catchup=False` 不逐日回放中间所有日期；历史失败记录保留，新的验收批次已成功。

已清理 6 个校验值相同的 Windows 迁移包（1,980,743,082 字节）、旧 Docker 重装备份盘（11,503,927,296 字节）、Ubuntu3 独立虚拟机目录（40,428,914,923 字节），以及两台从节点克隆来的重复数据库迁移目录（合计约 9.4 GB）。Windows 清理文件合计 53,913,585,301 字节；Linux 内删除文件的空间另计，未执行剩余虚拟磁盘压缩。主节点的必要恢复包、源项目和唯一历史材料保留。清理记录为 `output/backup_cleanup_completed_20261103.json`。
