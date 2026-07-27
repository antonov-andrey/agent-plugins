---
name: instruction-audit
description: Use only when explicitly asked for instruction-audit or its validated semantic report.
---

# Instruction Audit

Produce one evidence-backed semantic audit of current instruction artifacts against their real owner and precedence model. This skill owns instruction scope derivation, owner closure, evidence semantics, report identity, and handoff. Shared section orchestration belongs to `lib/section-audit/protocol.md`; instruction structure and wording rules belong to `project-standards:project-instruction-developer`; project-specific contracts stay project-local.

## Owners

Read completely:

- the governed repository `AGENTS.md` chain;
- `project-standards:project-instruction-developer`;
- every provider skill named by `Required Standards` whose selection or reference is in scope;
- applicable project `DESIGN.md` and nested instruction owners;
- `agent-workflows` plugin support owners `lib/section-audit/protocol.md`, `lib/subagent-role-contract.md`, and `lib/subagent-transport/protocol.md`.

Fail closed when a selected provider is unavailable. Do not use consumer-local copies of provider contracts.

## Scope

Use explicit repository-relative instruction paths when supplied. Otherwise derive `default-changed` from changed canonical instruction artifacts and include every owner whose precedence, reference, or dependency is affected.

Resolve the complete owner closure before looking at findings: every applicable `AGENTS.md`, every selected provider reference, every candidate provider whose applicability must be decided, every referenced stable design owner, every nested instruction owner, and every approved source-to-target ledger entry.

Build ordered checklist sections from that complete owner closure. Every normative requirement in every in-scope owner MUST appear in exactly one section task with its literal owner and applicability boundary. Owner placement, precedence, provider selection, external references, wording and ambiguity, duplication, path scope, retained project overlay, design boundaries, and task-artifact boundaries are required review dimensions, not an optional concern list. Do not derive sections from checker inventory, checker output, historical findings, an implementation plan, or already noticed problems. Do not assert exact prose or file presence as a surrogate for semantic review.

## Workflow

Execute the complete `lib/section-audit/protocol.md` orchestration. This skill supplies each section's exact instruction scope, complete owner sources, and assigned normative requirements in owner order.

Require one semantic verdict with concrete current artifact evidence for every assigned requirement, or one explicit not-applicable reason grounded in the current owner boundary. Every finding requires concrete current artifact evidence.

The final report path is `tmp/instruction-audit-<uuid>.md`. Return exactly that validated path.

The audit is read-only. A clean report requires both a successful applicable mechanical phase and complete independent semantic coverage with no findings. Mechanical validators never replace, narrow, seed, or close semantic instruction review.
