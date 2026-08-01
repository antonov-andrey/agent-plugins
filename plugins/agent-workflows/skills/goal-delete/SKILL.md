---
name: goal-delete
description: Delete one exact accepted tracked goal only after the user explicitly requests synchronized removal of its external resources, worktrees, refs, and central task directory.
---

# Goal Delete

Before mutation, inspect the current harness goal. Stop if an unfinished goal is bound to the task. Historical goal retrieval is not required. Require the user's explicit deletion request for the exact common prefix.

Run `scripts/delete.py` only after proving the latest checkpoint accepted, every task branch clean, pushed, merged, and every local main clean and equal to origin. The command owns one durable resumable transaction in this order: project cleanup hooks and exact absence proof, task worktrees, remote refs, local refs, then the tracked central task directory. It never uses tags as a blanket deletion selector and never deletes unrelated historical artifacts.

If any proof or hook fails, retain the journal and report the exact phase. Do not use raw file removal, force deletion, reset, history rewrite, or automatic rollback.
