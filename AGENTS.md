# 项目工作约定

## 存储

- 新项目、下载、依赖缓存、模型权重、生成产物及可配置的 Docker 存储放在 `D:\`。
- 工具无法选择 D 盘且需要写入大量数据时，先解释限制并取得许可。
- 不因当前任务移动或删除既有数据，也不修改 Windows 全局存储设置。

## 按任务使用技能

项目技能位于 `.agents/skills/`，支持自动匹配，也可用 `$技能名` 指定。
仅加载当前任务需要的技能和参考，不为小改动读取整个项目。

| 当前问题 | 技能入口 |
|---|---|
| 需求含糊、不知道改哪里、定位某个页面或数字的来源 | [energy-task-map](.agents/skills/energy-task-map/SKILL.md) |
| 改指标、统计口径、报告公式、筛选或语义 API | [energy-metric-change](.agents/skills/energy-metric-change/SKILL.md) |
| 清洗、装载、CDC、调度、数仓或告警运行失败 | [energy-pipeline-debug](.agents/skills/energy-pipeline-debug/SKILL.md) |
| 代码改完，选择相关测试及验收范围 | [energy-change-check](.agents/skills/energy-change-check/SKILL.md) |

“诊断工作台”通常指 `src/diagnostics.*`；“Agent 工作台”指 `agent-workbench/`。
根据页面、端口或错误来源消歧，不把两者混作同一服务。
