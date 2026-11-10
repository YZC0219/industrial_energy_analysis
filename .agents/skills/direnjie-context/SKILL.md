---
name: direnjie-context
description: MANDATORY on entering phases 截图语境/诊断 (SCREENSHOT_CONTEXT/DIAGNOSE) of the 狄仁杰 workflow. Screenshot/attachment evidence analysis and falsifiable root-cause diagnosis with diagnosis templates.
---

SKILL_GATE: 截图语境 (SCREENSHOT_CONTEXT) / 诊断 (DIAGNOSE) | PROTOCOL_VERSION=2026-10-27.1

Space skills bound in this phase: `debugging-and-error-recovery`, `doubt-driven-development`, `design-taste-frontend` (UI-adjacent interpretation).

# Phase 2 — 截图语境

If the task contains screenshots, screen recordings, red boxes, arrows, annotations, reference images, or other attachments, inspect them before forming a diagnosis.

Write `screenshot-context.md` with:

- the visible surface, UI state, and apparent user operation path;
- what each red box, arrow, label, or reference image means;
- objective visual facts;
- reporter interpretation or inferred intent;
- unresolved visual facts requiring runtime or code verification;
- the mapping from the visual symptom to candidate code state, request path, data condition, or configuration.

If a reference layout is supplied, record the relevant layout pattern without assuming the reference is automatically correct.

If there is no visual evidence, write exactly that the task has no screenshot or attachment evidence and continue with the normal evidence path. Never pretend to have seen an image.

When a response says “见截图” or otherwise relies on an image, attach the referenced image to the same HiFox message. A message that refers to an unattached screenshot is incomplete.

Use `design-taste-frontend` for UI-adjacent interpretation and browser/Electron skills only when the surface requires them. Do not change code in this phase.

# Phase 3 — 诊断

Diagnose before proposing implementation. Use `debugging-and-error-recovery` and `doubt-driven-development` when applicable.

Record in `diagnosis.md`:

- symptom and reproduction conditions;
- confirmed facts versus assumptions;
- the reporter's claimed root cause, clearly labeled as a claim;
- 2–4 mechanistically distinct candidate causes for ambiguous or user-visible problems;
- for every candidate: supporting evidence, `Confirm if`, `Eliminate if`, and observed result;
- a deliberate attack on the leading candidate, including alternate callers, state paths, flags, defaults, caches, migrations, version skew, test doubles, and recent changes;
- surviving root cause or explicit unresolved gap;
- secondary contributors;
- affected modules, callers, contracts, data paths, and likely blast radius;
- minimum verification action and repair boundary;
- complete files and runtime surfaces inspected;
- coverage floor: read at least 50 distinct relevant files (source, callers, tests, configuration, integrations, nearby modules) cumulatively across 截图语境/诊断/想法精化 before presenting the plan; if the complete relevant blast radius is demonstrably smaller, read all of it and explain why fewer is complete; repeated reads do not increase the count;
- commit-history analysis of the 10 or more most central related files over the past month: earlier fixes for the same symptom, previously abandoned approaches, known pitfalls; record under `历史风险` in `diagnosis.md` and make the plan explicitly avoid re-introducing them (if fewer than 10 are genuinely central, cover all and explain).

A single candidate is acceptable only for a genuinely direct change; explain why an alternative would be artificial. Do not accept “the screenshot says so” or the reporter's proposed fix as diagnosis.

Do not modify product code, commit, push, or 提测 in this phase. If evidence is insufficient, state the gap and ask a targeted human question instead of inventing certainty.
