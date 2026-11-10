---
name: lattice-idea
description: MANDATORY on entering phases 起草想法/精化想法 (DRAFT_IDEA/REFINE_IDEA) and whenever presenting Q decision tables or drafting user-facing copy. Idea artifacts, remaining-decision protocol, auto-decision protocol, locales draft-then-ratify protocol, and Q-table formatting.
---

SKILL_GATE: 起草想法 (DRAFT_IDEA) / 精化想法 (REFINE_IDEA) | PROTOCOL_VERSION=2026-10-27.1

Space skills bound in this phase: `idea-refine`, `requirement-forge`, `human-choice-eli5` with `eli5`, `locales-human-review` (copy work), `planning-and-task-breakdown` (large track).

# Decision handling in an automated workflow

## During idea composition (before `【进入开发】`)

The idea gate is the single design decision point. Follow the remaining-decision protocol:

1. Do not ask the human for information available in the task, repository, Git history, runtime, configuration, tests, or documentation. Explore first.
2. For every genuinely undecided item, explain it in plain language, state why it matters and what a wrong choice costs, list likely files/modules per option, and present 3–5 meaningfully different numbered options, including a conservative option when applicable. Present options as `## Q<n>` table blocks per `Chat reply formatting`.
3. At most one option may be marked recommended with one sentence of reasoning. Advice is not a decision.
4. Never pre-pick. Tell the human to answer with letter pairs (e.g. `1A 2B`) and end the reply with the exact token `【进入开发】`. `1A 2B 【进入开发】` is valid, and so is the token on its own line. Do not ask for a blank line or a resend. Record every selection in `idea.md` and the ledger.

## Auto-decision protocol (after `【进入开发】`, before `【提测】`)

Once the idea is approved, do not stop for design choices. For any decision the approved idea does not settle:

1. Enumerate the realistic options at the point of discovery.
2. Choose the conservative option: smallest blast radius, no public API/schema/migration expansion, no behavior beyond the approved idea, best conformance to repository conventions. If the idea explicitly prescribes a direction, follow it.
3. Record the decision in `auto_decisions` with question, options, choice, reasoning, and affected files; surface material ones in `findings.md` or `plan-delta.md` as appropriate.
4. Continue. The 提测包 presents every auto-decision for ratification at `【提测】`.

Hard limits — these are NOT auto-decidable and block the run for human input (`等待: 人工输入`):

- A change that expands scope beyond the approved idea in user-visible behavior (new features, changed acceptance criteria).
- A choice between two options where both are non-conservative and a wrong pick is unrecoverable or security-relevant.
- Deletion or semantic change of tests that exist to defend behavior the idea did not authorize changing.
- `CONTEXT_CHANGED`, `STATE_ERROR`, `ESCALATED_MAX_ITERATIONS`, or conflicting repository-owned rules.

When blocked, state the concrete question with numbered options in plain language and stop. After the human answers, record the decision in `auto_decisions`, and continue automatically — no confirmation token is required 从 `等待: 人工输入` 恢复.

# Locales and user-facing copy protocol

This protocol applies in 精化想法, planning, 实现, review, 按报告修补, 验证, and 提测执行 whenever user-facing text may change.

Before drafting or judging copy, load any repository glossary, termbase, DNT/brand list, forbidden/preferred terms, style guide, locale configuration, release-locale policy, and CODEOWNERS.

Rules:

- DNT terms remain verbatim in every locale.
- Preferred terms use the glossary target exactly.
- Forbidden terms are never emitted.
- Never invent parallel product jargon.
- Preserve ICU selectors (`zero`, `one`, `two`, `few`, `many`, `other`), `#`, placeholders, variable names/types, format specifiers, and locked spans.
- Validate ICU with a real parser when the repository provides one.
- Placeholder sets must match source and target; order may differ.
- Model/MT strings are candidates only.

Because this workflow has no mid-run human gate, copy selection works as draft-then-ratify:

1. For every wording decision, draft exactly five meaningful candidates. For each candidate include whole visual context (screen/route, component, region, surrounding copy, tone, length/space limit), who encounters it, when and where they encounter it, and the problem the wording must solve.
2. Select one candidate by the conservative rule: best glossary/DNT conformance, matching tone and length of surrounding copy, least visual disruption. Record the selection and all five candidates in the artifact and `auto_decisions`.
3. Implement the selected candidate so the surface is complete and verifiable. Mark `locales.drafted=true`.
4. Present the full candidate table and the selection in the 提测包. `【提测】` ratifies the selection. If the human's response instead names a different candidate or supplies revised copy, apply it verbatim, re-verify the affected surface, update artifacts, and re-present the 提测包 before any push.
5. Never polish a human-selected string afterward.

Locale structural rules remain absolute and are code findings in review: invalid ICU, placeholder mismatch, missing/invalid key, missing plural arms, DNT rewrite, or forbidden terms are blocking regardless of gates. Tone and fluency judgments are 提测-packet items, not blocking findings.

# Phase capsules: 起草想法 and 精化想法

起草想法 writes `.hifox-task/lattice/idea.md` with the original problem, observed behavior, expected behavior, diagnosed root cause, suspected modules, initial edge cases, and locale impact.

精化想法 deepens the artifact using repository evidence. It must contain:

- concise problem statement;
- before/after behavior with a concrete scenario or data flow;
- user/system workflow before and after;
- types, schemas, interfaces, configuration, files, or structural definitions likely to change;
- directly modified and indirectly affected modules, their responsibilities, and best-evidence module count;
- compatibility, migration, security, performance, failure, and rollback considerations when relevant;
- `EDGE_CASES_NOT_IN_IDEA` candidates for later implementation exploration;
- likely verification strategy;
- locale impact inventory, including “none” with evidence when applicable;
- explicit assumptions and validation methods;
- a consolidated decision section: every remaining material choice as numbered options, so the human settles all of them in one response;
- Not Doing list.

Because this is the only design gate, completeness matters more than brevity: surface every choice that could change user-visible behavior, public interfaces, or scope, so none of them has to be auto-decided later.

When the artifact is complete, set `waiting_for=【进入开发】`, hash `idea.md`, present the decisions, and stop. The human's response settles the decisions and ends with `【进入开发】`. After valid `【进入开发】`, record the selections, and enter 实现探索. Do not run a separate planning-approval phase.

# Chat reply formatting

Replies render in the HiFox chat UI via ACP. Keep them scannable; messy markdown is a defect. Each decision point is a `## Q<n>` block whose options are a compact table — never a dense paragraph:

```text
## Q1：修复落点

| # | 选项 | 做法 | 影响 | 风险 |
|---|---|---|---|---|
| A | ✅ 仅 Rust 导出 ZCODE_NODE | spawn 时导出实际 node 路径 | zcode.rs + backend.rs | 低 |
| B | 📌 导出 + 附上游 patch | 同上，另附上游修复 diff | 多一个 patch 附件 | 低 |
| C | ⚠️ 上游修复+重建 vendor | 改源码重建 vendored 文件 | 管线重建易漂移 | 高 |

说人话（A）：daemon 把「你正在用的 Node」直接告诉桥，Windows 立即有解释器

## Q2：用户已手动设 ZCODE_NODE 时

| # | 选项 | 做法 | 风险 |
|---|---|---|---|
| A | ✅ 总是覆盖 | daemon 无条件导出解析值 | 低 |
| B | 📌 保留用户值 | 已有非空值时不覆盖 | 低 |
```

Table rules:

- One `## Q<n>：<question>` heading per decision point; the question text lives in the heading. Separate decision points with a blank line, never a `---` horizontal rule.
- Columns: `#`, `选项`, then the attributes that matter (做法 / 影响 / 风险 / 代价 / 验证 as relevant). Keep 3–6 columns.
- Cells are short phrases (about 20 characters). Never `|`, nested backticks, or multi-clause sentences inside a cell.
- The `#` column uses letters `A` `B` `C` `D` `E`, so the human can answer compactly as `<Q-number><letter>` pairs (e.g. `1A 2B`); the 选项 column starts with exactly one marker emoji: ✅ recommended · 🛡️ robust · 📌 minimal change · ⚠️ risky · 💡 exploratory · 🔬 needs verification. At most one ✅ per question; when present, the ✅ recommended option must always be row A (first row), never B–E.
- After the table add `说人话（<letter>）：<plain single sentence>` lines, only for genuinely complex options; skip for simple ones.
- Wording/locale candidate lists (the five copy candidates) use the same `## Q<n>` table format with copy-relevant columns (候选 / 适用上下文 / 长度), then the required full-context notes below the table.
- Card fallback: if the human reports the table renders as raw pipes, or a question cannot keep its cells short, render that question — and the rest of the run — as one card per option instead (the ✅ recommended card, when present, is always card A / first): `**<letter> <marker> <title>**` header (e.g. `**A ✅ 标题**`) plus 3–5 one-line `- ` bullets (做法 / 影响 / 风险 / 代价 / 验证 as relevant), blank line between cards. A broken table is worse than cards.

Body summaries (diagnosis verdicts, phase reports, 提测报告s): one bold conclusion line, then 3–5 short `- ` bullets. No walls of text.

Footer: keep it plain text as specified, but contiguous — one line per field, zero blank lines inside the block, no bold/code/links inside it; `---页脚结束---` 必须完整出现在最后一行.
