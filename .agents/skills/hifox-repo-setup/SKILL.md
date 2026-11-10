---
name: hifox-repo-setup
description: Use when a HiFox task needs a local repository, Web/API, Electron, CDP, local API auth, or task cleanup environment.
---

# Hifox Repo Setup

> **仓库侧环境契约（模板）**：本 skill 假设任务仓库声明了一组任务环境生命周期命令，下文以 `make agent-task-*` 目标族作为**参考实现**。你的仓库可以按同名采纳，也可以映射为自己的 target/脚本名；无论命名如何，必须保留四条契约要求：**环境清单文件**（一次 prepare 生成、同任务的串行 agent 复用）、**任务级隔离**（独立 DB/端口/运行时根）、**可停止可审计**（stop 只杀清单归属进程并输出停止报告）、**凭证不入库**（auth 状态留在任务运行时根，绝不打印/提交/附带）。

Use the assigned shared task workdir and the repository's task-local lifecycle.
For the same task in the same crew, every serial agent must reuse the same
assigned folder, such as the run's `workdir/<repo-name>`.
`hifox repo checkout` is allowed to obtain or locate that assigned workdir; it
is not permission to checkout another copy for a later crew member. The
lifecycle owns environment derivation, processes, authentication state, and
safe cleanup. An agent reports a structured failure; it does not repair a
machine, choose resources, or kill what it cannot prove it owns.

## Before starting

1. Read `AGENTS.md`, relevant repo docs, and the `Makefile` from the assigned
   workdir. Confirm the human `TASK_ID`, current branch, and `git status`.
2. Confirm the repo declares the full task lifecycle contract. Reference
   implementation (make targets):

   ```bash
   make agent-task-prepare TASK_ID=<task-key>
   make agent-task-start-web TASK_ID=<task-key>
   make agent-task-start-desktop TASK_ID=<task-key> # macOS; native-only proof
   make agent-task-stop TASK_ID=<task-key> # current task only; not ship cleanup
   make agent-task-stop --all              # mandatory at 【提测】 / SHIP
   ```

3. If the repo documents a one-time bootstrap target (reference
   implementation: `make setup-agent`), run it only when successful bootstrap
   evidence is absent. Do not repeat bootstrap for each serial task owner.
4. On macOS/Homebrew runtimes, if `brew services` shows PostgreSQL already
   started, prefer that local service over Docker fallback. Export the
   repository-documented database overrides in the same shell before every
   task lifecycle command, for example:

   ```bash
   export POSTGRES_USER=<db-user>
   export POSTGRES_PASSWORD=
   export POSTGRES_HOST=localhost
   export POSTGRES_PORT=5432
   ```

   Confirm `psql -U $POSTGRES_USER -d postgres` can connect and that user can
   create the task database (`CREATEDB` or equivalent). If permissions are
   missing, report a human/owner gate; do not repair PostgreSQL roles, switch
   to Docker, or hand-edit the task env file without authorization.
5. If a required lifecycle target is absent, report the missing target and
   follow the repository's documented path only after coordinator/human
   direction. Do not invent a worktree, env file, DB, port, config root, or
   cleanup sequence.

## Lifecycle

```bash
POSTGRES_USER=<db-user> make agent-task-prepare TASK_ID=<task-key>
```

`prepare` creates or reuses `.task-env/<normalized-task-key>/manifest.json`.
It owns the task DB; backend, frontend, browser-CDP, Electron-CDP, and daemon
ports; runtime root; and logs. Serial agents reuse this manifest.
On a Homebrew PostgreSQL runtime, keep the selected `POSTGRES_*` connection
overrides exported for `prepare`, `start-web`, `start-desktop`, and any retry.

```bash
make agent-task-start-web TASK_ID=<task-key>
```

Use Web by default for Web/API proof. It starts task-owned services and a
visible task-profile browser. Automatic local login supplies browser/API state;
after success, `runtime/auth.env` can be sourced for local seed data or API
tests. Never print, copy, commit, attach, or retain its token. A bounded
computer daemon is a separate agent/computer capability, not a prerequisite for
user login or API auth. For a simple task completely unrelated to computer or
agent capability, use `TASK_ENV_ENABLE_DAEMON=false` when starting the task.

```bash
make agent-task-start-desktop TASK_ID=<task-key>
```

Use only for a macOS native acceptance criterion: Electron shell, tray, deep
link, IPC, or desktop-only behavior. Read the manifest's desktop/CDP readiness
and attach only to its task-local Electron endpoint. Desktop automatic login
and its task-local user CLI daemon are distinct from an optional bounded
computer daemon; do not require the latter to validate login or Electron CDP.
Never use shared staging/prod Electron, `~/.hifox`, or a shared daemon/session
as a fallback.

```bash
make agent-task-stop TASK_ID=<task-key>
```

Use the `TASK_ID` form only to stop the current task env during an in-progress
retry. It does not satisfy ship cleanup.

```bash
make agent-task-stop --all
```

On a valid `【提测】`, before the ship run ends and also on a partial ship,
this command is mandatory. `--all` stops every agent-task env, including envs
from other tasks that a `TASK_ID` stop would leave running. Record the command,
exit status, and stop report. The stop target sends `SIGTERM` only to
manifest-proven process groups, then removes ephemeral task runtime, numbered
CLI config, Electron user-data, and `runtime/auth.env`. It deliberately retains
the DB, task env file, manifest, logs, screenshots, and stop report for retest.
Report `logs/stop-report.json`; if it is `stop-refused`, do not claim the env
is stopped. If the Makefile has no `--all` target, say so; do not invent
`pkill` or `kill -9`.

## Non-negotiable boundaries

- Do not create per-agent Git worktrees, per-agent env files, or additional
  checkout folders for another member of the same crew/task.
- Do not hand-edit the task env file, choose a DB/port, create a runtime/config
  root, extract browser cookies, create a PAT, or repair conflicts manually.
- Do not use `make stop`, `pkill`, `kill -9`, port-wide cleanup, global daemon
  cleanup, destructive removal under `~/.hifox`, shared desktop-user-data
  deletion, or production daemon/session changes.
- Do not print secrets or include credentials, cookies, JWTs, installation
  tokens, authorization headers, or generated env values in comments, logs,
  artifacts, screenshots, or commits.

For any lifecycle failure, report the command, normalized task key, redacted
output, manifest-relative path, capability state, and relevant log or stop
report. Return the exception to the coordinator/human owner; do not turn it
into a local repair task.
