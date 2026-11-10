---
name: evidence-settlement
description: Use when reviewing or verifying a HiFox task; turn a behavior claim into scope-matched, durable evidence and classify every non-pass without treating an agent report as proof.

# Evidence Settlement

Use this skill before declaring an implementation ready for review, a review clear, a behavior verified, or a release package eligible.

## Establish the claim

Write the exact claim and its proof scope:

```text
Behavior claim: <what should be true>
Proof scope: <surface, path, environment, data, and failure cases covered>
Authoritative source: <running behavior, test, API response, log, trace, screenshot, or git state>
```

## Gather proof

- Read the complete source artifacts and current diff before choosing checks.
- Select the smallest proof method that can fail for the claimed regression: focused test, typecheck/lint, API/browser/E2E, manual inspection, or git/remote evidence.
- Prefer real entry paths and external state over assertions that merely repeat implementation details or an agent's conclusion.
- Re-run the original reproduction when fixing a bug; cover the happy path and the relevant failure or boundary path.
- For UI claims, compare the same route, viewport, data, theme, and browser state before and after. Record a fallback when a comparable before image is unavailable.
- Record exact commands, non-sensitive outputs, evidence paths, and the first failure point. Never include secrets, cookies, tokens, or generated auth values.

## Settle the result

Classify each non-pass as one of: `task-related`, `unrelated with evidence`, `pre-existing with evidence`, `blocked`, `skipped`, `delegated`, or `uncertain`. Do not call an unrelated or pre-existing failure without comparable evidence.

The handoff must include:

- claim and proof scope;
- source artifacts and stable diff/commit/patch surface;
- commands and results;
- evidence and attachment/local paths;
- failure classification, skipped/delegated checks, and residual risk;
- verdict and the single next owner or authorization gate.

## Reviewer-ready and shipped claims

For managed task delivery, settle review and ship as separate claims:

```text
Review claim: <a reviewer can inspect the proposed change>
Review proof: dedicated branch commit + compare link + task-matched evidence packet

Ship claim: <the approved change was delivered>
Ship proof: verified remote integration-branch commit + task status update + task comment
```

Never collapse these fields:

- `BRANCH_COMMIT` — the commit available for review;
- `COMPARE` — the observed comparison against the repository's authoritative base branch;
- `DEVELOP_COMMIT` — the final integration-branch commit;
- `DEVELOP_CONTAINS_FIX` — verified remote containment;
- `SHIPPED` — only after integration, task status, and task comment all succeed.

Before `WAIT_CONFIRM_SHIP`, `BRANCH_COMMIT` and `COMPARE` are required. A branch push is review preparation, not shipping. A blocked real surface may be reviewer-visible as `REVIEWABLE_WITH_LIMITATION`, but it remains `SHIP_ELIGIBLE=false` until the human's existing `【提测】` explicitly authorizes the residual risk. Preserve `BLOCKED` in the final evidence; never upgrade it to `PASS`.

## Exit audit

Do not use review approval as verification, a clean diff as behavioral proof, or a passing narrow check to support a broader claim. If proof scope, failure classification, or ownership is ambiguous, stop and hand back the evidence gap.
