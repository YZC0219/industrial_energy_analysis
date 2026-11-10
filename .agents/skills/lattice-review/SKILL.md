---
name: lattice-review
description: MANDATORY on entering phases 评审前变基/净室评审/按报告修补 (PRE_REVIEW_REBASE/CLEAN_ROOM_REVIEW/PATCH_FROM_REPORT). Rebase protocol, clean-room review-report template, finding gate, patch cycle.
---

SKILL_GATE: 评审前变基 (PRE_REVIEW_REBASE) / 净室评审 (CLEAN_ROOM_REVIEW) / 按报告修补 (PATCH_FROM_REPORT) | PROTOCOL_VERSION=2026-10-27.1

Space skills bound in this phase: `code-review-and-quality`; `git-workflow-and-versioning` (评审前变基).

# Phase capsule: 评审前变基

After 实现, and again after every patch cycle:

1. Determine branch/upstream from repository instructions and current branch configuration. Never hardcode `origin/develop`.
2. Inspect local changes and commits.
3. Fetch and rebase with autostash only when repository policy permits.
4. Squash/reorder local commits only when safe and useful for review.
5. Stop on unexpected incoming history, ambiguous upstream, or conflicts requiring product judgment (`等待: 人工输入`).
6. Never force push and never push in this phase.

On success, transition to 净室评审.

# Phase capsule: 净室评审

Act as reviewer, not implementer. Do not trust or repeat the implementation summary. Review read-only; never patch in this phase.

Reset perspective by rereading only:

- task snapshot;
- approved idea and plan/delta;
- repository instructions;
- actual local commits and working-tree diff;
- relevant current code and tests.

Reconstruct intended behavior independently. Search for sibling paths that already implement similar behavior correctly.

Write `.hifox-task/lattice/review-report.md` with exactly these sections:

1. `Change Signal Dashboard`
   - modules changed, count and names;
   - frontend paths or none;
   - backend paths or none;
   - shared/infra/config/flags/permissions;
   - data/schema changes and breaking status;
   - API/interface changes and breaking status;
   - migrations;
   - tests added/missing;
   - locale/i18n keys changed;
   - overall risk;
   - three files a human should open first.
2. `Scope reviewed`: commits, unstaged, or both; SHAs or working tree.
3. `Plan alignment`: fulfilled items, missing items, and hard-limit questions.
4. `Strengths`: optional and brief.
5. `Findings`: worst first.
6. `Residual risks`: 1–3 uncertainties even when clean.
7. `Locale structure report`: structural validation results and drafted copy inventory.
8. `Recommended strategies`: 2–4 non-patch options for unresolved choices.
9. Final standalone `状态: 干净` 或 `状态: 有发现`.

Finding gate:

- Check idea/plan alignment, architecture, dependency direction, error handling, silent failure, security, input trust boundaries, tests, interface compatibility, migrations, performance regressions, and locale structure.
- Every finding requires location, concrete trigger, evidence, user impact, severity, strategy, and confidence.
- Report only confidence >=75.
- Critical/Important requires a concrete trigger and evidence quote/citation.
- Formatter/linter taste, naming nits, untouched pre-existing issues, and minor polish are not blocking.
- `状态: 有发现` 仅当 at least one Critical or Important issue remains.
- Minor-only or 零发现为 `状态: 干净`; list minor uncertainty under residual risks.
- Drafted copy awaiting ratification is not a code finding. Structural locale defects are.

若 `状态: 干净`，转入 验证。若 `状态: 有发现`，递增 `back_iteration` and enter 按报告修补. At six iterations, stop for human input (`等待: 人工输入`) with the accumulated reports.

# Phase capsule: 按报告修补

Patch only high-confidence Critical/Important findings in the current review report. Stay aligned with the task and approved idea. Do not push.

Auto-apply the correction whenever it does not cross a hard limit; record non-trivial corrections in `auto_decisions`. Cross a hard limit — scope expansion, unrecoverable/security-relevant tradeoffs, test-semantics changes beyond authorization — and the run blocks for input with numbered options. Minor-only items are listed but not expanded into cleanup. Out-of-scope findings remain residual/follow-up work; never sneak them into the patch.

After patching, rerun the targeted reproduction/check, list files mapped to findings, persist state, and return to 评审前变基. Never patch during 净室评审 itself.
