---
name: task-implement
description: "Implement one dispatchable Linear task: reconstruct semantic current state, adopt its exact issue worktrees, change only scope, verify, publish, and stop at Review for an independent Codex reviewer."
---

# Implement Linear Task

Execute only one `task:implementation` issue. Every `Todo` or `Rework` attempt starts in a fresh Codex thread; previous chat history is never execution state.

Use `gpt-5.6-sol` medium only for a bounded implementation whose approved plan closes every conceptual decision and whose behavior is directly covered by deterministic checks. Use max for planning, architecture, security, migration, cross-repository or unresolved lifecycle work, and escalate immediately after a substantive conceptual finding. An explicit user reasoning choice takes precedence.

Read `../../references/manual-workflow.md` and the complete issue, relations, comments, exact published source revision and relevant sections, repository instructions, Git/PR state and current direct verification evidence.

Every preview or user-facing handoff response MUST state that one exact issue process-lifetime host-local attempt guard is acquired before dispatch or status mutation, held continuously through nested attempt cleanup and final Linear provider readback, and released only by process exit after that boundary. The provider handoff does not duplicate guard state.

## Start

1. Set `LINEAR_AGENT_WORKSPACE_ROOT` to the explicit canonical multi-repository checkout container. It must not be a repository or task worktree. Start `../../lib/task_workspace/tool/attempt.py hold --issue-identifier <exact-identifier>`, require `status=held`, and keep that process alive through final readback. Never derive the namespace from CWD or selected repository and never explicitly unlock early.
2. Fully reread the authenticated destination, exact issue, relations, comments and source. Save the transient snapshot and run `../../lib/linear_boundary/tool/task.py dispatch`; require role `task:implementation`, delivery `code` or `evidence`, and no blockers.
3. For code delivery, run `scripts/workspace.py prepare` from the issue's exact origins/bases. On first dispatch its baseline is freshly fetched `origin/<base-branch>`, not local `HEAD`. Render and publish exactly one byte-identical `workspace-baseline` comment before Product mutation. Preparation creates/adopts only `.worktree/<issue>` and `linear/<issue>`; canonical checkouts remain untouched.
4. Before `Todo`/`Rework -> In Progress`, run nested `task-cleanup` for exact attempt-lifetime resources while reusing this attempt's live guard. On `Rework`, adopt the same worktrees, branches and PRs without reset and recover from semantic current source/Git/PR/evidence state; do not require the previous Codex version or unrelated orchestration equality.
5. Validate the transition with completed attempt cleanup, mutate Linear and reread it. Evidence-only probes create no fake branch or PR.

## Delivery

- Apply applicable project standards and implement only the bounded outcome. Complete one coherent owner slice before verification and publication. Use `agent-workflows:git-commit`; push each logical green commit immediately.
- Run targeted checks after each owner slice, directly applicable complete deterministic suites on the frozen result, and a fresh complete semantic owner audit after the last fix.
- Reuse a direct current successful result only after proving its result-affecting source, exact command, environment/release and semantic contract unchanged. Record that semantic decision and direct evidence in the handoff. Never create a verification receipt, candidate fingerprint or generic invalidation gate.
- If behavior evaluation applies, run only IDs in the current failed list. After one owner-level root correction, rerun only the immediately preceding failed IDs; passed IDs remain accepted in the cycle. The semantic contract determines whether provider behavior or case/judge changes. An empty failed list means no model case and a full corpus is forbidden unless separately owned.
- For code delivery, create/adopt exact PRs through `scripts/pull_request.py`, then reread the official Linear integration attachment/diff. Run CI or other long checks in a native background terminal and resume through native waiting; do not implement model polling, a Project supervisor, command timeouts, arbitrary thresholds or an alternate Codex home.

## Handoff

Reconcile attempt-lifetime resources first. Build one final minimal `handoff` input. Start with a concise human summary. For a code result, include one ordered nonempty composite PR candidate list. Each candidate contains its URL, base branch, base commit and head commit. Include direct check results and an optional evidence link on each result. Include any nonempty subset of exact known structured `input_tokens`, `cached_input_tokens`, `cache_write_input_tokens`, `output_tokens` and `reasoning_output_tokens` counters directly exposed by Codex. Aggregate each counter independently across nested invocations. Omit every unavailable outcome field instead of emitting an empty collection. Never add issue, role, delivery, outcome, cleanup, timestamp, UUID, schema, separate commit map or compatibility metadata. Never estimate or derive usage.

Render the handoff through `../../lib/verification/tool/evidence.py handoff`, publish it once, fully paginate and require one byte-identical provider readback. The handoff is recovery context, not an approval object or automatic verification cache. Exclude prompts, secrets and raw logs; delete transient inputs after readback.

Validate `In Progress -> Review` only after result, delivery-applicable verification, direct evidence, handoff, publication and required CI are complete. Mutate and reread Linear, preserve the workspace, and stop. A separate fresh max-reasoning `task-review` owns the gate; implementation never performs or waits for per-PR human approval.

The guard is released only by process exit after nested cleanup and the final Linear readback.
