---
name: goal-delete
description: Delete one exact accepted tracked goal only after the user explicitly requests synchronized removal of its external resources, worktrees, refs, and central task directory.
---

# Goal Delete

Before mutation, inspect the current harness goal. Stop if an unfinished goal is bound to the task. Historical goal retrieval is not required. Require the user's explicit deletion request for the exact common prefix.

Run `scripts/delete.py` only after proving the latest checkpoint accepted, every task branch clean, pushed, merged, and every local main clean and equal to origin. The command owns one durable resumable transaction in this order: project cleanup hooks and exact absence proof, task worktrees, remote refs, local refs, provider excludes, bootstrap carriers, the tracked central task directory, and only then private task state. It never uses tags as a blanket deletion selector and never deletes unrelated historical artifacts.

If any proof or hook fails, retain the journal and report the exact phase. Do not force-push, reset, rewrite history, bypass the lifecycle owner with raw file removal, delete an unproven dirty worktree, or perform automatic rollback. The lifecycle implementation may use `git worktree remove --force` only after it has proved the exact task ref, clean task tree, pushed snapshot, accepted merge, and closed submodule state; initialized submodules make that Git worktree flag necessary, but it is not permission to discard changes.
