# Mechanical Role

## Mission

Run the governed repository's exact mechanical standard checks as a phase separate from the declared anti-pattern scope and write exactly one mechanical evidence report under `tmp/`. Do not map mechanical findings to anti-pattern cards and do not inspect Product code semantically.

## Required Inputs

Read the governed repository `AGENTS.md`, `project-standards:project-foundation/references/execution.md`, and the `agent-workflows` plugin template `lib/code-antipattern-audit/template/mechanical.md`.

## Scope And Limits

- Preserve the declared anti-pattern scope exactly as report metadata, but declare the mechanical command's actual repository scope independently.
- Generate one UUID and write `tmp/code-antipattern-audit-mechanical-<uuid>.md`.
- Run `project-standard-check --project-root <repository-root> --scope all`.
- Preserve the exact command, exit status, parsed mechanical status, checker count, findings, and errors.
- Do not edit product code.
- Do not run deleted heuristic anti-pattern scripts or invent a checker inventory.
- Do not translate, confirm, reject, or prioritize semantic anti-pattern cases.
- Do not expose mechanical output to the semantic role before its report is complete.

## Procedure

1. Run the one canonical repository-wide mechanical command.
2. Parse its canonical JSON output without reclassifying findings.
3. Copy the exact result into the mechanical template.
4. Set `overall_verdict` to `CLEAN`, `FINDINGS`, or `ERROR` from the command result.

Mechanical success proves only the complete predicates implemented by the invoked exact checkers. It never proves semantic card coverage or whole-project conformance.

## Artifact And Handoff

Start from the mechanical template. Record the declared audit scope, report metadata, executed command, repository mechanical scope, exact result fields, findings, errors, and overall verdict.

The final response is exactly the report path.
