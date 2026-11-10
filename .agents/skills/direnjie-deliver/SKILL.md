---
name: direnjie-deliver
description: MANDATORY on entering phases 待评审/等待提测/提测执行 (REVIEW_READY/WAIT_CONFIRM_SHIP/SHIP). Reviewer packet, 提测前检查清单, links block and 测试套件 templates, gate-observed rules, ordered 提测事务 + 事务表, delivery contract.
---

SKILL_GATE: 待评审 (REVIEW_READY) / 等待提测 (WAIT_CONFIRM_SHIP) / 提测执行 (SHIP) | PROTOCOL_VERSION=2026-10-27.2

Space skills bound in this phase: `git-workflow-and-versioning`, `hifox-release-boundary`, `提测-and-launch`, `evidence-settlement`.

# Phase — 待评审

After implementation, clean-room review, and the available verification work are complete:

1. Write the reviewer packet and `ship-summary.md`.
2. Use the assigned dedicated Agent branch; never create a second task branch.
3. Commit and push the dedicated review branch so the reviewer can inspect the diff before `【提测】`.
4. Generate and include the compare link against the repository's authoritative integration branch.
5. Record `分支提交`, `对比`, `主干提交`, `主干已含修复`, `任务状态`, and `任务评论` separately.
6. A review-branch push 不算提测, does not change task state, and does not satisfy `主干已含修复`.
7. Continue automatically to `等待提测`; do not add a review-approval token.

If real environment evidence is unavailable, 置评审矩阵状态 `REVIEWABLE_WITH_LIMITATION`，保留 `REAL_ENV_CDP=BLOCKED`, and keep `可提测=false`. Reviewer access is allowed; automatic 提测 is not.

# 提测前检查清单（待评审 → 等待提测）

Run the named checks below when composing the 提测包. Each check is a command plus an expected observation. Record each result (✅ / ✘ / BLOCKED / N/A+证据) in `ship-summary.md`.

- `rebase-基线`：fetch 策略基线分支；期望：工作分支位于最新基线之上，无发散。
- `agent-task-实测`：读 `verification/matrix.md`；期望：`REAL_ENV_CDP=PASS` 且至少一条不受影响的正常路径 `PASS`。
- `截图齐全`：对照回复声明清点附件；期望：每条用户可见声明都有截图附在同一消息气泡。
- `字段回填`：`hifox task edit <task-id> --field 需求细节=… --field 实现结果=… --field 测试要点=…`；期望：`hifox task view <task-id>` 显示更新后的字段。字段不存在则记 `N/A` 并附 task view 输出为证据。
- `链接块完整`：构造链接块；期望：`对比`/`分支`/`分支提交` 均为观测值（或给出确切恢复动作），无臆造 URL。

结果契约（退出码式）：五项全 ✅ → 组装并呈现提测包；任一 ✘ 或 BLOCKED → 置 `状态=阻塞`，点名失败检查项，给出确切恢复动作；任何 `N/A` 必须附证据——置信度是由证据支撑的主张，不是感觉。

When implementation and verification are complete, write `ship-summary.md` containing:

- approved idea and scope;
- changed files and behavior;
- tests/checks and real-surface evidence;
- residual risks and known limitations;
- `对比:`、`分支:`、`分支提交:`、`主干提交:`、`环境:` 链接或显式不可用状态；
- 测试套件块（环境/账号/测试数据/截图清单/视频/残余风险，模板见下）。

The pre-提测 links block must be explicit and must never contain invented URLs. In replies, emit it as plain markdown text lines in the form `标签: [标题](URL)`（或 `标签: BLOCKED/N/A + 原因`）——never wrapped in a code block:

```text
任务: <current HiFox task URL or BLOCKED>
环境: <confirmed task-environment URL or BLOCKED/N/A with reason>
对比: <dedicated-branch compare URL or BLOCKED>
分支: <dedicated branch URL or BLOCKED>
分支提交: <review branch commit URL/SHA or BLOCKED>
证据: <verification artifact paths and attached evidence>
测试: <commands, exit codes, and reports>
主干提交: PENDING
主干已含修复: false
任务状态: PENDING
任务评论: PENDING
残余风险: <explicit list or NONE>
```

测试套件（提测包必填块，中文标签；每行要么是观测值，要么是显式不可用状态+原因，不得省略键）：

```text
环境: <任务环境 URL 或 N/A>
账号: <测试账号与入口 或 N/A>
测试数据: <预置数据/账号/操作路径 或 NONE>
截图清单: <已交付截图的主题列表>
视频: <录制文件路径 或 NONE>
残余风险: <明确列出 或 NONE>
```

At `等待提测`, `对比`, `分支`, and `分支提交` must be observed; if any is unavailable, remain blocked before the human 提测门 and give the exact recovery action. `环境` may be `N/A` for non-user-visible work or `BLOCKED` with a reviewer-visible limitation. `主干提交`, `主干已含修复`, `任务状态`, and `任务评论` remain `PENDING` until the human gate authorizes 提测. They must become observed final values before reporting `已提测`.

# 提测执行

After valid human `【提测】`:

- use `git-workflow-and-versioning`, `hifox-release-boundary`, and `提测-and-launch` as required;
- verify the approved commit, branch, compare link, and environment evidence;
- perform only the current task's authorized 提测变更;
- publish the tester-facing evidence required by the repository and HiFox task workflow;
- record the final result in `ship-summary.md` and state.

A failed push, task update, or release-boundary check is a visible partial state, not success. Do not retry blindly or mark `完成` without current evidence.

提测 is an ordered transaction, not merely a successful push:

1. Resolve the repository's authoritative integration branch from repository-owned instructions. If that branch is `develop`, publish the approved change to `develop`; never stop at a review branch.
2. Re-read the current task and approved artifacts, check the worktree and private-artifact exclusion, and synchronize with the latest integration branch using the repository policy.
3. Run the required pre-commit checks, commit through the repository-managed path, and publish without force.
4. Verify the remote integration branch contains the exact 已提测 commit using repository-appropriate evidence. Do not rely on a local branch name or an unverified push response.
5. Update only the current HiFox task to the repository/team-configured 提测后 status, normally `待测试` when that is the declared status.
6. Comment on the current task with before/after behavior, final integration commit, checks, evidence, environment, residual risks, and the final links block.
7. Record every step and result in `state.json` and `ship-summary.md`.
8. Before the 提测 run ends, 包括 `部分完成`，运行 `make agent-task-stop --all` when the repository Makefile provides it. This stops every agent-task env, 不止当前 `任务ID`. 仅按 `任务ID` 限定作用域的 stop 不满足本步. Record the command, exit status, and stop report. If the target is absent, say so; do not invent `pkill` or `kill -9`. Only after commit/publish, remote verification, task-status update, task comment, and this stop command all succeed may 最终状态方可为 `已提测`/`完成`。跳过 stop 则阻塞 `完成`。

If any step fails, 报告 `部分完成` 或 `阻塞`, name completed and incomplete mutations, preserve recovery evidence, and give the exact next action. Never claim that a code push alone completed 提测.

提测后事务表——每步完成即在 `state.json` 的 `history` 记一行（动作、观测、结果）；没有观测的行不算完成：

| # | 动作 | 期望观测 |
|---|---|---|
| 1 | 解析仓库权威集成分支 | 集成分支名来自仓库指令，非硬编码 |
| 2 | 复核任务/阶段产物并同步集成分支 | 无未批准 diff；私有阶段产物已排除 |
| 3 | 预提交检查 + 仓库托管提交 | 检查退出码 0；提交 SHA 已记录 |
| 4 | 发布到集成分支（不 force） | 远端集成分支包含已发布提交 |
| 5 | 远端验证 | ref 检查确认远端包含确切 SHA |
| 6 | HiFox 任务转待测试 | task view 显示 待测试 |
| 7 | 任务评论（前后行为/证据/链接块） | 评论可见且附件齐全 |
| 8 | `make agent-task-stop --all` | 命令、退出状态、停止报告已记录 |

# 交付与链接（回复契约摘录，全局生效）

- 凡回复报告评审分支推送、compare、提交或已启用任务环境，必须包含链接块（普通 markdown 文本行 `标签: [标题](URL)`，禁 code block 包裹）；`对比`/`分支`/`分支提交` 在 `等待提测` 前必须为观测值；提测后追加 `主干提交` 与 `已合并`。链接块是自动证据交付，不是人类门。
- 截图交付：正文与图片同一气泡（`hifox chat message send <session-id> --body-file <file> --attachment <shot>`），禁止断开/后台/拆分；声称已发送附件必须出示发送命令的观测结果；“稍后发送/后台发送”不算交付。
