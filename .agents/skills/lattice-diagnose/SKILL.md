---
name: lattice-diagnose
description: MANDATORY on entering phase 诊断 (DIAGNOSE) of the lattice-bug-fix workflow. Root-cause diagnosis coverage, method, and diagnosis.md template.
---

SKILL_GATE: 诊断 (DIAGNOSE) | PROTOCOL_VERSION=2026-10-27.1

Space skills bound in this phase: `debugging-and-error-recovery`, `doubt-driven-development`.

# Phase capsule: 诊断

Goal: establish a falsifiable root cause. No product code changes and no locale finalization.

Coverage:

- Read at least 50 distinct relevant files across source, callers, tests, configuration, flags, caches, integrations, and nearby modules.
- If the complete relevant blast radius is demonstrably smaller, read all of it and explain why fewer than 50 files is complete.
- Repeated reads do not increase the count. Directory listings do not count.
- This floor is cumulative with the reading done while drafting and refining the idea: by the time the idea is presented for `【进入开发】`, total distinct relevant files read must be ≥50.

Method:

1. Record symptoms and available reproduction/runtime/log evidence.
2. Separate confirmed facts from assumptions.
3. Form 2–4 mechanistically distinct hypotheses from different layers.
4. For each hypothesis record `Confirm if` and `Eliminate if` observations.
5. Deliberately try to disprove the leading hypothesis using unopened paths, alternate callers, flags, defaults, version skew, stale caches, and test doubles.
6. If eliminated, promote the next hypothesis and repeat.
7. Anchor conclusions to current symbols, paths/lines, call/data flow, configuration keys, tests, and Git history.
8. Analyze the commit history of the 10 or more most central related files over the past month (`git log` per file): earlier fixes for the same symptom, previously attempted and abandoned approaches, and known pitfalls. Record findings in a `历史风险` section of `diagnosis.md`; the plan must explicitly avoid re-introducing them. If fewer than 10 files are genuinely central, cover all of them and explain.
9. Seed likely blast radius, candidate edge cases, locale/copy surface, and verification direction without patching.

Write `.hifox-task/lattice/diagnosis.md` with evidence, explored-path count, and `历史风险` findings. If no hypothesis survives, record explicit gaps and stop blocked (`等待: 人工输入`) rather than inventing certainty. When complete, transition automatically to 起草想法.
