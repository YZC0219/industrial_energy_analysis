---
name: lattice-explore
description: MANDATORY on entering phase 实现探索 (IMPLEMENT_EXPLORE). Read-only product-tree coverage, findings.md and plan.md templates, plan-delta rules.
---

SKILL_GATE: 实现探索 (IMPLEMENT_EXPLORE) | PROTOCOL_VERSION=2026-10-27.1

Space skills bound in this phase: `planning-and-task-breakdown`.

# Phase capsule: 实现探索

This is a product-tree read-only phase. Only private artifacts may be written.

Normal track coverage is satisfied by either:

- at least 15 unique meaningful product-source reads; or
- complete coverage of entry points, adjacent call path, existing tests, configuration, and locale/catalog callers for the smaller blast radius.

Large track coverage is satisfied by either:

- at least 40 unique meaningful product-source reads; or
- complete coverage of the whole smaller blast radius.

Exclude generated/vendor/build/lock/minified files from counts. Every counted path needs a relevance note.

Write `.hifox-task/lattice/findings.md` with:

- intent restatement;
- module/call-path map with evidence;
- alternatives and tradeoffs;
- risks and failure modes;
- verification strategy;
- locale impact preview;
- normal: at least three edge cases discovered from code and absent from the idea;
- large: at least five such edge cases;
- any auto-decisions taken while reconciling the idea with repository reality.

Create or complete `.hifox-task/lattice/plan.md` with objective, context, approach, ordered implementation steps, affected files, tests, locale plan, and rollback. The plan executes automatically; there is no plan gate. If repository reality diverges from the approved idea, prefer the conservative adaptation and record it in `.hifox-task/lattice/plan-delta.md` (blockers, substitutions, out-of-scope discoveries) and `auto_decisions`. Only hard-limit divergence blocks for input.

Then transition directly to 实现. No product write before `【进入开发】`; after it, product writes are authorized for the approved scope.
