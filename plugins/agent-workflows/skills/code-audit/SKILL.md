---
name: code-audit
description: Use only when explicitly asked for the code-audit workflow or its validated report.
---

# Code Audit

Produce one evidence-backed audit of current code with separate exact mechanical verification and independent exhaustive semantic coverage against its actual owners. This skill owns code scope derivation, owner closure, evidence semantics, report identity, and handoff. It does not own shared section orchestration, reusable engineering checklist semantics, or product-specific rules.

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

Execute the complete `lib/section-audit/protocol.md` orchestration. This skill supplies each section's exact code scope, reviewed owner sources, and assigned normative requirements in canonical order.

Require one `Satisfied`, `Problems`, or explicitly justified `Not applicable` result with current evidence for every assigned requirement. Require current file or line evidence for every finding and direct verification evidence where behavior is in question.

The final report path is `tmp/code-audit-<uuid>.md`. Return exactly that validated path.

The audit is read-only. A clean report requires clean applicable mechanics and complete independent semantic coverage with no findings. Passing mechanical checks never replaces, narrows, seeds, or closes semantic review.
