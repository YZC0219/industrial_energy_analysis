# Windows / Docker 故障排查与答辩演示

本文是本地复现和答辩现场 runbook。Metabase 已于 2026-09-25 在本机完成浏览器与权限验收；重新部署或换数据后仍需复验，Docker 命令是否成功以现场容器状态为准。

## 演示前准备

建议在答辩前一晚完成一次全链路运行，现场使用已生成的报告和查询快照作后备，避免临时下载镜像或依赖外网。

1. 确认项目位于 `D:\industrial_energy_analysis`，Docker Desktop 已启动。
2. 在 Docker Desktop 的 **Settings → Resources → Advanced** 检查 Docker disk image location 指向 D 盘。镜像和命名卷由 Docker Desktop 管理，不要手动移动其虚拟磁盘文件。
3. 在 PowerShell 项目目录运行 `docker compose config --quiet`，检查配置能否解析；如需容器演示，再运行 `docker compose up -d --build`。
4. 检查 `docker compose ps`，等待 `mysql`、`postgres`、`airflow-webserver`、`airflow-scheduler` 健康/运行后，再打开 Airflow。首次启动会拉取镜像和构建 Airflow 镜像，预留时间且需要网络。
5. 准备离线展示：`output/report.html`、`output/Q*.csv`，以及阶段一/二证据 `output/spark_performance.jsonl`、`output/engine_comparison.json`、`output/lakehouse_validation.json`、`output/ml_model_metrics.json`、`output/ml_deep_metrics.json`。运行前检查这些文件与 README/论文引用的是同一版代码和数据；不要现场手工改指标。
6. 如果演示 API，另开一个 PowerShell 窗口启动 API，保持窗口运行：

   ```powershell
   Set-Location D:\industrial_energy_analysis
   python -m uvicorn src.api:app --host 127.0.0.1 --port 8000
   ```

   打开 `http://127.0.0.1:8000/docs`，并用下面的只读检查确认服务与产物可用：

   ```powershell
   Invoke-RestMethod http://127.0.0.1:8000/health
   Invoke-RestMethod "http://127.0.0.1:8000/api/v1/report-summary"
   ```

## 7 分钟答辩演示顺序

| 时间 | 演示内容 | 讲述重点 |
|---|---|---|
| 0:00–0:45 | 项目目标与架构图 | 模拟数据到清洗、MySQL 星型模型、分析、报告；Airflow 编排，Hive/Spark 是可选旁路。 |
| 0:45–1:45 | 数据质量闭环 | 展示注入的问题、修正/拒绝留痕，以及“异常值置空再插补”如何避免整行丢失能源品种。 |
| 1:45–2:45 | 指标与异常证据 | 用 Q26/Q27 或报告展示 2σ、产量基线、CUSUM；强调它们是互补信号，不等同于已确认设备故障。 |
| 2:45–3:45 | 分层与分布式证据 | 展示 Hive ODS→DWD→DWS→ADS、迟到修正重算证据和 `engine_comparison.json`；说明结论仅覆盖已验收的单节点规模。 |
| 3:45–4:45 | 预测对比 | 展示滚动验证逐折指标和 provenance；说明误差只代表当前模拟数据与切分，告警提前量因缺少经确认事件标签而为 N/A。 |
| 4:45–5:45 | 服务接口 | 展示 `/docs`、`/health`、报告摘要或按车间筛选的只读指标接口；提醒 API 读取最新生成的 CSV 快照，不是实时查库。 |
| 5:45–6:30 | 在线报告 | 打开自包含 `output/report.html`，筛选车间/日期，展示从总览到异常证据的路径。 |
| 6:30–7:00 | 局限与下一步 | 如实说明 Metabase 已在本机验收但尚非生产部署，实时/生产安全能力边界，以及哪些结论仍需要真实数据验证。 |

如果现场网络、Docker 拉取或数据库初始化异常，直接切换到静态报告、版本化查询结果和已保存的实验 JSONL/JSON。切换时明确说明这是预先生成的可复现证据，不把静态材料描述成实时运行。

## Windows / PowerShell 排查

| 症状 | 检查与处理 |
|---|---|
| `python` / `pip` 找不到 | 用 `py --version` 检查 Python Launcher；若项目依赖未安装，按 README 的安装步骤执行。优先把项目、虚拟环境与依赖缓存放在 D 盘。 |
| CSV 编码或路径报错 | 从仓库根目录启动命令；检查 `output` 是否存在及文件名是否正确。API 可通过 `ENERGY_OUTPUT_DIR` 指向另一目录，但该目录必须含相应的 `Q*.csv`。 |
| 端口已占用 | 检查本机监听：`Get-NetTCPConnection -LocalPort 8080,3307,8000,3000 -State Listen -ErrorAction SilentlyContinue`。确认占用进程后，关闭该应用或调整 Compose/API 端口映射；不要盲目结束进程。 |
| 浏览器打不开 `localhost:8080` | 确认 Docker Desktop 正在运行，执行 `docker compose ps` 和 `docker compose logs --tail 100 airflow-webserver airflow-scheduler airflow-init`；Airflow Web UI 默认账号为 `admin / admin`，仅适用于本地演示。 |
| API 返回 503 或 `/health` 为 degraded | 检查缺失文件名和 `output/Q*.csv` 是否由完整分析任务生成。API 不会替你运行 SQL，也不会把缺失数据伪装成 0。 |
| API 返回 422 | 检查日期格式是否为 `YYYY-MM-DD`，且 `date_from` 不晚于 `date_to`。 |
| D 盘空间增长 | Docker 镜像/卷占用由 Docker Desktop 管理；用 `docker system df` 查看。清理前先确认镜像或卷用途；不要使用 `docker system prune --volumes` 或 `docker compose down -v`，它们可能删除项目数据库和 BI 持久化数据。 |

## Docker Compose 排查

在项目根目录执行：

```powershell
docker compose config --quiet
docker compose ps
docker compose logs --tail 100 mysql postgres airflow-init airflow-webserver airflow-scheduler
```

| 症状 | 检查与处理 |
|---|---|
| 服务一直 `starting` / `unhealthy` | 查看对应服务日志；MySQL 首次初始化可能需要几十秒。等健康检查完成后再触发 DAG，不要重复删除数据库卷来“重置”。 |
| 3307 端口映射失败 | 宿主机 3307 已被占用。先识别占用进程，再将 `docker-compose.yml` 的宿主端口改为未占用端口；容器间连接仍使用 `mysql:3306`。 |
| Airflow DAG 看不到或任务导入失败 | 检查 `docker compose logs --tail 100 airflow-scheduler airflow-webserver`；确认 `dags/` 与项目目录按 Compose 挂载，改动后等待 scheduler 扫描完成。 |
| 任务报告找不到 CSV | 本项目将宿主机 `./output` 挂载为 `/opt/airflow/project/output`，MySQL 装载容器也需要该只读路径。检查命令从项目根目录执行且文件确实存在。 |
| Metabase 页面不可用 | 检查 `docker compose --profile bi ps` 和 `docker compose --profile bi logs --tail 100 metabase metabase-db`；恢复后运行 `python -m tools.verify_bi_runtime`。镜像首次拉取依赖网络。端口仅绑定 `127.0.0.1:3000`。不要用 `down -v` 删除配置库。 |
| MySQL 数据源连接失败 | Metabase 容器中使用主机 `mysql`、端口 `3306`，不能填 `localhost`；确认 MySQL 健康、BI 只读账号已按阶段四文档创建。 |

安全的停止方式是停止服务但保留数据卷：

```powershell
docker compose stop
```

需要查看 D 盘存储设置时，只检查 Docker Desktop 磁盘镜像位置；不要在 Docker Desktop 停止运行时手工移动或删除其 VHDX 文件。仓库的 `docker compose down` 不带 `-v` 会保留命名卷，但答辩结束通常无需删除容器或数据。

## 验收边界

- 本文中的命令是仓库配置对应的操作路径；`docker compose config --quiet` 通过只证明配置可解析，不代表当前机器上的镜像、容器或端口运行正常。
- Metabase 的 UI、筛选、钻取和真实权限边界已在 2026-09-25 本机验收；仓库保留脱敏的 `output/metabase_runtime_20260925.json` 与筛选截图。答辩现场仍应重新运行验收脚本，自动化契约测试或旧截图不能证明当前实例仍正常。
- FastAPI 是只读文件服务，`/health` 只确认关键文件存在；它不证明 CSV 同批次、足够新或实时。
- 模拟数据上的预测、异常和经济估算不能被表述为生产现场实测收益或已确认故障根因。
