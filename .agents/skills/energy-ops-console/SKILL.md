---
name: energy-ops-console
description: >-
  能迹报告「操作台」视图（智能诊断嵌入）与「数据大屏」视图的入口约定。
  用于改操作台布局、嵌入诊断 iframe、诊断 embed 样式、报告同域路由 /report，
  或把模型问答接到操作台验收；纯报告长文改版用报告入口，不走本技能。
---

# 操作台 / 数据大屏

## 入口

| 落点 | 路径 |
|---|---|
| 报告模板 | `src/report_template.html` |
| 数据大屏按钮 | `#view-dash` / `data-view="dash"`（驾驶舱 KPI + 图表） |
| 操作台按钮 | `#view-ops` / `data-view="ops"`（嵌入诊断工作台） |
| 生成产物 | `output/report.html`（改模板后跑 `python src/make_report.py`） |
| 同域打开 | `http://127.0.0.1:8010/report` → 可同域嵌 `/diagnostics?embed=1` |
| 诊断页 / JS | `src/diagnostics.html`、`src/diagnostics.js`（`embed=1` 或 iframe 内加 `body.embed`） |
| 诊断 API / 问答 | `src/diagnostics.py`（`/api/v1/diagnostics*`、`/ask`） |
| 启动 | `python -m tools.start_diagnostics --mode auto`（端口 8010） |

## 行为约定

- 「数据大屏」= 原驾驶舱图表（KPI + 月度/环比/结构/车间/热力/日线/Pareto）；筛选条与报告共用。
- 「操作台」= 独立视图，只嵌诊断工作台（异常列表 / 证据问答 / 处置反馈）；不再叠在大屏下方。
- 嵌入地址：本机 HTTP(S) 用 `location.origin + '/diagnostics?embed=1'`；`file://` 回退 `http://127.0.0.1:8010/diagnostics?embed=1`。
- **GitHub Pages 无诊断 API**：`*.github.io` 上不嵌同源 `/diagnostics`（会变成站点 404），改为提示本机启动 8010。
- 模型实时分析走诊断既有链路（配置了 `LLM_*` 则在线选证据，否则证据检索）；本技能不另起一套 LLM 客户端。
- 不要改 `submission_*/` 冻结副本当生效源码。

## 验收

1. 8010 已启动；打开 `/report`，点「数据大屏」只见图表；点「操作台」见诊断 iframe（embed 紧凑样式）。
2. 筛选后大屏 KPI/图变化；操作台诊断列表仍可筛选、提问、写反馈（在线模式需有效 LLM 配置）。
3. 相关浏览器/报告体验检查可按 `energy-change-check` 选跑 `tools/verify_report_experience.py`（会点 `data-view="dash"`）。
