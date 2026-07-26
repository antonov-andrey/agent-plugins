---
name: code-audit
description: Use when the user explicitly requests the code-audit workflow or one validated code-audit report path under tmp/.
---

# Code Audit

Produce one evidence-backed audit of current code with separate exact mechanical verification and independent exhaustive semantic coverage against its actual owners. This skill owns generic scope derivation, section orchestration, report assembly, and handoff. It does not own reusable engineering checklist semantics or product-specific rules.

## Owners

Read completely:

- the governed repository `AGENTS.md` chain;
- every applicable provider skill selected in `Required Standards`;
- applicable project `DESIGN.md`, `design/**`, and project-local retained skill contracts;
- `agent-workflows` plugin support owners `lib/section-audit/protocol.md`, `lib/subagent-role-contract.md`, and `lib/subagent-transport/protocol.md`.

Fail closed when a selected provider is unavailable. Do not use a stale consumer-local copy.

## Scope

Use the user's explicit repository-relative code scope when supplied. Otherwise derive `default-changed` from current Git changes and include directly affected owner contracts and call sites. Report an explicit no-auditable-scope result when no code is in scope.

Resolve the complete owner closure and build ordered checklist sections before reading checker output or existing findings. Every normative requirement in each applicable owner MUST appear exactly once:

- each selected reusable engineering standard that governs the code scope;
- each applicable project-local code, architecture, security, persistence, runtime, or verification contract;
- direct regression and integration behavior affected by the scope.

Do not copy product-specific checklist sections from another project or invent checklist cards from textual similarity.
Do not derive semantic sections from checker identities, checker output, historical findings, the implementation plan, or already noticed problems.

## Workflow

1. Run applicable exact mechanical validators and preserve their command, exit status, mechanical status, findings, and errors. Do not expose this output to semantic roles before their reports are complete.
2. State transport mode `agent_pool` when multiple checklist sections run concurrently, otherwise `direct_agent`.
3. Create one task per checklist section with exact scope entries, reviewed owner sources, every assigned normative requirement in canonical order, role mission, limits, evidence requirements, result path, and handoff rules.
4. Require one `Satisfied`, `Problems`, or explicitly justified `Not applicable` result with current evidence for every assigned requirement. Require current file or line evidence for every finding and direct verification evidence where behavior is in question.
5. Validate each result with `lib/section-audit/tool/audit_section_result_check.py`, passing every assigned literal through one `--expected-requirement` argument. Formal validation checks structure only.
6. Send corrective feedback to the same current subagent while transport keeps it current.
7. Compare returned coverage with the independently derived complete owner inventory. Missing or duplicated coverage forbids a clean report.
8. Merge validated section results in canonical owner order with `lib/section-audit/tool/audit_report_merge.py`, passing the exact separate mechanical status and command/result evidence.
9. Validate the final `tmp/code-audit-<uuid>.md` with `lib/section-audit/tool/audit_report_check.py`.
10. Return exactly the validated report path.

The audit is read-only. A clean report requires clean applicable mechanics and complete independent semantic coverage with no findings. Passing mechanical checks never replaces, narrows, seeds, or closes semantic review.
