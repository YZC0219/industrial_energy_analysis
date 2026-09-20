# 工业能耗分析 (Industrial Energy Analysis)

一套完整的工业能耗数据管道: **模拟原始数据 → 清洗 → 入 MySQL → SQL 分析**。
覆盖 8 个车间、6 种能源品种、2024-01-01 ~ 2025-12-31 共 731 天的日粒度能耗与产量数据。

## 目录结构

```
industrial_energy_analysis/
├─ data/
│   └─ raw_energy_data.csv      # 原始数据(含脏数据), 由 generate_data.py 生成
├─ src/
│   ├─ generate_data.py         # 生成模拟原始数据
│   ├─ clean_data.py            # 清洗 + 结构化, 输出到 output/
│   ├─ import_mysql.py          # 建表 / 装载 / 跑分析
│   ├─ make_report.py           # 把查询结果渲染成可视化报告
│   └─ report_template.html     # 报告模板(HTML/CSS/SVG, 数据以占位符注入)
├─ sql/
│   ├─ create_table.sql         # 建库、维表、事实表、视图、主数据
│   └─ analysis.sql             # 23 条业务分析查询
├─ dags/
│   └─ energy_pipeline_dag.py   # Airflow DAG: 五个阶段串成一条可调度管道
├─ docker/
│   └─ airflow/Dockerfile       # Airflow 镜像 + 项目依赖
├─ docker-compose.yml           # 一键起: postgres + mysql + airflow
├─ .env.example                 # 环境变量模板(UID / Fernet key / 口令)
├─ output/                      # 清洗结果 + 分析结果导出 + report.html
├─ README.md
└─ requirements.txt
```

## 快速开始

```bash
pip install -r requirements.txt

# 1) 生成原始数据(含脏数据)
python src/generate_data.py

# 2) 清洗
python src/clean_data.py

# 3) 建库建表 + 装载 + 执行分析
python src/import_mysql.py --init --run-analysis --user root --password 你的密码

# 4) 出可视化报告
python src/make_report.py
```

也可用环境变量提供口令, 避免明文出现在命令行历史中:

```bash
# Windows PowerShell
$env:MYSQL_PWD="你的密码"; python src/import_mysql.py --init --run-analysis

# Linux / macOS
MYSQL_PWD=你的密码 python src/import_mysql.py --init --run-analysis
```

只跑分析、不重建表:

```bash
python src/import_mysql.py --run-analysis
```

## 用 Airflow 调度

上面的四步也可以交给 Airflow 编排 —— 依赖、重试、失败告警、调度周期都由 DAG 管，
不需要人盯着顺序。

```bash
docker compose up -d          # 首次会构建镜像, 约 2~3 分钟
# 打开 http://localhost:8080  账号 admin / admin
# 在 DAG 列表里取消 energy_pipeline 的 Pause, 点 Trigger
```

**前置条件**：Docker。Windows 上需要装 WSL2 + Docker Desktop（Airflow 官方不支持
Windows 原生运行）。

### 五个服务

| 服务 | 作用 | 挂载 |
|---|---|---|
| `postgres` | Airflow 自己的元数据库（DAG 状态、任务实例） | `postgres-db` 卷 |
| `mysql` | 项目的分析仓库（星型模型 + 23 条查询跑在这里），映射到宿主机 `3307` | `mysql-data` 卷 + `./output`（只读）|
| `airflow-init` | 一次性任务：建元数据库表 + 建管理员账号，跑完即退 | — |
| `airflow-scheduler` | 调度器 | `./dags`、`./`（项目目录）|
| `airflow-webserver` | Web UI，`localhost:8080` | 同上 |

元数据库用 Postgres 而非复用 MySQL，是因为两者职责不同：一个是调度器的内部状态，
一个是业务数据。混在一起的话重建业务库会连调度历史一起清掉。

**为什么 mysql 也要挂 `./output`**：装载用的是 `LOAD DATA LOCAL INFILE`，
语句里那个文件是 **MySQL 服务端**打开的，不是执行脚本的 airflow 容器。
两个容器的文件系统互相看不见，不在 mysql 这边挂上、且路径与 airflow 侧对齐
（都挂到 `/opt/airflow/project/output`），服务端就会报 `File not found`
并静默装入 0 行。只读挂载足够 —— 这个目录 mysql 只读不写。

### DAG 结构

```
generate_raw_data → clean_data → load_warehouse → run_analysis → build_report
```

- **幂等**：`load_warehouse` 走的 `create_table.sql` 用 `DROP TABLE IF EXISTS` 重建表，
  所以整条管道重跑任意次结果一致，不会数据翻倍。
- **并发控制**：`max_active_runs=1`。管道是全量重建，两次运行并发会互相覆盖。
- **重试**：失败自动重试 2 次，指数退避（1min → 2min → 4min）。数据库冷启动没就绪
  这类瞬时故障不该让整条管道挂掉。
- **告警**：`on_failure_callback` 里留了钩子，接 Slack/钉钉只需替换成 webhook 调用。
- **不回补**：`catchup=False`。全量重建的管道回补历史没有意义。

任务切分、每个任务的职责说明写在 `dags/energy_pipeline_dag.py` 的 docstring 里，
在 Airflow UI 的 Graph 视图点开 DAG 就能看到。

### 配置

```bash
cp .env.example .env      # 可选; 不建也能跑, 用的是演示默认值
```

`.env` 里三个变量：`AIRFLOW_UID`（Linux/macOS 下设成 `id -u`，Windows 保持 50000）、
`AIRFLOW_FERNET_KEY`（**真实部署前必须重新生成**）、`MYSQL_ROOT_PASSWORD`。
`.env` 已在 `.gitignore` 里，进仓库的是 `.env.example` 这份模板。

## 数据模型 (星型模型)

| 类型 | 表 | 说明 |
|---|---|---|
| 维表 | `dim_workshop` | 车间/工序, 含是否连续生产、产量单位 |
| 维表 | `dim_energy_type` | 能源品种, 含**折标煤系数**、**碳排放因子**、参考单价 |
| 维表 | `dim_calendar` | 日期维, 标记周末与法定节假日 |
| 事实 | `fact_energy_consumption` | 车间 × 日 × 能源品种 的消耗明细 |
| 事实 | `fact_production` | 车间 × 日的产量 |

三个视图封装了口径计算, 分析 SQL 直接引用:

- `v_energy_enriched` — 明细级, 关联维表并算出 `std_coal_kgce`(折标煤) 与 `co2_kg`
- `v_daily_workshop` — 车间 × 日 汇总(综合能耗 tce / 碳排放 / 费用)
- `v_monthly_workshop` — 车间 × 月 汇总, **含单位产品能耗**(单耗)

### 关键口径

- **折标煤系数** 取 GB/T 2589-2020《综合能耗计算通则》当量值, 如电力 0.1229 kgce/kWh、天然气 1.3300 kgce/m³。
- **碳排放因子**: 电力取生态环境部全国电网平均排放因子 0.5703 kgCO₂/kWh; 燃料取 IPCC 缺省值; 压缩空气属二次能源, 不重复计入碳排。
- **重复计算提示**: `W07 动力站` 属公用工程, 其产出的蒸汽被其他车间二次消费, 全厂口径会重复计算。能耗结构分析(Q01/Q04)用全厂口径, **单耗类分析(Q02/Q07/Q08/Q16/Q20)一律剔除公用工程**。

## 清洗脚本做了什么

原始 CSV 刻意注入了 10 类质量问题, `clean_data.py` 逐一处理并留痕:

| 问题 | 处理方式 | 留痕文件 |
|---|---|---|
| 日期 4 种格式混用 | 逐一格式尝试解析, 全部失败则剔除 | `clean_rejects.csv` |
| 车间名/能源名 别名与错别字 | 别名表映射到标准编码 | — |
| 能源编码缺失 | 按名称回填 | — |
| 单位写法不统一 | 归一为能源主数据的标准单位 | — |
| 重复记录 | 按 日期×车间×能源 业务键去重(非整行相等) | `clean_rejects.csv` |
| 消耗量离群 (10 倍级) | 按 车间×能源 分组 `Q3 + 3×IQR` 剔除 | `clean_rejects.csv` |
| 负消耗量 (仪表倒走) | 取绝对值 | `clean_fixed.csv` |
| 消耗量缺失 | 按 车间×能源×年月 中位数插补 | `clean_fixed.csv` |
| 单价缺失或为 0 | 按 能源×年月 中位数插补 | `clean_fixed.csv` |
| 费用 ≠ 量×价 (偏差>5%) | 以 量×价 重算 | `clean_fixed.csv` |

清洗后输出 `output/clean_report.txt` 报告, 记录每一步的处理条数与数据保留率。

### 落盘时的两个约定

改 `clean_data.py` / `generate_data.py` 的输出部分时注意:

- **行尾固定为 LF**: 所有 `to_csv` 都显式带 `lineterminator="\n"`。不指定的话
  pandas 跟着操作系统走(Windows 出 CRLF、Linux 出 LF), 而下游 `LOAD DATA` 的
  行终止符只能写死一个 —— 行尾对不上时整个文件会被当成一行, 静默装入 0 行。
- **BOM**: 用 `encoding="utf-8-sig"` 写, Excel 双击打开中文表头才不乱码。
  读取侧(`import_mysql.py`、`make_report.py`)相应地也用 `utf-8-sig` 打开。

## 分析查询清单 (`sql/analysis.sql`)

| 编号 | 主题 |
|---|---|
| Q01–Q02 | 能源消费总览(全厂口径 / 剔除公用工程口径) |
| Q03 | 各车间综合能耗排名与占比 |
| Q04 | 能源结构(折标煤 + 费用双口径) |
| Q05 | 月度能耗趋势与环比 MoM |
| Q06 | 各车间能耗同比 YoY |
| Q07–Q08 | 单位产品能耗月度趋势 / 同比, 衡量节能改造成效 |
| Q09 | 能源费用帕累托分析 |
| Q10 | 各车间能耗结构交叉表 |
| Q11–Q12 | 气温分档对电耗气耗的影响 / Pearson 相关系数 (均限定连续型车间, 排除停产日干扰) |
| Q13 | **停产日待机损耗**分析 |
| Q14–Q15 | 工作日·周末·节假日能耗对比, 各节假日水平 |
| Q16 | 单耗异常日检测 (均值 + 2σ) |
| Q17 | 连续 3 日能耗上涨识别 |
| Q18 | 全厂 7 日移动平均与波动 |
| Q19 | 能源采购单价月度波动 vs 参考价 |
| Q20 | 电耗强度月度趋势 |
| Q21 | 碳排放强度 |
| Q22 | 能耗突增预警 (环比 > 25%) |
| Q23 | 能耗最高的车间日 TOP20 |

`--run-analysis` 会把每条查询结果导出为 `output/Q01_能源消费总览.csv` 等文件。

## 可视化报告

```bash
python src/make_report.py       # -> output/report.html
```

`make_report.py` 读取 `output/` 下的查询结果 CSV，把它们作为 JSON 内联进
`src/report_template.html`，写出一个**自包含的 `output/report.html`** ——
零依赖、无构建、无网络请求，双击即可打开。

报告分 8 段：总览 → 结构 → 趋势 → 成因 → 成效 → 损耗 → 风险 → 方法。
只用 Python 标准库，不需要额外依赖。

> **注意**：`src/report_template.html` 是模板，不是报告。直接打开它只会看到
> 一条"还没有数据"的提示——图表数据由 `make_report.py` 注入。请看
> `output/report.html`。
图表是手写 SVG（不引第三方图表库），每张图都带：

- **深/浅色主题**：跟随系统，也可被 `data-theme` 覆盖；每个主题用各自验证过的
  色阶，不是简单反色。
- **悬停提示**：折线图有十字准星，柱状图有整行命中区（命中区比柱子本身大）。
- **表格视图**：每张图下方可展开等价表格，保证任何数值都不只能靠颜色或悬停获取。

配色不是拍脑袋选的：分类色板（蓝＝能耗口径、橙＝费用口径、青＝碳排放口径）
已用 OKLab ΔE 校验过色盲可分辨性与对比度，深色与浅色两套各自独立通过。
同一含义全篇同色——橙色在整份报告里只代表"费用"。

若改了 `analysis.sql` 或重新生成了数据，重跑 `make_report.py` 即可刷新报告；
报告里引用的派生结论（如"剔除公用工程占 72.7%"）也由脚本从数据算出，不写死。

## 数据生成逻辑 (业务规则)

生成的数据不是随机数, 而是带业务规律的, 可用于验证分析结果是否符合预期:

- **生产日历**: 连续型车间(熔炼/轧制/热处理/动力站)全年不停产; 间歇型车间周日与法定节假日基本停产。
- **待机损耗**: 停产日仍保留 `standby` 比例的基础负荷。
- **能耗随产量同步变化**: 能耗 = 负荷形状 × 产量年趋势 × 单耗年趋势, 因此能耗增速 = 产量增速 + 单耗增速。

随机种子固定为 `20240918`, 数据可复现。下表是当前数据下的**实际跑出结果**, 可作为回归基线:

| 业务规则 | 预期 | 实际结果 | 对应查询 |
|---|---|---|---|
| 产量年增 5%, 单耗年降 3% | 能耗同比 ≈ +1.9% | +1.39% | Q06 |
| 单耗年降 3% | 各车间单耗同比 ≈ -3% | -2.12% ~ -3.78% | Q08 |
| 天然气冬季采暖 | 与气温强负相关 | r = **-0.9507** | Q12 |
| 蒸汽冬季高 | 与气温强负相关 | r = **-0.9052** | Q12 |
| 电力冬夏双高峰 | 呈 U 形, 线性 r 偏弱 | r = 0.1673, 分档可见 U 形 | Q11 / Q12 |
| 间歇型车间停产日仍有基础负荷 | 可检出待机损耗 | 表面处理车间 58 天 / 110.8 tce | Q13 |
| 季节峰谷差约 20% | 环比 > 25% 属异常 | 检出 5 条, 集中在 11 月 | Q22 |
| 2024-2025 全厂 | — | 63,653.91 tce / ¥2.15 亿 / 156,558.71 tCO2 | Q01 |

## 环境要求

- Python 3.9+
- MySQL 8.0+

### 关于 `local_infile`

装载这一步用 `LOAD DATA LOCAL INFILE` 批量灌数(比逐行 INSERT 快一两个数量级),
它需要在**服务端**开启 `local_infile`, 两种跑法各自的开启方式不同:

| 跑法 | 怎么开 |
|---|---|
| 直接用本机 MySQL | 在 `my.ini` 的 `[mysqld]` 下加 `local_infile=1` 后重启服务 |
| 用 Docker Compose | 无需手工操作, `docker-compose.yml` 里 mysql 服务的 `command` 已带 `--local-infile=1` |

服务端没开时 `import_mysql.py` 会自动退化为批量 `INSERT`, 所以两种情况都能跑通。

> **容器环境的一个坑**: `LOAD DATA LOCAL INFILE` 里的文件是 **MySQL 服务端**
> 按自己的文件系统打开的, 不是客户端。所以 `docker-compose.yml` 里 mysql 服务
> 也挂载了 `./output`(只读), 且路径与 airflow 容器保持一致 —— 否则服务端会
> 报 `File not found`。宿主机直跑时不存在这个问题(客户端和服务端是同一台机器)。
