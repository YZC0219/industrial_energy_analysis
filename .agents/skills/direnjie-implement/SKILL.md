---
name: direnjie-implement
description: MANDATORY on entering phases 计划/实现 (PLAN/IMPLEMENT) after 【进入开发】. Planning discipline, implementation scope rules, clean-room review.
---

SKILL_GATE: 计划 (PLAN) / 实现 (IMPLEMENT) / 净室评审 (CLEAN_ROOM_REVIEW) | PROTOCOL_VERSION=2026-10-27.1

Space skills bound in this phase: `planning-and-task-breakdown`, `incremental-implementation`, `code-forge`, `test-driven-development`, `code-review-and-quality`.

# 计划与实现（【进入开发】后）

After valid `【进入开发】`:

1. Re-read the approved `idea.md`, current task snapshot, repository instructions, and all evidence artifacts.
2. Use `planning-and-task-breakdown` and `incremental-implementation` for non-trivial changes.
3. Use `code-forge` and `test-driven-development` when changing behavior.
4. Keep implementation inside the approved scope. If a new product choice or scope expansion appears, stop and return to the human with a compact decision table.
5. Use repository-declared commands only. Do not import another repository's or workflow's commands unless the current repository declares them.
6. Record material autonomous choices in `auto_decisions` with alternatives, reasoning, and affected files.

# 净室评审

Use `code-review-and-quality` after implementation. Review the final diff as a fresh reader against:

- the approved idea and diagnosis;
- repository architecture and dependency rules;
- security, correctness, error handling, and compatibility;
- tests and likely consumer-visible regressions;
- the screenshot/reference behavior when applicable.

Fix blocking findings before verification. Never claim a clean review without reading the diff and evidence.
