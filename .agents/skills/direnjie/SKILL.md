---
name: direnjie
description: "狄仁杰单 Agent 缺陷修复/需求开发状态机入口。用于 HiFox 任务、快速修 BUG、idea 确认后开发与提测；人类只有【进入开发】与【提测】两道门。收到这两类任务或用户点名狄仁杰/direnjie 时加载。"
---

# 狄仁杰工作流（项目入口）

单 Agent、高度自动化；人类确认点只有两个：

```text
【进入开发】
【提测】
```

自然语言「可以 / approved / 继续」**不能**过门。消息去掉尾部空白后，**必须以确切令牌结尾**才算确认。

进度落盘（阶段产物 + 状态），不只靠对话。参考协议：`PROTOCOL_VERSION=2026-10-27`。

## 安全语义（始终遵守）

- 阶段产物即证据：落盘产物 + 状态转移才算阶段完成
- 精确口令门：仅 `【进入开发】` / `【提测】`
- 净室评审：实现后自查；blocking findings 修补循环最多 6 轮
- 验证矩阵：修复/不受影响/边界分栏；`SIMULATED` 不得冒充真实通过
- 自主决策账本：两道门之间的决策记入 `auto_decisions`，提测时交人类追认
- 经验账本：人类纠正且认可的做法入账，跨任务不再重犯
- 指令快照：长会话/压缩后重读协议，防旧记忆跑新规则

## 阶段与必读技能

按当前阶段**强制**加载对应 skill（`$技能名` 或 Read SKILL.md）：

| 阶段 | Skill |
|---|---|
| 截图语境 / 诊断 | [direnjie-context](../direnjie-context/SKILL.md)、[lattice-diagnose](../lattice-diagnose/SKILL.md) |
| 想法精化 | [direnjie-idea](../direnjie-idea/SKILL.md)、[lattice-idea](../lattice-idea/SKILL.md)、[requirement-forge](../requirement-forge/SKILL.md)、[human-choice-eli5](../human-choice-eli5/SKILL.md) |
| 等待【进入开发】 | 停；展示 idea，不改产品代码 |
| 计划 / 实现 / 净室评审 | [direnjie-implement](../direnjie-implement/SKILL.md)、[lattice-explore](../lattice-explore/SKILL.md)、[lattice-implement](../lattice-implement/SKILL.md)、[lattice-review](../lattice-review/SKILL.md)、[code-forge](../code-forge/SKILL.md) |
| 验证 | [direnjie-verify](../direnjie-verify/SKILL.md)、[lattice-verify](../lattice-verify/SKILL.md)、[evidence-settlement](../evidence-settlement/SKILL.md) |
| 待评审 / 等待提测 / 提测 | [direnjie-deliver](../direnjie-deliver/SKILL.md)、[lattice-deliver](../lattice-deliver/SKILL.md)、[hifox-release-boundary](../hifox-release-boundary/SKILL.md)、[task-control-ledger](../task-control-ledger/SKILL.md) |

辅助（按需）：[design-blueprint](../design-blueprint/SKILL.md)、[design-taste-frontend](../design-taste-frontend/SKILL.md)、[electron](../electron/SKILL.md)、[hifox-repo-setup](../hifox-repo-setup/SKILL.md)、[locales-human-review](../locales-human-review/SKILL.md)、[github-opensource-research](../github-opensource-research/SKILL.md)。

## 典型产物

`screenshot-context.md`、`diagnosis.md`、`idea.md`、`plan.md` / `plan-delta.md`、`findings.md`、`review-report.md`、`verification/matrix.md`、`ship-summary.md`、`state.json`、`auto_decisions`。

## 与能迹既有技能的关系

| 问题类型 | 优先 |
|---|---|
| 指标/口径/语义 API | `energy-metric-change` |
| 清洗/装载/CDC/调度故障 | `energy-pipeline-debug` |
| 改完选回归范围 | `energy-change-check` |
| 需求含糊、定位入口 | `energy-task-map` |
| HiFox 式修 BUG / 带两道门的交付 | **本 skill `direnjie`** |

能迹仓库内修缺陷时：目录与存储仍遵守根目录 `AGENTS.md`；提测/分支规则以本仓库实际约定为准，HiFox 专用命令仅在任务明确要求时使用。
