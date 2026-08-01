---
name: goal-merge
description: Exclusively merge and accept exactly one published cross-repository goal checkpoint when the user explicitly starts the merge workflow in a dedicated Codex thread.
---

# Goal Merge

Run only in one dedicated exclusive thread and process one selected checkpoint. Read the task `spec.md`, `goal.md`, `checkpoint.yaml`, every participating repository instruction chain, and the primary-environment acceptance contract.

Run `scripts/merge.py merge` first. It holds the workspace merge lock, preflights every repository before mutation, performs only compare-and-swap fast-forward main updates, and resumes a recorded partial merge without rollback or source edits.

After every project main is the exact selected snapshot, deploy and verify the full permanent primary environment using the owning repositories' current acceptance rules. A failure leaves the merge journal and accepted pointer unchanged; report the exact fix-forward requirement and do not create or modify implementation code.

Only after complete green acceptance run `scripts/merge.py accept`. Never accept based on local unit tests, partial deployment, an earlier environment, or a different commit set.
