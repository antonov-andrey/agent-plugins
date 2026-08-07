---
name: task-review
description: Independently review either one implementation in Review or one post-merge review issue from complete current owners; zero findings advance it and findings route to explicit rework without hidden fixes.
---

# Review Linear Task

Run in a fresh `gpt-5.6-sol` thread with max reasoning. Read `../../references/manual-workflow.md`, complete current Linear context, exact source revision, repository/PR/merged state, applicable stable owners and direct external evidence. Prior reports, implementation artifacts, changed-file lists and passing tests never bound review coverage.

Every preview or handoff response MUST state that one exact issue process-lifetime host-local attempt guard is acquired before dispatch or status mutation, held continuously through nested cleanup and final provider readback, and released only by process exit afterward.

## Candidate Review

For a `task:implementation` issue in `Review`, acquire the canonical-root guard before dispatch, require code/evidence implementation delivery and no blockers, and inspect read-only current state. Do not move it to `In Progress`, create a review branch, modify Product code or require a GitHub human review.

Derive full semantic coverage independently from every applicable owner. Compare the implementation handoff's direct PR URL/head map with freshly read current GitHub state; the handoff is context, not an approval fingerprint. Reuse direct verification only when source, exact command, environment/release and semantic contract are unchanged. Run new checks when that semantic proof is absent.

- Zero findings: reconcile attempt resources, publish and byte-for-byte reread a `review-passed` semantic handoff with exact reviewed PR heads and direct evidence. Validate current reviewed state. Move code delivery `Review -> Merging`; move evidence implementation `Review -> Done`.
- Findings: merge duplicate manifestations into owner-level findings, publish and reread a `review-findings` handoff with exact current PR heads and direct evidence, then validate `Review -> Rework`. Do not fix findings in review.

## Post-Merge Graph Review

For a `task:review` / `evidence` issue in `Todo`, `In Progress` or `Rework`, validate dispatch and enter `In Progress` from `Todo`/`Rework`. Review the entire current graph-owned scope from scratch using read-only merged commits and environment state; do not create a fake branch or modify Product code.

- Zero findings: publish and reread a `review-passed` handoff, then validate `In Progress -> Done` with direct evidence and stop.
- Findings against unfinished work: return the owning implementation to `Rework` where applicable, create the remediation relation, then publish and reread a `review-findings` handoff before returning this review to blocked `Todo`.
- Findings against merged/`Done` work: use `task-graph-create` to preview an approved active-Project delta that creates bounded implementation blockers and returns only this review to `Todo`. Publish and reread the `review-findings` handoff before that transition. After remediation, a new fresh-thread full review starts from scratch.

For both modes, use native background-terminal waiting for external checks, never model polling/supervisors/timeouts/thresholds or alternate Codex homes. Handoffs carry exact structured Codex usage counters only when directly exposed; never estimates. No verification receipt, candidate fingerprint or generic invalidation gate is created.

Nested cleanup reuses the live guard. Delete transient inputs only after final provider readback; process exit then releases the guard.
