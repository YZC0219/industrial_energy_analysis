# 工业能耗分析（Industrial Energy Analysis）

这是我的毕业设计项目。我围绕工业企业的能源管理场景，独立搭建了一条从模拟数据、数据清洗、MySQL 数据仓库、业务分析、异常检测，到 Airflow 调度和可视化报告的完整数据管道。

我没有把它做成只展示几张图的分析作业，而是把重点放在数据质量、统计口径、可重复运行和回归验证上。项目覆盖 8 个车间、6 种能源、2024-01-01 至 2025-12-31 共 731 天的数据，最终形成 29 组分析结果和一份可离线打开的 HTML 报告。

## 我完成的内容

- 我用固定随机种子生成带业务规律的模拟工业数据，并主动注入日期混乱、别名、缺失值、重复记录、负数、离群值和费用错误等 10 类质量问题。
- 我实现了可追溯的数据清洗流程，把剔除记录、修正记录和清洗统计分别落盘。
- 我在 MySQL 中设计了星型模型，包括 3 张维表、2 张事实表和 5 个口径/算法视图。
- 我编写了 Q01～Q29 分析查询，覆盖能耗结构、费用、单耗、同比环比、待机损耗、碳排放和异常检测。
- 我实现了 2σ、产量基线和 CUSUM 三种异常检测方法，并对它们的检出结果进行了对比。
- 我用原生 HTML、CSS、JavaScript 和 SVG 生成自包含报告，支持日期、车间和日型筛选，不依赖外部图表库。
- 我用 Airflow 和 Docker Compose 编排整条管道，并验证失败重跑、幂等装载和历史数据修正。
- 我建立了离线测试、数据库测试、29 份查询快照和端到端测试，防止口径或数据在改动后静默漂移。

## 项目架构

```mermaid
flowchart LR
    A[模拟原始数据] --> B[数据清洗与质量报告]
    B --> C[MySQL 星型模型]
    C --> D[29 组 SQL 分析]
    D --> E[自包含 HTML 报告]
    F[Airflow DAG] --> A
    F --> B
    F --> C
    F --> D
    F --> E
    G[pytest 与回归基线] -.验证.-> B
    G -.验证.-> C
    G -.验证.-> D
    G -.验证.-> E
```

Airflow 中的执行链为：

```text
generate_raw_data → clean_data → load_warehouse → run_analysis → build_report
```

（上面是 `dags/energy_pipeline_dag.py` 里的 `task_id`；对应的脚本依次是
`generate_data.py`、`clean_data.py`、`import_mysql.py --init`、
`import_mysql.py --run-analysis`、`make_report.py`。）

## 技术栈

| 领域 | 技术 |
|---|---|
| 数据处理 | Python、pandas、NumPy |
| 数据仓库 | MySQL 8.0、星型模型、窗口函数 |
| 数据装载 | PyMySQL、`LOAD DATA LOCAL INFILE`、幂等 upsert |
| 调度与运行 | Apache Airflow、Docker Compose |
| 分析方法 | 同比/环比、2σ、最小二乘基线、CUSUM |
| 可视化 | HTML、CSS、JavaScript、SVG |
| 质量保障 | pytest、快照测试、端到端测试 |

## 目录结构

```text
industrial_energy_analysis/
├─ dags/
│  └─ energy_pipeline_dag.py       # Airflow 五任务 DAG
├─ data/                            # 本地生成的原始数据
├─ docker/
│  └─ airflow/Dockerfile
├─ docs/                            # 指标字典、质量说明与工程复盘
├─ output/                          # 本地生成的清洗、分析和报告产物
├─ sql/
│  ├─ create_table.sql              # 建库、维表、事实表和视图
│  └─ analysis.sql                  # Q01～Q29 分析查询
├─ src/
│  ├─ generate_data.py              # 生成模拟数据
│  ├─ clean_data.py                 # 清洗和质量留痕
│  ├─ import_mysql.py               # 建表、装载和执行分析
│  ├─ make_report.py                # 生成可视化报告
│  └─ report_template.html          # 报告模板
├─ tests/
│  ├─ baseline/                     # 29 份查询回归基线
│  └─ test_*.py                     # 离线、数据库和端到端测试
├─ tools/                            # 校准与验收工具
├─ docker-compose.yml
├─ pytest.ini
└─ requirements.txt
```

`data/raw_energy_data.csv` 和 `output/` 下的文件都是可复现产物，因此我没有把它们继续纳入版本控制。运行下面的流程即可重新生成。

## 快速开始

### 方式一：在本机运行

环境要求：Python 3.9+、MySQL 8.0+。

```bash
pip install -r requirements.txt

# 1. 生成带脏数据的原始 CSV
python src/generate_data.py

# 2. 清洗并生成质量报告
python src/clean_data.py

# 3. 建表、装载数据并执行全部分析
python src/import_mysql.py --init --run-analysis --user root --password 你的密码

# 4. 生成自包含 HTML 报告
python src/make_report.py
```

我也支持通过环境变量传入 MySQL 密码，避免密码进入命令行历史：

```powershell
# Windows PowerShell
$env:MYSQL_PWD="你的密码"
python src/import_mysql.py --init --run-analysis
```

```bash
# Linux / macOS
MYSQL_PWD=你的密码 python src/import_mysql.py --init --run-analysis
```

只重新执行分析时，可以运行：

```bash
python src/import_mysql.py --run-analysis
```

最终报告位于 `output/report.html`，它已经内联全部数据和样式，可以直接在浏览器中打开。

### 方式二：使用 Docker Compose 和 Airflow

```bash
docker compose up -d --build
```

启动后打开 `http://localhost:8080`，使用 `admin / admin` 登录，在 DAG 列表中启用并触发 `energy_pipeline`。

编排环境包含 5 个服务：

| 服务 | 作用 |
|---|---|
| `mysql` | 保存维表、事实表和分析视图 |
| `airflow-init` | 初始化 Airflow 数据库和管理员账号 |
| `airflow-webserver` | 提供 Airflow Web 界面 |
| `airflow-scheduler` | 调度 DAG 任务 |
| `airflow-triggerer` | 支持可延迟任务 |

## 数据模型

我采用星型模型组织数据：

| 类型 | 表 | 说明 |
|---|---|---|
| 维表 | `dim_workshop` | 车间、工序、生产方式和产量单位 |
| 维表 | `dim_energy_type` | 能源品种、折标煤系数、碳排放因子和参考单价 |
| 维表 | `dim_calendar` | 日期、工作日、周末和法定节假日 |
| 事实表 | `fact_energy_consumption` | 车间 × 日期 × 能源品种的消耗明细 |
| 事实表 | `fact_production` | 车间 × 日期的产量 |

我把重复使用的统计口径和算法中间量封装在 5 个视图中：

- `v_energy_enriched`：统一计算折标煤、碳排放和费用。
- `v_daily_workshop`：汇总车间日度综合能耗。
- `v_monthly_workshop`：汇总车间月度能耗和单位产品能耗。
- `v_unit_energy_baseline`：计算产量基线、残差和白化残差。
- `v_unit_energy_cusum`：计算上下侧 CUSUM 和判定限。

完整指标定义、单位和适用口径见 [`docs/指标字典.md`](docs/指标字典.md)；
整体的设计取舍与分层见 [`docs/系统设计文档.md`](docs/系统设计文档.md)。

## 我统一的统计口径

工业能源分析最容易出现的问题不是 SQL 报错，而是不同页面使用了不同口径。我在项目中明确区分了三类口径：

1. **全厂口径**：用于能源结构、总量和总费用分析。
2. **剔除公用工程口径**：用于单位产品能耗，避免动力站生产蒸汽与其他车间消费蒸汽造成重复计算。
3. **仅连续型车间口径**：用于气温相关性分析，减少停产日对相关系数的干扰。

折标煤系数采用 GB/T 2589-2020 的当量值。电力碳排放因子采用 0.5703 kgCO₂/kWh；压缩空气作为二次能源，不重复计算碳排放。

## 数据清洗设计

我在原始数据中注入了 10 类问题，并为每种问题定义了明确处理方式：

| 问题 | 我的处理方式 | 留痕 |
|---|---|---|
| 多种日期格式 | 逐种格式解析，无法解析才剔除 | `clean_rejects.csv` |
| 车间名和能源名别名 | 映射为标准编码和名称 | 修正记录 |
| 能源编码缺失 | 根据标准能源名称回填 | 修正记录 |
| 单位写法不一致 | 统一为主数据中的标准单位 | 修正记录 |
| 业务键重复 | 按日期 × 车间 × 能源去重 | `clean_rejects.csv` |
| 消耗量离群 | 按车间 × 能源的 `Q3 + 3×IQR` 识别，置空后插补 | 拒绝与修正记录 |
| 负消耗量 | 取绝对值 | `clean_fixed.csv` |
| 消耗量缺失 | 按车间 × 能源 × 年月的中位数插补 | `clean_fixed.csv` |
| 单价缺失或为 0 | 按能源 × 年月的中位数插补 | `clean_fixed.csv` |
| 费用偏差超过 5% | 按消耗量 × 单价重算 | `clean_fixed.csv` |

我曾在这里发现并修复一个会制造假异常的缺陷：旧逻辑把离群值所在的整行删除，导致某些“车间-日期”永久少一种能源，日总能耗因此突然下降，后续 CUSUM 把它识别成持续异常。我把处理改成“只把离群单元格置空，再交给插补”，清洗后的 5,848 个车间日不再缺少能源品种。

修复效果如下：

| 指标 | 修复前 | 修复后 |
|---|---:|---:|
| 清洗后能耗明细 | 18,972 | 19,006 |
| 消耗量插补 | 295 | 329 |
| 品种数不足的车间日 | 34 | 0 |
| 单耗残差 Z 值下限 | -11.6 | -3.27 |

更完整的核对过程见 [`docs/数据清洗质量说明.md`](docs/数据清洗质量说明.md) 和 [`docs/问题发现与工程复盘.md`](docs/问题发现与工程复盘.md)。

## 分析内容

29 组查询大致分为以下几类：

| 查询 | 内容 |
|---|---|
| Q01～Q04 | 能源总量、车间排名、能源结构和费用结构 |
| Q05～Q08 | 月度趋势、环比、同比和单位产品能耗 |
| Q09～Q15 | 费用帕累托、车间结构、气温、停产日和节假日分析 |
| Q16～Q23 | 单耗异常、连续上涨、移动平均、价格、碳排和突增预警 |
| Q24 | 车间单耗完整序列及 μ ± 2σ 控制带 |
| Q25 | 考虑产量和季节因素的期望单耗与残差 |
| Q26 | 2σ、产量基线和 CUSUM 三种检测方法对比 |
| Q27 | 白化残差的上下侧 CUSUM 序列 |
| Q28～Q29 | 动态筛选所需的车间日度汇总和能源结构明细 |

## 异常检测方法

### 2σ 固定阈值

我用每个车间自己的历史均值和标准差识别单日大偏离。这个方法直观，但容易把节假日低产量造成的高单耗当成异常。

### 产量基线

我使用产量和年内季节项拟合期望单耗：

```text
期望单耗 = a + b·ln(产量) + g·sin(2π·年内日序/365) + h·cos(2π·年内日序/365)
```

模型在 SQL 中通过最小二乘正规方程闭式求解。相比固定阈值，它先解释产量和季节变化，再判断残差是否异常。

### CUSUM

我先按“车间 × 日历月”对白化残差去季节性，再计算上下侧 CUSUM。判定限没有直接照搬常见的 `5σ`，而是根据当前序列长度进行 5,000 次白噪声仿真，最终采用零分布 99 分位对应的 `h = 9.9σ`。

这个选择来自实际验证：对每个车间约 730 个观测点的序列，`5σ` 会产生过高的累计误报率，无法作为可靠阈值。

## 可视化报告

我没有引入第三方图表库，而是用 SVG 绘制图表，并把查询数据内联到一个 HTML 文件中。报告包括：

- 总览、结构、趋势、成因、成效、损耗和风险分析；
- 2σ 控制图、产量基线残差图和 CUSUM 图；
- 日期、车间和日型动态筛选；
- 深色/浅色主题；
- 悬停提示和等价表格视图；
- 经过 OKLab ΔE 与对比度检查的配色。

生成报告：

```bash
python src/make_report.py
```

`src/report_template.html` 只是模板，实际成品是 `output/report.html`。

## 自动化测试

```bash
# 默认只运行离线测试，不要求 MySQL
pytest

# 数据库测试
MYSQL_PORT=3307 pytest -m db

# 重跑整条管道的端到端测试
MYSQL_PORT=3307 pytest -m "db and slow"

# 经 Airflow 触发 DAG 并检查五个任务状态
python tools/verify_dag_run.py
```

| 测试文件 | 我验证的内容 | 是否需要 MySQL |
|---|---|---|
| `test_clean_data.py` | 清洗规则、业务键、缺失值、产物一致性 | 否 |
| `test_metric_dictionary.py` | 指标字典与代码中的系数、参数和口径一致 | 否 |
| `test_import_mysql.py` | 幂等装载、中断续跑、历史修正和旧版本保护 | 是 |
| `test_analysis_snapshot.py` | Q01～Q29 与基线逐行比对 | 是 |
| `test_end_to_end.py` | 产物完整性、时效性和多条链路结果自洽 | 是 |

我把 29 份查询结果保存在 `tests/baseline/`。当业务口径发生有意变更时，我先检查差异，再更新基线：

```bash
python tests/update_baseline.py --check
python tests/update_baseline.py
```

如果我无法解释数字为什么变化，就不会用刷新基线掩盖问题，而是先检查数据库数据是否陈旧、装载是否完整以及统计口径是否被意外修改。

每一层测试分别防什么、为什么这么分层、哪些地方**故意没有**加断言，见 [`docs/测试文档.md`](docs/测试文档.md)。

## 幂等装载与历史修正

能源事实表以 `(record_date, workshop_code, energy_code)` 作为业务唯一键。我先把 CSV 装入临时表，再执行 `INSERT ... SELECT ... ON DUPLICATE KEY UPDATE`。

更新时，我通过 `updated_at` 判断版本：只有严格更新的记录才能覆盖现有数据，旧批次重放不会把后来修正过的值改回去。`LOAD DATA LOCAL INFILE` 不可用时，批量 `INSERT` 分支复用同一套 upsert 逻辑。

这个设计解决了 `INSERT IGNORE` 会静默丢弃历史修正的问题，也保证了任务失败后可以安全重跑。

## 当前结果

固定随机种子为 `20240918`。当前数据得到的部分结果如下：

| 指标 | 结果 |
|---|---:|
| 2024～2025 全厂综合能耗 | 63,759.63 tce |
| 全厂能源费用 | 约 2.15 亿元 |
| 全厂碳排放 | 156,783.46 tCO₂ |
| 天然气与气温相关系数 | -0.9555 |
| 蒸汽与气温相关系数 | -0.9262 |
| 表面处理车间停产日待机损耗 | 58 天 / 110.89 tce |

这些数值由脚本生成，并由查询快照和文档一致性测试共同保护。

## 后续计划

接下来我会继续完成：

- 更完整的安装与故障排查说明；
- 系统设计和测试文档；
- 异常告警与异常详情之间的页面联动；
- 毕业论文、答辩材料和演示流程。

我会继续通过独立提交记录每项能力的演进，保证每次修改都能说明动机、实现和验证结果。
