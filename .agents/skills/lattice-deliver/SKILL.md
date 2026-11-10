---
name: lattice-deliver
description: MANDATORY on entering phases 等待提测/提测执行 (WAIT_CONFIRM_SHIP/SHIP) and whenever a reply reports a push or env-backed verification. 提测前检查清单, 提测包 + 测试套件 block, links contract, ordered 提测事务 + 事务表, screenshot single-bubble delivery.
---

SKILL_GATE: 等待提测 (WAIT_CONFIRM_SHIP) / 提测执行 (SHIP) | PROTOCOL_VERSION=2026-10-27.1

Space skills bound in this phase: `hifox-release-boundary` with `提测-and-launch` (before commit/push), `evidence-settlement` (packet), `git-workflow-and-versioning` (提测执行).

# 提测前检查清单（验证 → 等待提测）

Run the named checks below when composing the 提测包. Each check is a command plus an expected observation. Record each result (✅ / ✘ / BLOCKED / N/A+证据) in `ship-summary.md`.

- `rebase-基线`：fetch 策略基线分支；期望：工作分支位于最新基线之上（或冲突已按策略停下询问），无发散。
- `agent-task-实测`：读 `state.verification`；期望：`checks_passed=true` 且 `evidence_ok=true`，实测覆盖变更路径与至少一条不受影响的正常路径。
- `截图齐全`：对照回复声明清点附件；期望：每条用户可见声明都有截图附在同一消息气泡。
- `字段回填`：`hifox task edit <task-id> --field 需求细节=… --field 实现结果=… --field 测试要点=…`；期望：`hifox task view <task-id>` 显示更新后的字段。字段不存在则记 `N/A` 并附 task view 输出为证据。
- `链接块完整`：构造链接块；期望：`对比`/`分支`/`提交` 均为观测值（或给出确切恢复动作），无臆造 URL。

结果契约（退出码式）：五项全 ✅ → 组装并呈现提测包；任一 ✘ 或 BLOCKED → 置 `状态=阻塞`，点名失败检查项，给出确切恢复动作；任何 `N/A` 必须附证据——置信度是由证据支撑的主张，不是感觉。

# Phase capsule: 等待提测

Compose `.hifox-task/lattice/ship-summary.md` as the 提测包. The 提测包 is the human review surface: presenting it IS the review request, and a valid `【提测】` means the human has finished reviewing everything in it. Include:

- task and approved scope;
- the diff under review (commits and working tree) and final diff/commit plan, with remote links per the Links contract — the `对比:` link of the dedicated work branch against the policy base branch is mandatory here (push the branch first if needed), plus compare/branch/commit URLs even if the push happened earlier, plus the `环境:` line while the agent-task env is still running;
- review verdict and residual risks;
- verification commands/results;
- evidence paths;
- locale/copy decision table: every wording decision, its five candidates, and the selected candidate awaiting ratification;
- auto-decision table: every autonomous decision with question, options, choice, reasoning, affected files;
- the three files a human should open first;
- target branch/upstream from repository policy and the base-branch landing plan (merge or platform merge request to the policy base branch — 提测落地在那里, not on the agent branch);
- exact task-side actions: evidence comment/attachments and transition to 待测试;
- 测试套件块（环境/账号/测试数据/截图清单/视频/残余风险），模板：

```text
环境: <任务环境 URL 或 N/A>
账号: <测试账号与入口 或 N/A>
测试数据: <预置数据/账号/操作路径 或 NONE>
截图清单: <已交付截图的主题列表>
视频: <录制文件路径 或 NONE>
残余风险: <明确列出 或 NONE>
```

每行要么是观测值，要么是显式不可用状态+原因，不得省略键。

Revalidate that the review remains `干净`, verification passed, evidence is valid, no unapproved diff appeared, and private artifacts are excluded. Then set `waiting_for=【提测】` and stop.

If the human's response does not end with `【提测】` after trimming trailing whitespace:

- naming a different copy candidate or providing revised copy → apply verbatim, re-verify the affected surface, update artifacts, re-present the packet;
- rejecting an auto-decision → record the reversal as a new human-settled decision, implement the correction, re-run affected verification and review as needed, re-present the packet;
- requesting a change or reporting a defect → treat it like a review finding: fix it within scope (structural expansion still follows the hard-limit rules), re-verify, re-review, re-present the packet;
- other feedback → revise the packet or the work accordingly and re-present.

Any work change after presentation requires a re-presented packet before `【提测】` is valid again.

# Phase capsule: 提测执行

Run only after valid `【提测】`.

1. Re-read state and verify the approved diff hash still matches. New changes invalidate 提测批准 and require a re-presented packet.
2. Fetch/rebase according to repository policy; stop on ambiguity or conflict.
3. Run final required checks.
4. Commit through repository-managed hooks. If setup is missing, run repository setup; never skip hooks.
5. Ensure working tree is clean except ignored private artifacts.
6. Push the work branch to the policy-approved remote/upstream. Never force push. Then LAND the change on the policy base branch (commonly `develop` in the task repository; resolve it from repository policy and branch configuration — never hardcode): fetch the latest base branch, merge 已提测提交 onto it (or rebase, per policy), rerun the final required checks on the merged result, and push the base branch (never force). If repository policy forbids direct base-branch pushes, create the platform merge request AND complete the merge through it as part of 提测执行. 提测意味着 the change ends up on the base branch — a change that exists only on the agent branch is 未提测. If the platform genuinely requires a human merge click, stop, set `提测部分完成`, and hand over the merge link; never claim delivery.
7. Verify publication by ref inspection: the remote base branch contains 已提测提交 (`git fetch` + `git merge-base --is-ancestor <提测-sha> <remote-base>`, or equivalent `ls-remote` evidence), local HEAD matches the pushed ref, and report the landed base-branch SHA and its link.
8. Finalize `.hifox-task/lattice/ship-summary.md` with before/after, verification, evidence, commit link/SHA, branch, the landed base-branch SHA/link, residual risks, locale verdict, and the ratified auto-decision table.
9. Add a tester-facing comment to the current task using `hifox task comment <task-id> --body-file <提测-summary-file>` and attach ALL accumulated evidence (every screenshot and recording under `evidence/` and `verification/`), not a selection, with repeated `--attachment <path>` arguments.
10. Transition only the current task to 待测试 using `hifox task edit <task-id> --status 待测试 --output json`.
11. Parse and verify CLI JSON responses identify the same task and expected status. A successful process exit without matching response data is not success.
12. Before the 提测 run ends, including `提测部分完成`, run `make agent-task-stop --all`. This stops every agent-task env, not only the current task. `make agent-task-stop` 仅按 `任务ID` 限定作用域的调用 does not satisfy this step. Record the command, exit status, and stop report. If the Makefile has no such target, say so; do not invent `pkill`, `kill -9`, or another repository's stop command. Do not mark 完成 while this command was skipped.
13. Record commit, remote ref, comment result, attachment result, task status result, and completion time.
14. Mark 完成 only after the change is landed on the remote policy base branch and the task transition both succeed. An agent-branch-only push is never 完成.

If the base-branch landing, comment, or status update fails after the work-branch push, set `提测部分完成`; do not amend or repush. Still run step 12 before ending the run. On resume, verify the recorded remote commit, then complete only the missing action (base-branch merge, comment, status update, or env stop).

提测后事务表——每步完成即在 `state.json` 的 `history` 记一行（动作、观测、结果）；没有观测的行不算完成：

| # | 动作 | 期望观测 |
|---|---|---|
| 1-2 | 复核状态与审批 diff 哈希、按策略 fetch/rebase | 哈希不变；无冲突或已停下询问 |
| 3-5 | 最终检查、托管提交、工作区清洁 | 检查退出码 0；提交 SHA 已记录 |
| 6 | 推送工作分支并落到策略基线分支（不 force） | 远端基线分支包含已发布提交 |
| 7 | 远端验证 | ref 检查确认远端包含确切 SHA |
| 8 | 定稿 `ship-summary.md` | 前后行为/证据/落点 SHA/残余风险齐全 |
| 9 | 任务评论 + 全量附件 | 评论可见且附件齐全 |
| 10 | 任务转待测试 | task view 显示 待测试 |
| 11 | CLI JSON 校验 | 响应 task/status 与预期一致 |
| 12 | `make agent-task-stop --all` | 命令、退出状态、停止报告已记录 |
| 13-14 | 记录与完成标记 | 全部观测落账后才可标 `完成` |

# Links contract

Whenever the run publishes or references a remote branch or commit — the review-branch push, the 提测推送, or any ad-hoc push the human explicitly requests — the same response MUST include the links block. Never make the human ask for them.

Derive links from the repository's actual remote host. For CNB remotes (`cnb.cool`):

- compare/PR link: `https://cnb.cool/<org>/<repo>/-/compare/<base>...<branch>` — `<base>` is the policy base branch from repository configuration, never hardcoded;
- branch tree link: `https://cnb.cool/<org>/<repo>/-/tree/<branch>`;
- commit link: `https://cnb.cool/<org>/<repo>/-/commit/<sha>`.

For other hosts (GitHub, GitLab, self-hosted), construct the host's equivalent compare/branch/commit URLs from the remote URL; if no web URL can be constructed, print the remote URL, branch name, and commit SHA as plain text instead of omitting them.

Emit the links block in the response body — not only in the footer or artifacts — in every response that reports a push or env-backed verification, and in the 提测包 even when the push happened in an earlier response. In replies, emit it as plain markdown text lines in the form `标签: [标题](URL)`（或 `标签: BLOCKED/N/A + 原因`）——never wrapped in a code block:

```text
链接:
对比: <compare/PR URL>
分支:  <branch tree URL>
提交:  <head commit URL or remote+SHA>
环境:  <agent-task env LAN URL, e.g. http://192.0.2.10:31024>
```

The links block appears in addition to the footer, not inside it. A response that reports a push without the links block is incomplete. When the change lands on the policy base branch, add a `已合并:` line with the base-branch commit URL or base ref + SHA. Also record the links in `.hifox-task/lattice/ship-summary.md` and the tester-facing task comment.

`对比:` is mandatory before 提测: from 验证 completion onward, a dedicated work branch (one per task, e.g. `<org-agent-ns>/<agent>/<task-id>` or the repository's declared convention) must be pushed and its `<base>...<branch>` compare link included in every review-facing response. Never present the 提测包 or run any 提测动作 without a working compare link — if the branch is not yet remote, push it to its dedicated remote branch first (that push is authorized). The compare base is always the policy base branch, never the agent branch itself.

The `环境:` line is required whenever the reply reports verification that ran against an enabled agent-task env (the same trigger as the screenshot rule), and in the 提测包 while the env is still running; it may be the block's only line. Omit it when no task env is running. Derive it as `http://<LAN-IPv4>:<FRONTEND_PORT>`: `FRONTEND_PORT` from `.env.worktree` or `.task-env/<task-id>/.env.task` (the repository's authoritative sources), the LAN IPv4 from the primary network interface (`ipconfig getifaddr en0` on macOS, `ip route get` or `hostname -I` on Linux). Several candidate IPs: list them all. No LAN IP: say so and fall back to `http://127.0.0.1:<FRONTEND_PORT>`. Around the line, note that the env runs until 提测清理 and the link dies afterwards, and that the dev login page offers devbot one-click login.

# Screenshot delivery

A reply that claims user-visible verification must SHOW the screenshots in the same reply — evidence paths alone are not delivery. The ACP text stream cannot carry images, so deliver the body and its images as ONE bubble via `hifox chat message send`:

1. Write the complete reply body to a file.
2. Send one bubble: `hifox chat message send <session-id> --body-file <file> --attachment <shot-1> --attachment <shot-2> ...` — `<session-id>` is the chat session this run is bound to (confirm with `hifox chat session view <id>` if unsure).
3. The ACP reply for this turn is a one-line summary pointing at the bubble (e.g. `验证完成，完整图文见同消息`) plus the LATTICE footer. The bubble carries the full body.

Rules:

- Curate: attach only the screenshots that back THIS reply's claims (typically 3–8), each captioned in the body with the claim it proves. The full set stays under `evidence/`/`verification/`.
- If the body mentions a screenshot ("已截图/见截图/如图"), that image must be attached to the same bubble. A reply referencing unattached screenshots is incomplete.
- Delivery is foreground and single-bubble: never detach, never run the send in the background, never split body and attachments across bubbles; “稍后发送/后台发送” is not delivery.
- When the bubble reports verification that ran against an enabled agent-task env, its body also carries the `环境:` line per the Links contract.
- Backend-only or text-only replies carry no attachments.
- If `hifox chat message send` fails, paste the full body into the ACP reply, state that attachments failed to deliver, and retry before moving on.
- 提测评论 is the exception to curation: attach ALL accumulated evidence (every screenshot and recording under `evidence/` and `verification/`) with repeated `--attachment` flags.
