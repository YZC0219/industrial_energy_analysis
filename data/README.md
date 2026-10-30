# 能迹示例数据

[原始模拟数据 CSV](raw_energy_data.csv) · [29 份 SQL 分析结果](../tests/baseline/) · [在线可视化报告](https://yzc0219.github.io/industrial_energy_analysis/)

`raw_energy_data.csv` 是本项目生成的制造企业能源记录，**不是真实工厂实测数据**。它与报告中的模拟数据口径对应，可用于练习清洗、建模、筛选和异常分析。

| 项目 | 内容 |
|---|---|
| 时间范围 | 2024-01-01～2025-12-31，共 731 天 |
| 业务范围 | 8 个车间、6 种能源 |
| 原始记录 | 19,234 行，不含表头；15 个字段 |
| 文件格式 | UTF-8 CSV，LF 行尾 |
| 固定随机种子 | `20240918` |
| 生成入口 | `src/generate_data.py` |

字段包括日期、车间编码/名称、能源编码/名称、消耗量、计量单位、单价、费用、产量、产量单位、日均气温、记录状态、模拟来源系统和更新时间。

文件刻意包含缺失值、重复行、负值、单位及名称别名、日期格式差异和离群值，用于展示数据清洗。原始行不能直接求和后与报告对账；报告使用清洗、装载及 SQL 分析后的结果。`data_source=MES` 表示模拟的来源系统名称。

仓库已保留 `tests/baseline/Q01_*.csv`～`Q29_*.csv`；清洗产物可由现有代码重建，不重复提交本地缓存、日志和运行归档。`real/` 下的公开真实数据是独立案例，其来源见各自说明。

在项目根目录重新生成到 D 盘的独立目录：

```powershell
python src/generate_data.py --out D:/industrial_energy_analysis/tmp/reproduce/raw_energy_data.csv
```

本次发布原始 CSV 的 SHA-256：

```text
5f7e66ee8a1769852002ab2abc88954648836633c531854f9b7e8370fc5aae42
```
