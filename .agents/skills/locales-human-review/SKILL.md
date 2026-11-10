# Skill: locales-human-review
# Use when the idea, implement, review, patch, verify, or ship phase touches user-facing copy.

## Locales / i18n human review (mandatory)

### Product terminology (do not invent)
1. Before drafting or judging any locale string, load — if present — glossary, termbase (TBX/CSV), DO_NOT_TRANSLATE / brand list, style guide, and CODEOWNERS for locale paths.
2. Term classes:
   - **Do-not-translate (DNT)**: keep the source form verbatim in every locale (brand names, productized feature names, domain jargon marked untranslatable).
   - **Preferred**: use the glossary target form exactly; do not substitute synonyms.
   - **Forbidden**: never emit; replace with the preferred form.
3. Never machine-translate or "improve" DNT terms. Never invent parallel product jargon when the glossary is silent — mark `needs-review` and ask a human.
4. Protect ICU/placeholders and locked spans from rewriter damage (same names/types as source; no dropped/extra args).

### LLM / MT drafts
- Drafts are **candidates** only (`needs-review` / Waiting for review).
- You must **not** treat your own draft as approved.
- `LOCALES_LGTM` may be set only after an explicit human approval (locale owner / CODEOWNERS / reviewer). Otherwise emit `LOCALES_NEEDS_HUMAN_REVIEW`.

### Copy design options — 5 candidates, the human decides
When any user-facing string needs a wording/design decision (new copy, changed copy, or a candidate still `needs-review`), do **not** present a single draft and do **not** pick for the human.

1. **Ground the decision in the whole visual context.** State where the string actually appears: screen/route, component, region (title / button / empty state / toast / error), what is visible around it, tone of adjacent copy, and length/space limits. Use whatever evidence exists — a screenshot when available, otherwise the layout and related components (component tree, JSX/templates, styles, sibling labels); a screenshot is **not** required. If the context still cannot be established, say so instead of guessing.
2. **Exactly 5 candidate options per decision**, numbered, meaningfully different in tone, directness, length, or formality — not five near-duplicates. Every option must stay structurally valid (ICU parses; placeholders, DNT and brand terms intact).
3. **Each option names the user scenario it serves:**
   - **Who** — which user/role meets this string
   - **When & Where** — the moment and place they encounter it
   - **Problem** — what they are trying to do / what the string must resolve for them
4. **The pick is the human's.** At most one option may be marked *recommended* with a one-line reason — that is advice, never a decision; when present, list that candidate first. Until the human selects, every candidate stays `needs-review`（`文案: 草案待批准`）. Only an explicit human choice (in-thread pick, TMS apply, or the active workflow's explicit confirmation token) may set `FINALIZED_BY_HUMAN`, and only the **selected** option may be written into locale files — verbatim, never re-edited or "polished".

### Blocking vs advisory vs ship gate (4B)
- **Blocking (STATUS: FINDINGS):** invalid ICU, placeholder/format-specifier mismatch, missing/invalid keys, missing required plural arms, DNT rewritten, forbidden term used.
- **Advisory / human QC:** tone, fluency, naturalness, screenshot context gaps.
- **Ship gate (not automatic FINDINGS):** structurally OK strings still awaiting human linguistic approval on **release** locales → set `locales.needs_human=true` for verify/ship. Do **not** force whole-run `STATUS: FINDINGS` solely for pending human linguistic approval.

### Verdicts (fail-closed)
At the end of review, you MUST output exactly one:
- `LOCALES_LGTM` — human-approved or no locale-facing change
- `LOCALES_NEEDS_HUMAN_REVIEW` — default if uncertain or any candidate remains unapproved

Also set the reply footer's 文案 field when applicable:
- `文案: 未触碰|已起草|草案待批准|已获人工批准`（state keys `locales.drafted` / `locales.copy_ratified`）
- While human finalize is pending: `等待: 人工输入` and `文案: 草案待批准`
