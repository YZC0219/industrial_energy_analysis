---
name: direnjie-verify
description: MANDATORY on entering phase 验证 (VERIFY). Real-surface verification matrix, CDP rules, evidence classification, retry circuit breaker.
---

SKILL_GATE: 验证 (VERIFY) | PROTOCOL_VERSION=2026-10-27.1

Space skills bound in this phase: `browser-testing-with-devtools`, `electron`, `evidence-settlement`, `observability-and-instrumentation`.

# 验证

Verification must exercise the changed surface, not only compile or unit-test wiring.

- Run targeted tests and repository-declared checks.
- For web behavior, use `browser-testing-with-devtools` and inspect the actual DOM, console, network, and visual result when applicable.
- For Electron behavior, use `electron` and capture real-surface evidence when applicable.
- For screenshot tasks, compare the resulting surface against the reported screenshot facts and attach relevant evidence.
- Use `evidence-settlement` to classify each claim as observed, inferred, blocked, or not applicable.
- Use `observability-and-instrumentation` only when the change actually needs runtime visibility.
- Read task environment manifests for confirmed experience links; never invent a LAN URL or fall back to localhost for cross-device reporting.
- If a check fails, use `debugging-and-error-recovery`, fix the root cause, and rerun the relevant check.
- A failed check must not be retried the same way. Before the second retry of the same failure, change the method (different entry point, instrument, fixture, or reproduction) and record the attempt number and the method change in `verification/matrix.md` or `findings.md`; a third identical retry is prohibited.
- Record commands, outputs, screenshots, URLs, and limitations in `verification/` and `findings.md`.

`验证` is a hard evidence gate, not a prose report. Before running checks, write `verification/matrix.md` with at least:

```text
FIXED_BEHAVIOR: PASS | FAIL | BLOCKED
NORMAL_UNAFFECTED_BEHAVIOR: PASS | FAIL | BLOCKED | SIMULATED
BOUNDARY_OR_ERROR_BEHAVIOR: PASS | FAIL | NOT_RUN
REAL_ENV_CDP: PASS | BLOCKED | N/A
MECHANICAL_CHECKS: PASS | FAIL
可提测: true | false
LIMITATIONS: <NONE or explicit limitations>
```

Verification rules:

- For every task with a web, Electron, or other user-visible runtime surface, attempt to start or reuse the task-specific `agent-task env` and exercise the changed surface through CDP. This is required, not optional.
- Exercise both the requested/fixed path and at least one relevant normal path that must remain unaffected. Include boundary or error behavior when the task claims it.
- Capture the observed DOM or desktop state, console/network evidence when relevant, screenshots, recordings, logs, URLs, commands, exit codes, and artifact paths.
- If the task environment cannot start, exercise the unaffected normal path with the best available simulation and mark it `SIMULATED`; simulation never upgrades `REAL_ENV_CDP` and never counts as real-surface PASS. The task remains `REVIEWABLE_WITH_LIMITATION`, with `可提测=false`.
- A failed fixed path or failed required normal path is `VERIFY_BLOCKED`; do not present it as review-ready. Missing real-surface evidence may still be presented for reviewer inspection only when the changed code and all available evidence are clearly marked `BLOCKED`.
- For a task with no user-visible runtime surface, state why CDP is `N/A`, set `可提测=true` only when the repository-declared runtime or integration check observes the changed contract, and record the justification.
- Use `evidence-settlement` to classify every acceptance claim as `OBSERVED`, `INFERRED`, `SIMULATED`, `BLOCKED`, or `NOT_APPLICABLE`.
- Do not claim real verification PASS for `SIMULATED` or `BLOCKED` evidence. `【提测】` is the only human risk-acceptance and release token; if it is used with a visible limitation, preserve the limitation in the final packet and comment.

Never claim a check, screenshot, runtime behavior, or link that was not observed.
