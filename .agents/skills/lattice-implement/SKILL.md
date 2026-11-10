---
name: lattice-implement
description: MANDATORY on entering phase 实现 (IMPLEMENT). Implementation discipline, auto-decision expansion boundaries, locales pointer.
---

SKILL_GATE: 实现 (IMPLEMENT) | PROTOCOL_VERSION=2026-10-27.1

Space skills bound in this phase: `code-forge`, `incremental-implementation`, `test-driven-development`. Locales protocol: follow `lattice-idea` (draft-then-ratify) whenever user-facing text changes.

# Phase capsule: 实现

1. Implement only the approved idea, with auto-decided adaptations recorded per the auto-decision protocol.
2. Work in small, reviewable increments.
3. Read each target before edit and migrate all affected callers.
4. Verify each increment with the narrowest meaningful runtime or targeted check.
5. Preserve repository architecture and conventions.
6. Do not push.
7. Do not silently add retries, telemetry, validation, abstractions, compatibility shims, or adjacent features not authorized by the idea.

If implementation requires public API/schema/CLI/interface changes, migrations, package/layer movement, new cross-module dependency edges, or shared-state changes beyond the approved idea:

- prefer the conservative in-scope alternative;
- if expansion is genuinely required to fix the root cause, record the exact bundle in `auto_decisions` and `plan-delta.md` with reasoning and blast radius, then proceed — unless it crosses a hard limit, in which case block for input.

For copy/locales, follow the draft-then-ratify locale protocol. Record all touched keys and locale status.

When implementation and targeted proof are complete, summarize changed files, behavior, checks, unresolved risks, locale state, and auto-decisions, persist state, and transition to 评审前变基 automatically.
