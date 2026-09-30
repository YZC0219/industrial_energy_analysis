# 阶段三：MySQL 物理删除 CDC 源试验

这是一个**默认关闭、源端独立**的 Kafka Connect / Debezium 配置。它只订阅
`industrial_energy.fact_energy_consumption`，向
`energy-cdc.industrial_energy.fact_energy_consumption` 发布 Debezium 原始 envelope。
现有 `energy-events`、Flink SQL、Airflow 对账与 MySQL→Hive 软删除链路不接入该主题。
因此它不会改变当前报表结果，也不代表物理删除已在下游完成处理。

## 启用前

Windows 本机可使用 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/start_docker_desktop.ps1`
启动 Docker。该入口先检查引擎；只在日志确认 Secrets Engine 的遗留 socket 错误时，
通过现有 Ubuntu WSL 将仅含零字节 socket 的临时目录改名保留，再启动 Desktop。
它拒绝处理含其它文件的目录，启动日志写入 `D:\Docker\desktop-startup.log`。
这处理的是 Docker Desktop 的已知 Windows 启动缺陷，不涉及镜像/卷的重置。
当前机器已设置用户登录启动项 `IndustrialEnergyDockerRecovery`，并创建
`D:\Docker\Start Docker Desktop.lnk`。若要停用自动启动，可以删除这个同名用户启动项；
脚本依赖现有 `Ubuntu` WSL 和本机 Docker 安装路径。

1. 确认 Docker 的镜像和卷实际存储在 `D:\`。本试验会拉取 Debezium 镜像，
   也会写入已有的 MySQL/Kafka 卷；若 Docker Desktop 的数据盘仍在 `C:\`，
   先不要运行下面的 `up`。本仓库不修改 Docker Desktop 的全局存储设置。
2. 启动原有 MySQL，并执行只读前提检查：

   ```powershell
   python -m tools.check_mysql_cdc_readiness --output output/mysql_cdc_readiness.json
   ```

   需要 `log_bin=ON`、`binlog_format=ROW`、`binlog_row_image=FULL`、非零
   `server_id`、事实表主键 `id` 和可用 binlog 位点。若检查失败，先修复前提；
   不要用初始化快照代替持续日志采集。
3. 在 MySQL 交互式客户端中创建专用账号。下面的口令是占位符，需自行替换，
   且与下一步文件里的 `db-password` 完全一致。不要把实际口令写进命令行或提交仓库。

   ```sql
   CREATE USER 'energy_cdc'@'%' IDENTIFIED BY 'replace-with-a-unique-local-password';
   GRANT SELECT ON industrial_energy.* TO 'energy_cdc'@'%';
   GRANT RELOAD, SHOW DATABASES, REPLICATION SLAVE, REPLICATION CLIENT ON *.* TO 'energy_cdc'@'%';
   ```

   `%` 用于本地 Compose 容器之间的动态地址；如有固定网络地址范围，可以进一步收紧。
4. 复制 [`cdc/connect-secrets.example.properties`](../cdc/connect-secrets.example.properties)
   到 `cdc/secrets/connect-secrets.properties` 并填入同一口令。真实文件被
   `cdc/secrets/.gitignore` 忽略，只读挂载到容器。Kafka Connect 的 REST 请求和
   配置主题只保存 `${file:/opt/connect-secrets.properties:db-password}` 引用，
   不保存口令原文。不要在共享机器上给该文件宽泛的读取权限。

## 启动与注册

下面的 Compose 叠加文件仅在显式指定时生效；`cdc` profile 额外启动
Kafka 内部主题初始化和 Kafka Connect，不启动 Flink：

```powershell
docker compose -f docker-compose.yml -f docker-compose.cdc.yml --profile streaming --profile cdc up -d mysql kafka cdc-topics-init debezium-connect
python -m tools.register_mysql_cdc --print-config
python -m tools.register_mysql_cdc --apply
python -m tools.register_mysql_cdc --status
```

`--apply` 通过 Kafka Connect REST 的 `PUT /connectors/energy-mysql-cdc/config`
创建或更新连接器；其它两个命令只读。连接器使用 `snapshot.mode=initial`：
首次运行应先发布表的现存行，然后从 binlog 接续。独立源主题按主键 `id`
发布事件。物理删除预期生成 `op=d`、`before` 为删除前行、`after=null` 的事件，
随后是同 key 的 Kafka null tombstone。`source` 元数据包含 binlog 顺序所需信息；
删除不会自动推进旧行的 `updated_at`，所以不能直接用当前流批对账器的
`updated_at` 规则解释 Debezium 事件。

## 完成判定与限制

2026-09-30 已启动隔离连接器，并完成真实物理删除及独立 Hive/Spark 投影验证，
报告见本文末尾。**主业务数仓 DAG 尚未接入此连接器**。完整的业务源验收仍需：

- 注册后 connector/task 均为 `RUNNING`；完整快照行数与源表同一时点一致。
- 在隔离样本上执行物理 `DELETE`，核实源主题的 `op=d`、主键、`before`
  和随后 tombstone；随后重启连接器，核实无漏发、无错误复活。
- 保留源端 binlog 位点、Kafka offset、DDL 变更与恢复记录；对 schema 演进
  另做覆盖。现有 `energy-events` 契约与 Debezium envelope 不同，不能直接
  接到当前 Flink 或 Airflow 门禁，更不能把单 broker 试验称作 HA。

如果要做端到端硬删除，还需增加经过验证的 Debezium→项目事件契约转换、
基于 binlog 顺序的版本判定、Hive/Spark 删除传播和同范围流批对账。不要在
生产事实表上做演示性物理删除。

## 隔离的真实硬删除探针

### MySQL 插件运行方式

完整 Debezium 镜像首次下载较大。可以复用本项目的 `apache/kafka:3.9.1`
及 Java 21，只安装固定版本的官方 MySQL 插件。文件均保存在项目所在的 D 盘，
`cdc/plugins` 中的二进制依赖不进入 Git。下面的 SHA1 来自 Maven Central 的
同名 `.sha1` 文件；不匹配时必须停止，不能解压启动。

```powershell
New-Item -ItemType Directory -Force -Path cdc/plugins, tmp | Out-Null
curl.exe -fL --retry 5 -o tmp/debezium-mysql-3.6.0.Final-plugin.tar.gz https://repo.maven.apache.org/maven2/io/debezium/debezium-connector-mysql/3.6.0.Final/debezium-connector-mysql-3.6.0.Final-plugin.tar.gz
if ((Get-FileHash tmp/debezium-mysql-3.6.0.Final-plugin.tar.gz -Algorithm SHA1).Hash.ToLower() -ne '431125792651e8a4217bc769f53b72a4b291c95e') { throw 'Plugin checksum mismatch' }
tar -xzf tmp/debezium-mysql-3.6.0.Final-plugin.tar.gz -C cdc/plugins
docker compose -f docker-compose.yml -f docker-compose.cdc.yml -f docker-compose.cdc-plugin.yml --profile streaming --profile cdc up -d debezium-connect
```

先执行下述 `--prepare`，再启动连接器。使用插件方式后，后续启动、重启命令也
需要保留第三个 `-f docker-compose.cdc-plugin.yml`，否则会切回完整镜像。
两种方式复用相同的 Kafka Connect 配置、offset 和 schema history 主题，
一次只运行一种方式。schema history 保持单分区、无限保留，禁止日志压缩；
Connect 自身的配置、offset、状态主题使用日志压缩。

参考：[Debezium 官方插件安装](https://debezium.io/documentation/reference/stable/install.html)。

`tools.verify_mysql_cdc_probe` 使用单独的 `industrial_energy_cdc_probe` 库、
`energy_cdc_probe` 账号、`energy-cdc-probe.*` 主题和本地 SQLite 投影。
它在真实 MySQL 中提交 INSERT 后执行物理 DELETE，读取 Debezium 的插入、
删除前镜像和 Kafka tombstone，再检查投影由一行变为零行。探针只会清理自己
生成的随机主键与标记；不会写项目事实表、`energy-events`、Flink 或 Hive。
因此它证明的是**源端到隔离下游投影**，不能替代主数仓删除传播验收。

在确认 Docker 镜像与卷位于 `D:\`、Docker 可用之后，按顺序执行：

```powershell
docker compose -f docker-compose.yml -f docker-compose.cdc.yml --profile streaming --profile cdc up -d mysql kafka cdc-topics-init
python -m tools.verify_mysql_cdc_probe --prepare
docker compose -f docker-compose.yml -f docker-compose.cdc.yml --profile streaming --profile cdc up -d debezium-connect
python -m tools.verify_mysql_cdc_probe --run --output output/mysql_cdc_probe_YYYYMMDD.json
```

`--prepare` 检查 ROW/FULL binlog、创建隔离库与最小事实表、生成仅用于探针的
随机口令并授予读取/复制权限。真实口令只写入被忽略的
`cdc/secrets/connect-secrets.properties`，通过标准输入进入 MySQL 客户端。
`--run` 使用独立 connector ID 注册 CDC、等待任务运行、执行插入与删除，
保存 Kafka 分区/offset、源端 binlog 文件/位点及 SQLite 投影行数。
输出 JSON 和 SQLite 文件默认保存在 `D:\industrial_energy_analysis\output`；
更换报告文件名可以重跑，脚本拒绝覆盖旧证据。

仍需单独实现并验收 Debezium 事件转项目事件契约，以及主业务 Hive/Spark
DWD、DWS、ADS 的硬删除传播。上述探针的通过结果只覆盖所列隔离路径。

## 2026-09-30 实机证据

后续已增加[CDC 业务数仓入口与验收](阶段三_CDC业务数仓接入.md)：业务事实结构的
真实删除已通过原项目 DWD/DWS/ADS SQL，包含旧插入消息追加重放与部分失败恢复。
该入口改用 binlog 坐标判定版本；本节早期最小探针仍只覆盖其明确列出的隔离路径。

运行方式为 Kafka 3.9.1 / Java 21 加 Debezium MySQL 3.6.0.Final 插件；
源端是 Windows Docker 中的 MySQL 8.0，仓库端为原有 Linux VM 的 Spark 3.5.1
和 HDFS。没有修改业务事实表或现有 Airflow DAG。

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 真实 INSERT → 物理 DELETE → Kafka tombstone | PASS；源表 0 行，SQLite 投影 1 → 0，完整重放后 0 | [源端报告](../output/mysql_cdc_probe_20260930193749.json) |
| 重启连接器后再次执行真实删除，并重放重启前删除的记录 | PASS；connector/task RUNNING，新旧探针删除后及完整重放后均 0 | [重启后报告](../output/mysql_cdc_probe_restart_20260930193749.json) |
| 将捕获的真实 CDC 行镜像写入独立 Hive ODS/DWD/DWS | PASS；有效行数 1 → 0 → 0，DWD 保留删除标记 | [Hive 报告](../output/mysql_cdc_hive_projection_20260930193749.json) |

Hive 校验使用 `tools/verify_cdc_hive_projection.py`，读取源端报告里的真实
行镜像、Kafka 分区/offset 和 binlog 位点，按单分区 Kafka offset 判定最新版本。
它不是直接订阅 Kafka 的生产消费任务；源端报告的 SHA256 写入 Hive 报告，
用于关联证据。ODS 中保留重放事件，DWD/DWS 中删除行不会恢复为有效行。

在虚拟机项目目录中，以适配 Spark 的 Python 3.11 执行：

```bash
PYSPARK_PYTHON=/path/to/python3.11 PYSPARK_DRIVER_PYTHON=/path/to/python3.11 \
  /usr/local/spark/bin/spark-submit --master 'local[2]' tools/verify_cdc_hive_projection.py \
  --evidence output/mysql_cdc_probe_20260930193749.json \
  --database energy_cdc_probe_YYYYMMDDHHMMSS \
  --output output/mysql_cdc_hive_projection_YYYYMMDDHHMMSS.json
```

数据库后缀必须替换为新的 14 位时间戳。脚本拒绝复用已有数据库、HDFS 路径
或输出文件。全量快照一致性、断网期间的删除恢复、主业务契约转换、ADS 传播、
schema 演进、多分区排序和多节点 HA 均未由这三份报告覆盖。

参考：[Debezium MySQL connector](https://debezium.io/documentation/reference/stable/connectors/mysql.html)、
[Kafka Connect 配置提供者](https://docs.confluent.io/platform/current/connect/userguide.html#externalizing-secrets)。
