---
name: energy-metric-change
description: "修改能迹项目的指标口径、报告公式、图表筛选和语义 API，追踪 SQL 到前后端的计算一致性。用于单耗、费用、碳排、环比、基线、CUSUM 或节能情景改动；纯服务启动和调度故障使用运行排障流程。"
---

# 能迹指标与报告修改

路径相对实际项目根目录；本机默认根目录为 `D:\industrial_energy_analysis`。

## 找到计算责任

按需求选择入口，不同时重写所有层：

| 改动 | 定义与消费入口 |
|---|---|
| 原始统计、系数、SQL 窗口结果 | `sql/create_table.sql`、`sql/analysis.sql`、`governance/metrics.yaml`、`governance/catalog.py` |
| 报告公式及节能情景 | `governance/analysis_formulas.yaml`、`governance/analysis_formulas.py`、`src/analysis_formulas.js`、`src/analysis_scenarios.js` |
| 报告展示与基础指标筛选 | `src/make_report.py`、`governance/analysis_report.py`、`src/report_template.html`、`src/semantic_metrics.js` |
| API/BI 消费 | `src/api.py`、`governance/consumers.py`、`governance/metabase.py` |

确认字段、单位、数据粒度、筛选作用域及零分母/缺失数据行为。实际读取相关定义；`docs/指标字典.md` 用于核对说明，不能代替代码契约。

## 保持数字可解释

- 单耗按相同统计范围的总能耗/总产量计算，不平均每日比率。`governance/catalog.py` 对跨车间产品单耗设有限制，不绕过产品单位不兼容检查。
- `sql_result` 指标不能通过筛选已导出的表来冒充重算后的窗口统计；根据契约重算或明确拒绝不支持的筛选。
- 区分零、空值和证据不足；费用、tce、tCO₂、kgce 的单位与倍数保持一致。
- 修改 SQL 时检查 `governance/metrics.yaml` 的 SQL 摘要及输出列契约。`tools/build_semantic_contract.py` 会重写契约并读取 `tests/baseline/`，只在确认 SQL/列及基线变化合理后使用，不能用刷新摘要掩盖回归。
- 公式变化时核对实际消费它的 Python 和 JavaScript；展示变化不需要重新定义公式。根据变化更新对应文档，而非批量改历史验收记录。
- 模拟节能情景、公开数据估算与真实工厂效果分别表述。筛选后基线仍应遵守公式契约。

## 验证与交付

优先使用既有相关测试，按 `energy-change-check` 的验证地图选择，不复制一套与实现同形的断言。
报告改动可从已有分析快照生成独立预览：

```powershell
python src/make_report.py --input-dir D:/industrial_energy_analysis/output --output D:/industrial_energy_analysis/tmp/metric-review/report.html
```

工作树不在默认路径时替换两处根目录。预览需要完整的分析输入；生成 HTML 不代表已验证实际筛选和浏览器计算。
有 UI 行为变化时再选择相应浏览器验证器，先读脚本的运行条件与写入位置。
版本归档入口是 `tools/report_delivery.py`；准备版本、切换当前指针和更新部署副本是不同动作，按用户已授权的范围执行。
