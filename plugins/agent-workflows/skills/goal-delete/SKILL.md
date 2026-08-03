---
name: goal-delete
description: Idempotently delete every remaining resource owned by one exact goal after the user explicitly requests that goal cleanup, while retaining its tracked project-goals registry record.
---

# Goal Delete

Require one explicit user request naming the exact common prefix. That request authorizes deletion of all state inside the prefix's recorded task scope, including dirty, unmerged, changed, or partially deleted task worktrees and refs. Do not infer deletion from completion, abandonment, or harness goal status.

Run `scripts/delete.py`. The durable transaction executes current project-owned cleanup hooks, removes task worktrees, remote refs, local refs, legacy bootstrap carriers and its one exact legacy exclude, records `task_resource_state: deleted` in the retained `project-goals/<common-prefix>/checkpoint.yaml`, and finally removes private lifecycle state. Shared provider excludes are outside one goal's deletion scope.

Treat an already absent in-scope resource as a completed cleanup step. Do not require accepted checkpoints, pristine task state, preserved branch commits, retained bootstrap branches/worktrees, clean implementation or `project-goals` `main` checkouts, or equality with historical fingerprints. Execute external cleanup and the retained registry-state mutation from current `origin/main` in temporary clean checkouts so unrelated canonical-checkout changes neither block cleanup nor enter its code path; fast-forward a canonical checkout only when doing so is safe and never make that synchronization a deletion precondition.

Every blocking check must prevent a concrete harmful outcome: deletion outside the exact recorded scope, mutation of primary/shared/foreign resources, an unobservable cleanup result, or false completion while a known resource remains. Retain the journal and report the exact phase only for such a failure. Do not reset, rewrite history, delete the retained goal registry directory, or use broad discovery as deletion authority.
