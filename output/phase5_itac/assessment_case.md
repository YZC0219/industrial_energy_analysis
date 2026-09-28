# ITAC 真实工厂评估案例：DL0238

来源：[美国能源部 ITAC 官方评估页](https://itac.university/assessment/DL0238)，2024 财年。

## 工厂基线

- 产品：packaged dry food products；年产量：107,000 ton（源数据单位，未换算）。
- 年用电量：43,731,684 kWh；用电成本：$3,123,752。
- 年电耗强度：408.71 kWh/ton。这是单个年度的基线，不能由此推出同比节能。

## 建议与记录的实施状态

| 建议 | 措施 | 状态 | 年节省估计 (USD) | 投资估计 (USD) | 简单回收期 (年) |
|---|---|---|---:|---:|---:|
| DL023801 | Use / purchase optimum sized compressor | I | 236,055 | 1,860,000 | 7.88 |
| DL023802 | Upgrade controls on compressors | I | 21,276 | 7,300 | 0.34 |
| DL023803 | Eliminate or reduce compressed air usage | I | 205,595 | 34,400 | 0.17 |
| DL023804 | Repair or replace steam traps | I | 112,874 | 37,000 | 0.33 |
| DL023805 | Recover waste heat from equipment | N | 40,888 | 180,000 | 4.40 |
| DL023806 | Repair and eliminate steam leaks | I | 56,437 | 22,000 | 0.39 |
| DL023807 | Replace purchased steam with other energy source | N | 212,600 | 900,000 | 4.23 |

数据库记录 5/7 项建议已实施。
这些已实施建议的年节省估计合计 $632,237，投资估计合计 $1,960,700。

## 解释边界

- `I` 是 ITAC 数据库记录的实施状态；年节省和成本是评估方工程估计。工作簿没有实施前后连续计量序列，不能把估计额写成实测节能或因果效果。
- 该工厂的电耗强度仅与本厂同一产品和同一产量单位可比。ITAC 与 UCI 钢厂数据属于不同企业，不能按时间或设备键拼接。
- 回收期为投资估计除以年节省估计，未计融资、维护、价格变动、产能影响和税费。

来源工作簿哈希和逐项资源明细分别见 `assessment_summary.json`、`recommendations.csv`。
