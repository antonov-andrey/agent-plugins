---
name: task-implement
description: "Implement one dispatchable Linear task: reconstruct fresh context, adopt or create exact issue worktrees, change only its scope, verify, commit and push logical slices, open linked PRs, wait for required CI, and stop at Human Review."
---

# Implement Linear Task

Execute only one `task:implementation` issue. Every `Todo` or `Rework` attempt starts in a fresh Codex thread; previous chat history is never execution state.

Read `../../references/manual-workflow.md` and the complete issue, relations, comments, exact published source revision and relevant sections, repository instructions, Git/PR state and current verification evidence.

Every preview or handoff response MUST state that one exact issue process-lifetime host-local attempt guard is acquired before dispatch or status mutation, held continuously through nested attempt cleanup and the final Linear provider readback, and released only by process exit after that boundary.

## Start

1. Set `LINEAR_AGENT_WORKSPACE_ROOT` to the explicit user workspace. Start `../../lib/task_workspace/tool/attempt.py hold --issue-identifier <exact-identifier>` as the exact issue process-lifetime host-local guard, require its initial `status=held` JSON, and keep the same process alive through the complete attempt, nested attempt-resource cleanup and final Linear read-back. A nonzero exit means another local attempt owns the issue; do not continue. Do not describe or perform an earlier explicit unlock: process exit after final read-back is the release mechanism for the kernel lock.
2. Reread the exact authenticated Linear destination and issue. Save the complete transient task snapshot and run `../../lib/linear_boundary/tool/task.py dispatch`; require exit `0`, role `task:implementation` and delivery `code` or `evidence`.
3. For code delivery, build a transient repository request from issue origins/bases and recorded workspace baselines, then run `scripts/workspace.py prepare`. On first dispatch, the returned baseline for each repository is the freshly fetched exact `origin/<base-branch>` commit, not local `HEAD`; record this first remote-base baseline. Build the exact `workspace-baseline` input from those branch/repository commits plus the source fingerprint and render it through `../../lib/verification/tool/evidence.py workspace-baseline`. Fully paginate issue comments: create the provider-marked baseline only when absent, accept exactly one byte-identical comment, and stop on duplicate or conflicting baseline comments. Reread it before Product mutation. Preparation creates or adopts only deterministic linked `.worktree/<issue>` worktrees and `linear/<issue>` branches; the canonical main checkout, its branch and its files remain untouched. A crash before the comment is recovered from private Git-admin state; on later attempts require that exact comment to match the adopted private state.
4. On `Rework`, adopt the same branches, worktrees and PRs. Never reset user work or silently change baseline. A base update/rebase is a candidate mutation requiring a new approval cycle.
5. Validate the exact `Todo`/`Rework -> In Progress` proof with `../../lib/linear_boundary/tool/task.py transition`, then mutate and reread Linear. Evidence-only probes create no fake branch or PR.
6. Before recovering a prior attempt, invoke `task-cleanup` for every declared `attempt`-lifetime resource using its exact recorded identity; absence is success. That nested cleanup reuses this attempt's live guard rather than acquiring a second lock.

## Delivery

- Apply every applicable project standard and implement only the bounded issue outcome.
- Complete one coherent owner slice before verification and publication. Use `agent-workflows:git-commit` for logical commits and push each commit immediately; do not emit symptom-by-symptom intermediate commits that are not independently green.
- Run targeted checks after each completed owner slice, the applicable full suite on the frozen candidate, live acceptance only for that exact deployed candidate, and a fresh semantic audit after the last fix.
- Build receipts from the exact source fingerprint, direct argv, canonical absolute working directory, and every result-affecting checkout.
  - The verification key binds only the complete declared result inputs.
  - Each checkout binds its canonical absolute path, roles, repository URL, full commit, recursive submodules, and dependency locks.
  - Use canonical checkout-relative paths for submodules and locks.
  - Keep repeated repository URLs as separate checkout records.
  - Every receipt preview and handoff MUST state these per-checkout identities.
  - Bind corpus content, model identity, model configuration, and applicable environment or release identity.
  - Preserve the exact machine-readable result as one independently readable immutable provider artifact.
  - Use a durable canonical HTTPS provider URL without credentials, ports, query strings, or fragments.
  - Publish the exact codec-rendered receipt comment; its JSON slash escapes preserve that URL value across Linear readback instead of exposing an autolink target that Linear can replace with a presigned URL.
  - A receipt-bearing handoff is invalid unless it explicitly reports that every exact codec-rendered receipt comment was published.
  - The receipt key binds the verification key, outcome, UTC completion instant, exact artifact URL, and artifact content SHA-256.
  - Create and evaluate only the current receipt schema through `scripts/receipt.py`.
  - An evidence candidate input carries the complete current-schema receipt by evidence kind. `../../lib/verification/tool/evidence.py candidate` validates each receipt and derives the compact candidate identity only from its receipt key; a stable verification key is never an evidence approval identity.
  - A source-independent evidence probe may use an empty checkout list, but its source fingerprint remains required.
  - Reuse only an exact passed receipt whose bound artifact remains authoritative.
- For code delivery, push the deterministic `linear/<lowercase-issue-identifier>` branch and run `scripts/pull_request.py` to create or accept PRs whose title and branch contain the exact Linear identifier. Fully reread the Linear issue and require the official integration-created GitHub PR attachment or diff for that exact PR URL. A title, branch or manually added URL is not link evidence. Wait for applicable required CI without frequent polling.

## Handoff

Every receipt-bearing preview or handoff MUST explicitly state all of these facts:

- the stable verification key binds the source fingerprint, exact direct argv, canonical absolute working directory, corpus content, model identity, model configuration, applicable environment and release identity, plus each checkout's canonical absolute path, roles, repository URL, full commit, recursive submodules and dependency locks;
- the separate receipt key binds that stable verification key itself plus the outcome, UTC completion instant, exact artifact URL and artifact content SHA-256;
- every exact codec-rendered verification receipt comment was published before candidate publication; and
- each durable canonical HTTPS artifact URL contains no credentials, port, query string or fragment.

A generic claim that a receipt binds inputs and evidence, or a handoff that only says receipts were created, is insufficient.

Before publishing the attempt result, invoke `task-cleanup` for all current `attempt`-lifetime resources. Build the exact code or evidence candidate fingerprint with `../../lib/verification/tool/evidence.py candidate`. Evidence delivery supplies a map of evidence kind to complete current-schema verification receipt; the tool validates the derived receipt key and emits the compact map of evidence kind to receipt key that Human Review approves. Never substitute the stable verification key. Build the concise structured attempt comment with its `attempt` operation: attempt ID, role, delivery kind, UTC start/end, outcome, delivery-applicable commit set, receipt hit/miss counts, external wait duration, token usage only when directly exposed, candidate fingerprint, PR/CI links and bounded evidence. Publish that exact comment to Linear. Exclude prompts, secrets and raw logs; delete transient input files.

Validate the exact `In Progress -> Human Review` proof with the shared transition tool. Move only when result, verification, commit/push/PR, required CI and evidence are complete, then reread Linear. Stop the agent and preserve the workspace. Any later candidate mutation requires `Rework`, a fresh thread and a new fingerprint.

Attempt ownership is released only by process exit after nested cleanup and the final Linear read-back; never unlock it earlier inside the workflow.
