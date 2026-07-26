---
name: instruction-audit
description: Use when the user explicitly requests the instruction-audit workflow or one validated instruction-audit report path under tmp/.
---

# Instruction Audit

Produce one evidence-backed semantic audit of current instruction artifacts against their real owner and precedence model. This skill owns generic scope derivation, section orchestration, report assembly, and handoff. Instruction structure and wording rules belong to `project-standards:project-instruction-developer`; project-specific contracts stay project-local.

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

1. Run the applicable exact mechanical validators and preserve their command, exit status, mechanical status, findings, and errors as a separate evidence block. Their inventory MUST NOT define semantic sections.
2. State transport mode `agent_pool` when multiple checklist sections run concurrently, otherwise `direct_agent`.
3. Create one task per checklist section with exact scope, complete owner sources, every assigned normative requirement in owner order, role mission, limits, evidence requirements, result path, and handoff rules.
4. Require one semantic verdict with concrete current artifact evidence for every assigned requirement, or one explicit not-applicable reason grounded in the current owner boundary.
5. Require concrete current artifact evidence for every finding.
6. Validate each result with `lib/section-audit/tool/audit_section_result_check.py`, passing every assigned literal through one `--expected-requirement` argument. Formal validation checks artifact structure only and MUST NOT be treated as semantic validation.
7. Send corrective feedback to the same current subagent while transport keeps it current.
8. Before merge, compare returned coverage with the independently derived owner requirement inventory. Missing, duplicated, or checker-derived coverage is a finding and forbids a clean report.
9. Merge validated section results in canonical owner order with `lib/section-audit/tool/audit_report_merge.py`, passing the exact separate mechanical status and command/result evidence.
10. Validate the final `tmp/instruction-audit-<uuid>.md` with `lib/section-audit/tool/audit_report_check.py`.
11. Return exactly the validated report path.

The audit is read-only. A clean report requires both a successful applicable mechanical phase and complete independent semantic coverage with no findings. Mechanical validators never replace, narrow, seed, or close semantic instruction review.
