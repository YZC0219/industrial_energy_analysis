---
name: direnjie-idea
description: MANDATORY on entering phase 想法精化 (IDEA_REFINEMENT). Idea artifact template, scope classification (CORE_FIX/REQUIRED_SUPPORT/ARCHITECTURE_FOLLOW_UP), human choice tables.
---

SKILL_GATE: 想法精化 (IDEA_REFINEMENT) | PROTOCOL_VERSION=2026-10-27.1

Space skills bound in this phase: `idea-refine`, `requirement-forge`, `design-blueprint`, `human-choice-eli5` with `eli5`.

# Phase 4 — 想法精化

Use `idea-refine`, `requirement-forge`, `design-blueprint`, `human-choice-eli5`, and `eli5` when they fit the task.

Write `idea.md` with:

- the user's actual problem and desired outcome;
- the recommended direction and why it is preferable;
- acceptance behavior and observable evidence;
- in-scope files/modules/surfaces;
- explicit non-goals and `Not Doing`;
- risks, compatibility concerns, and rollback boundary;
- open product or engineering decisions;
- the diagnosis evidence that the idea relies on;
- expected verification and success criteria.

Every idea must classify scope explicitly:

- `CORE_FIX` — the smallest change that directly satisfies the current task.
- `REQUIRED_SUPPORT` — work required for the core fix to be safe, compatible, or verifiable.
- `ARCHITECTURE_FOLLOW_UP` — a useful structural improvement that can be separated without making the core fix unsafe.

Default implementation scope is `CORE_FIX` plus only the necessary `REQUIRED_SUPPORT`. Present architecture follow-up separately. Any API, schema, migration, cross-module contract, or repository-workflow expansion must be named in the human choice table; never add it silently and never treat it as required merely because it is cleaner.

For product-shaped work or multiple plausible directions, present 2–5 categorized options in a compact Chinese `## Q<n>` table. Do not silently choose a product decision that belongs to the human. Technical choices that are fully determined by repository rules may be made autonomously and recorded in `auto_decisions`.

The idea must be actionable, not a restatement of the task. It must say what will not be built.
