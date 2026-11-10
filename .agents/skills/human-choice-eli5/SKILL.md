# Skill: human-choice-eli5
# Use when the idea, plan, implement, review, or patch phase
# needs a human on remaining undecided items, questions, issues, or direction.
# Companion: eli5 for the explanation voice; locales-human-review for copy wording.

## When this applies
Whenever you would ask the human anything, or you cannot proceed without a choice:
- remaining undecided items
- clarifying questions
- open issues
- implementation / fix / product direction
- structural vs narrow, keep vs change, scope

Do **not** skip this because the stage is implement, review, or patch rather than the idea phase.
Do **not** replace unlock tokens (`【进入开发】` / `【提测】`). Those stay exact. This skill is how you **present** remaining decisions before those tokens.

Do **not** ask if exploring the repo would answer it. Explore first.

Copy/wording decisions still use the 5-option set in `locales-human-review` (Who / When & Where / Problem + whole visual context). This skill covers everything else that is still undecided.

## How to present each remaining item
For each remaining item, question, issue, or direction:

1. **ELI5 the item** using the `eli5` skill.
   - What it is, why it is still open, what happens if we pick wrong.
   - Keep **product terminology** (feature names, glossary / DNT, UI labels, domain nouns) in the product's own words. Explain around those terms; do not rename them or invent parallel jargon.
   - Assume the human has no engineering taste or product taste and does not know what is possible. Options are driven by product/engineering philosophy, explained plainly.

2. **Might-change files**
   - List the files / modules this item would likely change (paths).
   - Say `unknown until <what to inspect>` rather than inventing a fake list.
   - If options would touch different files, list files **per option**. Distinguish already-touched files from files that would change if this option is chosen.

3. **Options the human can choose**
   - Always attach choices. Prefer 3–5 meaningfully different options, not near-duplicates.
   - Number them. One line each: what it does, who it serves, the main tradeoff.
   - Include a conservative option when one exists (keep current behavior / postpone / ask later).
   - At most one option marked *recommended* with a one-line reason — advice, never a decision. When present, list the recommended option first.
   - Never pre-pick. Wait for an explicit human choice.

## Shape (repeat per item)

```text
### Undecided: <short name>
- ELI5: <plain explanation; product terms kept>
- Why it matters: <wrong-pick cost>
- Might-change files: `path` — why  (or unknown until …)
- Options:
  1. …
  2. …
  3. …
  - Recommended: N — one-line reason (optional)
- Decision: pending human
```
