---
name: github-opensource-research
description: Deep research methodology for surveying GitHub open-source repos before a design/requirement decision. Use when the user asks to 调研开源项目、find prior art, survey how the ecosystem solves X, "research github repos", requires a star threshold (e.g. >=300 stars) or a minimum repo count (e.g. at least 20 repos), or asks to shallow-clone repos and study their implementation. Grounds conclusions in cloned source files plus official docs/blog posts, never in README-level impressions.
---

# GitHub Open-Source Repo Research

Evidence-first repo survey: derive keywords from the requirement, discover from multiple dimensions, verify metadata, shallow-clone, read the code, then synthesize mechanisms — not repo lists.

## Hard requirements (every run)

1. Keyword/tag matrix is produced BEFORE any repo search.
2. Each discovery track collects at least the user-required repo count (default 20) with stars >= the required threshold (default 300). Verified at selection time via API, never eyeballed from search snippets.
3. Sources are diversified: no single query contributes more than 1/3 of the final set; at least 3 distinct keyword families and 3 distinct repo archetypes (library/framework, product/app, benchmark/eval, docs/spec, plugin/extension).
4. Every selected repo is shallow-cloned locally and at least 50 files are read per repo, with a mandated file mix (below).
5. For each adopted mechanism: official docs / blog post / best-practice article is read and the URL recorded.
6. Final deliverable is a mechanism-level synthesis with per-claim `path:line` citations, not a link dump.

## Phase 0 — Pin the research question

Write down before searching:

```text
Goal: <what decision this research informs>
Constraints: <language / runtime / platform / model / scale that the solution must fit>
Track A: <core domain, e.g. agent harness / orchestration / eval>
Track B: <adjacent tech or product domains whose mechanisms may transfer>
Output: <the exact deliverable: doc? comparison table? integration proposal?>
```

If the user gave explicit quotas (repo count, star floor, file count), copy them verbatim into the plan; they are hard floors, not targets.

## Phase 1 — Keyword and tag matrix (before searching)

Derive 8–15 keywords across at least these dimensions:

| Dimension | Purpose |
|---|---|
| Core domain | exact terms the user named (e.g. "agent harness", "multi-agent framework") |
| Mechanism | how it works internally (e.g. "context engineering", "memory management", "scaffold", "orchestrator") |
| Protocol / interface | standards and wire formats (e.g. "MCP", "ACP", "LSP") |
| Platform / integration | where it embeds (e.g. "IDE plugin", "CLI daemon", "CI") |
| Quality / evaluation | how it is proven (e.g. "benchmark", "eval harness", "regression") |
| Adjacent field | same problem under another name (e.g. robotics "behavior tree", game "AI planner", data "pipeline orchestration") |

Map each keyword to GitHub topics and synonyms; note the language qualifier when relevant. Do not proceed to Phase 2 with fewer than 3 keyword families.

## Phase 2 — Discovery (two tracks, quotas enforced)

Run both tracks independently. Track A = core domain repos; Track B = adjacent tech/product repos.

```bash
# keyword search, star floor, sort by stars
gh search repos 'agent harness' --stars='>=300' --sort=stars --limit=50 \
  --json fullName,stargazersCount,description,language,license,updatedAt,isArchived,url

# topic search (topics are often better than keywords)
gh search repos --topic=ai-agents --topic=llm-agent --stars='>=300' --limit=50 \
  --json fullName,stargazersCount,description,language,license,updatedAt,isArchived,url

# verify metadata for a candidate at selection time
gh api repos/OWNER/REPO --jq '{full_name,stars:.stargazers_count,archived,pushed:.pushed_at,license:.license.spdx_id,topics}'
```

Rules:

- Record a query log: date, exact query string, filters, result count. Every finding must be traceable to a query.
- Enforce diversification: >= 3 keyword families per track; drop surplus repos from any single query until it contributes <= 1/3 of the track's set.
- Stars verified via `gh api` at selection time; exclude archived repos unless uniquely relevant (note why). Star count is a relevance filter, never evidence that a mechanism fits your constraints.
- Checkpoint after discovery: report track A/B counts, threshold compliance, and keyword-family coverage before cloning.

## Phase 3 — Select the deliverable repo set

For each repo record one row: `repo | stars | last push | license | language | one-line thesis | mechanism worth stealing | where it maps into our design | risk`.

- Reject-and-replace rule: if a repo yields no concrete mechanism after reading, drop it and backfill from the query log. A repo that only "seems related" is dead weight.
- License check before proposing to copy code (MIT/Apache-2.0 vs GPL/AGPL changes what "adopt" means).

## Phase 4 — Shallow clone + file-level exploration

```bash
mkdir -p /tmp/oss-research/<track>
GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 --filter=blob:none \
  https://github.com/OWNER/REPO /tmp/oss-research/<track>/OWNER__REPO
```

- Always `--depth 1` (`--filter=blob:none` additionally lazily fetches blobs); never full history; skip LFS smudge.
- Monorepos: explore from the subdirectory that matches the mechanism, not the root listing.
- Per repo read at least 50 files with this mix (inventory first with glob/find, then read):
  - <= 10 docs/README/changelog (context only — reading docs is not research)
  - >= 25 core implementation files (entrypoints, the mechanism's modules)
  - >= 5 config/manifest/build files (how it wires and ships)
  - >= 5 tests or examples (what behavior is guaranteed)
  - >= 5 extension-point files (hooks, plugin API, public interfaces)
- Track progress as a counter per repo; report `files explored: N` with the count, not "explored the repo".
- For mechanisms that live outside code (protocols, hosted products): read official docs, the project's blog posts, and at least one independent best-practice article; record URL + the key claim each supports.

## Phase 5 — Synthesize

Deliver, in this order:

1. **Mechanism matrix** — rows are mechanisms, not repos: `mechanism | repos implementing it | how (path:line) | tradeoffs | verdict (adopt / adapt / reject)`.
2. **Theory** — design principles extracted across repos; each principle names >= 2 independent repos as evidence.
3. **Practice** — concrete integration proposal for our constraint set, citing cloned files.
4. **Coverage evidence** — query log summary, repo count per track with star verification, files-explored counts per repo, doc/article URLs.

Cite every code claim as `repo: path:line`. Label anything not directly read as `[INFERENCE]`.

## Anti-patterns (reject your own work if you see these)

- 20 repos discovered by one query or one keyword family.
- README-level "research"; repo explored but < 50 files read, or 50 files all docs.
- Star count used as an argument for adopting a mechanism.
- Skipping official docs for protocol-level mechanisms.
- Proposing to copy code without a license check.
- A final report that lists repos without saying what to adopt.
