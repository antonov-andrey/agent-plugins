---
name: instruction-migration
description: Use only when explicitly asked to apply an approved instruction source-to-target ledger.
---

# Instruction Migration

Move or restructure an approved instruction owner set into its final canonical state without semantic loss, compatibility copies, or stale references.

Use only when the user explicitly invokes this skill and approves a source-to-target ledger. Ordinary instruction edits use `project-standards:project-instruction-developer`; structured read-only reports use `agent-workflows:instruction-audit`.

## Owners

Read completely:

- the applicable `AGENTS.md` chain;
- `project-standards:project-instruction-developer`;
- `project-standards:project-documentation-developer` when stable design or task-artifact ownership changes;
- every source owner, destination owner, and direct reference named by the approved ledger.

Fail closed if a required owner is unavailable or the ledger leaves one source fragment without an approved destination.

## Workflow

1. Inventory current repository state and every ledger source, destination, dependent reference, test, tool, and maintained document.
2. Verify that each destination is the canonical owner under the current provider and project instruction model.
3. Preserve each source requirement at exactly one destination, shorten only redundant wording, and create no compatibility path or forwarding owner.
4. Remove the old owner only after the destination is complete, then update every direct consumer in the same change.
5. Run structural validators and behavior tests for changed executable tooling. Treat them only as evidence for their exact contracts.
6. Rebuild semantic scope from the complete current owner closure and verify every ledger entry, destination, removed source, reference, and project overlay. A finding returns to step 2 or 3; after the fix, rerun affected checks and restart this full semantic pass.
7. Hand off only after one fresh post-fix pass has no finding and no uncovered ledger entry.

Do not create a migration report, completion ledger, compatibility document, or generated instruction copy unless the user explicitly requests that artifact.
