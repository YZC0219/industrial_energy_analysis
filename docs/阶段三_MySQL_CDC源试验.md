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

这个提交只提供可选的源端配置，**未执行容器启动和真实硬删除试验**。
启用后至少需要保存以下证据，才能把“源端物理删除 CDC”标为完成：

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

仍需单独实现并验收 Debezium 事件转项目事件契约，以及 Hive/Spark DWD、DWS、
ADS 的硬删除传播。上述探针的通过结果只覆盖所列隔离路径。

参考：[Debezium MySQL connector](https://debezium.io/documentation/reference/stable/connectors/mysql.html)、
[Kafka Connect 配置提供者](https://docs.confluent.io/platform/current/connect/userguide.html#externalizing-secrets)。
