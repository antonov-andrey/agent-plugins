---
name: code-antipattern-audit
description: Use when the user explicitly requests the code-antipattern-audit workflow or one merged anti-pattern audit report path under tmp/.
---

# Code Anti-pattern Audit

Run one exact mechanical phase and one independent exhaustive semantic audit of an explicitly declared Python product-code scope. This skill owns orchestration, source-report validation, deterministic merge, and final handoff. Python rules and anti-pattern cards are owned by `project-standards:python-developer`; exact mechanical verification is owned by `project-standard-check`.

## Required Owners

Read completely before execution:

- the governed repository `AGENTS.md`;
- `project-standards:project-foundation`, including `references/execution.md`;
- `project-standards:python-developer`, including `references/code-antipattern-cards.md`;
- `references/mechanical-role.md`;
- `references/semantic-role.md`;
- `agent-workflows` plugin support owner `lib/subagent-role-contract.md`;
- `agent-workflows` plugin support owner `lib/subagent-transport/protocol.md`;
- both templates under `lib/code-antipattern-audit/template/`.

Fail closed if either provider or required owner is unavailable. Do not fall back to a consumer-local copy or named-agent TOML.

## Workflow

1. Normalize one repository-relative auditable Python scope.
2. Run the complete `references/mechanical-role.md` contract and write one mechanical source report. This phase records only exact repository-wide mechanical evidence and MUST NOT interpret anti-pattern cards.
3. State transport mode `agent_pool` when the semantic role is delegated, otherwise `direct_agent`.
4. Start the semantic role only after freezing the independently derived complete card inventory. Do not provide checker identities, mechanical output, historical findings, or the implementation plan to that role.
5. When delegated, record the current semantic-agent identifier in one run-local `agent.json` as required by the transport owner and apply transport recovery without changing its fixed role.
6. Validate both source reports with `lib/code-antipattern-audit/tool/code_antipattern_audit_report_check.py --expected-scope <scope>`.
7. Send malformed or incomplete semantic-report feedback to the same current role subagent while it remains current; replacement is transport-owned recovery only.
8. Merge the two validated reports with `lib/code-antipattern-audit/tool/code_antipattern_audit_report_merge.py`.
9. Return exactly the merged report path under `tmp/`.

Every parent-to-subagent task must be idempotent or restart-resumable. The parent owns scope, complete card inventory, mechanical execution, role assignment, source-report validation, semantic coverage comparison, merge, and final path handoff. Subagents must not edit product code.

## Perspective Separation

The mechanical phase runs only the canonical exact standard runner and records its output without mapping it to cards. Heuristic anti-pattern scripts, threshold signals, selected-name checks, and false-positive exception lists are forbidden.

The semantic role independently reviews every current card in document order, collects evidence directly from current code, and confirms or rejects each candidate in a second pass. It must not consume checker output or the mechanical report during discovery or confirmation. Before merge, the parent compares returned card coverage with the complete provider-owned card inventory; missing or duplicated coverage forbids a clean verdict.

## Completion

Completion requires one valid exact mechanical report, one valid exhaustive semantic report, one deterministic merged report, closure of obsolete subagents, and a final response containing only the merged report path. A clean merged verdict requires both a clean mechanical result and a complete semantic audit with no findings.
