---
name: sequential-batch
description: Distribute ordered batches to reusable subagents while the parent owns assignment, validation, writes, dry-run, and apply.
---

# Sequential Batch

Read the plugin support contracts `lib/sequential-batch/protocol.md`, `lib/sequential-batch/subagent-protocol.md`, `lib/sequential-batch/worker-role.md`, `lib/subagent-role-contract.md`, and `lib/subagent-transport/protocol.md` from the installed `agent-workflows` plugin.

Use `agent_pool` transport for every sequential-batch run because the registry is part of this workflow contract, including when only one batch worker is active. The parent owns batch selection, task preparation, validation, dry-run apply, database writes, apply gates, and agent bindings. One worker owns exactly one assigned batch at a time.

Each task names the batch input, allowed output paths, domain references, result schema, and validation command. Workers process items strictly in input order, write and validate each item before inspecting the next, and resume from the first missing or malformed result.

Do not let a worker select another batch, write outside its task paths, write to the database, replace parent validation, or proceed to a new batch before the previous batch passes every parent-owned gate.
