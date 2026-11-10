---
name: lattice-verify
description: MANDATORY on entering phase 验证 (VERIFY). Real-surface verification rules, evidence storage, verification flags, work-branch push.
---

SKILL_GATE: 验证 (VERIFY) | PROTOCOL_VERSION=2026-10-27.1

Space skills bound in this phase: `browser-testing-with-devtools` (web), `electron` (desktop), `evidence-settlement`.

# Phase capsule: 验证

Verification is mandatory and uses the actual changed surface. Do not commit or push.

1. Read repository verification/environment documentation first.
2. Exercise the changed behavior, not merely a test file.
3. For web UI, use a real browser and capture screenshots plus relevant console/network evidence.
4. For desktop UI, run the actual application and capture screenshots or recording plus logs.
5. If both surfaces materially change, cover both.
6. Backend/CLI-only work may use requests, command output, logs, traces, database/query evidence, or another direct runtime observation when screenshots would be meaningless.
7. Run targeted repository tests/checks that defend the changed behavior.
8. Run repository-declared 提测前检查 required before commit, except checks explicitly reserved for a managed commit hook.
9. If environment/dependencies are missing, use repository setup instructions and continue; do not bypass checks.
10. Store evidence and a manifest under `.hifox-task/lattice/verification/` and `evidence/`.

Set:

- `verification.checks_passed=true` only when required checks exit successfully;
- `verification.evidence_ok=true` only when evidence directly demonstrates the changed path.

A functional or structural failure returns to 按报告修补 and consumes a Back iteration. When verification passes, push the work branch to its dedicated remote branch per repository policy (never force, never merge, never the policy base branch) — mandatory, so a `<base>...<branch>` compare link exists before any 提测动作 — capture the links per the Links contract, and transition to 等待提测.
