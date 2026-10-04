# 能迹 EnergyTrace｜工业能耗分析与可视化

[![Build GitHub Pages report](https://github.com/YZC0219/industrial_energy_analysis/actions/workflows/build-pages-report.yml/badge.svg)](https://github.com/YZC0219/industrial_energy_analysis/actions/workflows/build-pages-report.yml)

[在线分析报告](https://yzc0219.github.io/industrial_energy_analysis/) · [系统设计](docs/系统设计文档.md) · [指标字典](docs/指标字典.md) · [项目复盘](docs/问题发现与工程复盘.md)

我是大数据专业的学生，想用一个完整项目练习从原始数据到分析报告的过程，所以在 AI 辅助下持续开发了这个工业能耗分析系统。我负责确定要分析的问题、检查结果、提出修改要求并逐步理解实现。它模拟一家制造企业两年的用能情况，覆盖 8 个车间、6 种能源和 731 天；项目用 Python 清洗数据、用 MySQL 建模和分析，再把结果做成可以筛选的网页报告。

项目使用的是**带业务规律的模拟数据**，不是企业真实生产数据。后面加入的 Hive、Spark、预测模型和实时处理是在单机或单节点环境中验证的实验，不能直接等同于生产环境的处理能力。

## 当前目录与新增交付（2026-10-04）

本仓库同步正式源码、配置示例、测试、技术文档及精选验收证据。运行密钥、依赖缓存、临时项目副本、学习练习环境、生成数据库和论文打包文件保留在本地，不进入版本库。

| 功能 | 目录 / 入口 | 使用与验证说明 |
|---|---|---|
| 智能诊断工作台 | `src/diagnostics.py`、`src/diagnostics.html` | 异常筛选、证据问答、人工处置及追加历史；见[工作台说明](docs/智能诊断工作台.md) |
| 统一指标消费 | `governance/`、`src/semantic_metrics.js` | 报告、API、浏览器基础指标和 Metabase 语义迁移；见[消费迁移](docs/语义层消费迁移_报告与API.md)与[Metabase 说明](docs/Metabase语义层迁移.md) |
| 持久化告警与恢复 | `streaming/alert_outbox.py`、`streaming/outbox_supervisor.py` | Kafka 桥接、Redis 恢复、重试预算、HTTP 确认与人工重入队；见[10 月 4 日复验记录](docs/告警与整体验收复验_20261004.md) |
| 增量审计与设备排程 | `lakehouse/`、`optimization/` | Iceberg 历史快照与模拟设备排程；见[四方向扩展](docs/工业数据生命周期_四方向扩展.md) |
| 前端分析公式 | `src/analysis_formulas.js`、`governance/analysis_formulas.py` | 分析公式独立扩展及原报告接入；见[报告入口说明](docs/分析公式接入原报告入口.md) |
| 学习与复现 | `docs/daily_lessons/` | 保留课程与操作说明；见[两个月学习计划](docs/两个月项目学习计划.md) |

已有分析输出时，可在项目根目录启动本地诊断入口：

```powershell
python -m uvicorn src.api:app --host 127.0.0.1 --port 8010
# 浏览器打开 http://127.0.0.1:8010/diagnostics
```

工作台默认读取 `output/` 下的分析结果；配置模型需设置服务进程环境变量，未配置时使用证据检索模式。该本地入口尚无用户身份认证，人工反馈不代表已验证的训练标签或节能效果。GitHub Pages 展示静态报告，诊断反馈与问答需另行运行 API 服务。

扩展依赖与离线检查：

```powershell
python -m pip install -r requirements.txt -r requirements-extensions.txt --cache-dir D:/industrial_energy_analysis/.pip-cache -r requirements-extensions.txt --cache-dir D:/industrial_energy_analysis/.pip-cache
python -m pytest -q --basetemp D:/industrial_energy_analysis/tmp/pytest
```

上述默认测试排除 MySQL `db` 测试；Docker、浏览器、Spark/Iceberg 和外部服务验收使用各文档的独立命令。历史验收记录及其适用边界见[复验说明](docs/告警与整体验收复验_20261004.md)，不表示已完成生产部署。

详细架构、数据模型、快速开始和分阶段路线图见[项目完整说明](docs/项目完整说明.md)。

## 我做了什么

| 环节 | 完成内容 |
|---|---|
| 数据准备 | 生成模拟用能与产量数据，注入日期格式、别名、缺失、重复、离群值等 10 类问题 |
| 清洗与装载 | 记录剔除和修正原因；建 MySQL 星型模型，支持历史修正、增量装载和失败重跑 |
| 数据分析 | 编写 Q01～Q29 查询，分析能耗结构、费用、单耗、同比环比、待机损耗和碳排放 |
| 异常分析 | 对比 2σ、产量基线和 CUSUM，检查不同方法会给出什么结论 |
| 结果展示 | 制作桌面和手机都能查看的 HTML 报告，支持日期、车间、日型筛选和数据大屏 |
| 结果验证 | 用查询快照、自动化测试和前端数据恒等式检查，防止改动后数字悄悄变化 |

后续我又借助 AI 辅助开发，把基础链路扩展到了 Hive ODS/DWD/DWS/ADS、Spark SQL、DataX、Airflow、预测模型、Kafka/Flink 单机实验、FastAPI 和 Metabase。这些内容各有验证范围，详细记录放在[阶段一](docs/阶段一_分层数仓与分布式计算.md)、[阶段二](docs/阶段二_预测与智能归因.md)、[阶段三](docs/阶段三_数据质量与实时管道.md)、[阶段四](docs/阶段四_服务化与可视化交付.md)。首页先介绍我最想展示的数据分析主线。

## 两个实际遇到的问题

**清洗造成了假异常。** 一开始我把离群值所在的整行删除，后来发现这会让 34 个“车间 × 日期”缺少一种能源，日能耗突然下降，CUSUM 也跟着报警。我改为只把异常单元格置空，再按车间、能源和月份插补。修复后，缺少能源品种的车间日从 34 个降到了 0 个，并加了回归测试。[完整排查过程](docs/问题发现与工程复盘.md)

**数据装进表了，报告却没有变化。** 我增加一天数据后，事实表行数正常增长，但查询结果没变。原因是日期维表没有覆盖新日期，分析视图的 `INNER JOIN` 把新记录过滤掉了。现在清洗会按数据范围扩展日期维表，装载时也会检查日期上下界。这个问题让我意识到，任务成功不代表分析结果一定正确。

## 目前能看到的结果

固定随机种子 `20240918` 下，模拟数据得到以下结果：

| 指标 | 结果 |
|---|---:|
| 2024～2025 年全厂综合能耗 | 63,759.63 tce |
| 全厂能源费用 | 约 2.15 亿元 |
| 全厂碳排放 | 156,783.46 tCO₂ |
| 4 个间歇生产车间的停产日待机损耗 | 58 天，约 79.39 万元 |
| 以 2024 年单耗为基线估算的 2025 年节能量 | 731 tce |

节能收益约 247 万元，指按模拟数据估算的**毛收益**，没有扣除改造投入，不能当作投资回报。详细计算口径见[指标字典](docs/指标字典.md)和[在线报告](https://yzc0219.github.io/industrial_energy_analysis/)。

## 项目怎么运行

本机基础链路需要 Python 3.12（与 CI 一致） 和 MySQL 8.0+：

```bash
pip install -r requirements.txt -r requirements-extensions.txt --cache-dir D:/industrial_energy_analysis/.pip-cache
python src/generate_data.py
python src/clean_data.py
python src/import_mysql.py --init --full --run-analysis --user root --password 你的密码
python src/make_report.py
```

生成的报告在 `output/report.html`。首次建库使用 `--init --full`；后续增量装载直接运行 `python src/import_mysql.py`，不要把 `--init` 放进日常任务。也可以用 Docker Compose 启动 Airflow：

```bash
docker compose up -d --build
```

打开 `http://localhost:8080`，在 Airflow 中触发 `energy_pipeline`。更详细的环境准备、数据模型和故障排查见[系统设计](docs/系统设计文档.md)与[运行手册](docs/Windows_Docker_故障排查与答辩演示.md)。

## 我怎么检查结果

```bash
pytest                       # 不连接 MySQL 的测试
MYSQL_PORT=3307 pytest -m db  # 数据库测试，端口按本机配置调整
node tools/verify_filter.mjs  # 检查筛选后的 KPI 与明细汇总是否一致
```

项目保存了 29 组 SQL 查询快照。修改统计口径后，我会先查清差异，再更新快照；前端还检查筛选后的汇总数字能否互相对上。[测试说明](docs/测试文档.md)

## 技术与范围

主要使用 Python、pandas、MySQL、SQL、HTML/CSS/JavaScript、Airflow 和 pytest。Hive/Spark/DataX、LightGBM/LSTM/Transformer、Kafka/Flink、FastAPI 与 Metabase 是在基础项目上继续扩展的内容。

Hive/Spark/DataX 已在 Ubuntu 单节点做过结果对照和迟到数据修正验证；10×/100× 只验证了隔离环境中的部分核心指标，不能代表完整生产链路扩容。预测模型在模拟数据的滚动切分上比较过误差，没有真实故障标签，因此不报告“提前预警天数”。Kafka/Flink 完成的是单机窗口和故障注入实验；隔离链路已验证物理删除 CDC 与 Hive 投影，主业务 CDC 已切换并启用每日 02:00（Asia/Shanghai）更新，自动批次恢复验收通过，运行仍依赖 Windows、Docker 与 VM/HDFS 在线。多节点高可用仍未完成。

我把这些限制写清楚，是为了让项目展示的结论能对应到实际做过的验证。更细的实验条件、结果和未完成事项，都在上面的阶段文档和 `output/` 证据文件中。
