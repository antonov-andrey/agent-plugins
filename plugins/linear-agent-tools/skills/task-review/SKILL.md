---
name: task-review
description: Independently review either one implementation in Review or one post-merge review issue from complete current owners; zero findings advance it and findings route to explicit rework without hidden fixes.
---

# Review Linear Task

Run in a fresh `gpt-5.6-sol` thread with max reasoning. Read `../../references/manual-workflow.md`, complete current Linear context, exact source revision, repository/PR/merged state, applicable stable owners and direct external evidence. Prior reports, implementation artifacts, changed-file lists and passing tests never bound review coverage.

Every preview or user-facing handoff response MUST state that one exact issue process-lifetime host-local attempt guard is acquired before dispatch or status mutation, held continuously through nested cleanup and final provider readback, and released only by process exit afterward. The provider handoff does not duplicate guard state.

## Candidate Review

For a `task:implementation` issue in `Review`, acquire the canonical-root guard before dispatch, require code/evidence implementation delivery and no blockers, and inspect read-only current state. Do not move it to `In Progress`, create a review branch, modify Product code or require a GitHub human review.

Before recovering any interrupted or rework path, reconcile its declared attempt-lifetime resources under the live guard. Derive full semantic coverage independently from every applicable owner. For code delivery, compare the implementation handoff's complete ordered composite PR candidate list with freshly read current GitHub state. Each candidate contains its URL, base branch, base commit and head commit. Evidence delivery omits inapplicable PR candidates. The handoff is context, not an approval fingerprint. If a declared code candidate value changed, first reconcile attempt resources, then publish the stale-state finding and validate `Review -> Rework` with changed reviewed identity. Reuse direct verification only when source, exact command, environment/release and semantic contract are unchanged. Run new checks when that semantic proof is absent.

- Zero findings: reconcile attempt resources, then publish and byte-for-byte reread a minimal handoff. Its human summary states the zero-finding result. Include exact composite PR candidates for code delivery and omit them for evidence delivery. Include direct check results with applicable evidence links. Validate current reviewed identity when code applies. Move code delivery `Review -> Merging`; move evidence implementation `Review -> Done`.
- Findings: merge duplicate manifestations into owner-level findings and reconcile attempt resources. Publish and reread a minimal handoff whose summary states the finding result. Include exact current composite PR candidates only for code delivery, plus direct check results with applicable evidence links. Then validate `Review -> Rework`. Do not fix findings in review.

## Post-Merge Graph Review

For a `task:review` / `evidence` issue in `Todo`, `In Progress` or `Rework`, validate dispatch, reconcile attempt resources and only then enter `In Progress` from `Todo`/`Rework`. Review the entire current graph-owned scope from scratch using read-only merged commits and environment state; do not create a fake branch or modify Product code.

- Zero findings: reconcile attempt resources and publish a minimal handoff whose summary states the zero-finding result. Reread it, then validate `In Progress -> Done` with direct evidence and stop.
- Findings against unfinished work: reconcile attempt resources and publish a minimal handoff whose summary states the finding result. Reread it, then return the owning implementation to `Rework` where applicable. Create the remediation relation and return this review to blocked `Todo`.
- Findings against merged/`Done` work: use `task-graph-create` to preview the approved active-Project delta. Reconcile attempt resources and publish a minimal finding-summary handoff. Reread it. Only then apply the delta that creates bounded implementation blockers and returns this review to `Todo`. After remediation, a new fresh-thread full review starts from scratch.

For both modes, attempt-resource cleanup is a mandatory pre-handoff and pre-transition step for success, finding, stale-state, failed, canceled and controlled interrupted outcomes. No status mutation or handoff publication may precede it. An abrupt process loss publishes nothing; the next guarded attempt repeats the idempotent cleanup before recovery. Use native background-terminal waiting for external checks, never model polling/supervisors/timeouts/thresholds or alternate Codex homes. Handoffs carry any nonempty subset of exact known structured Codex usage counters only when directly exposed; omit usage when none are exposed and never estimate. No verification receipt, candidate fingerprint or generic invalidation gate is created.

Nested cleanup reuses the live guard. Delete transient inputs only after final provider readback; process exit then releases the guard.
