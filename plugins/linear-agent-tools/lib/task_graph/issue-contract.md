# Linear Issue Contract Template

Every provider-created issue renders this single visible template. Sections are omitted only when their typed source collection is empty.

```markdown
# <title>

## Provider Identity

- Provider: `linear-agent-tools/v1`
- Source fingerprint: `<sha256>`
- Node key: `<stable-node-key>`
- Full source key: `<source-fingerprint>:<node-key>`
- Role: `<task:...>`
- Delivery kind: `<code|evidence|cleanup|human>`
- Execution assignment: `<assignee|delegate> <exact Linear user UUID>`

## Outcome

<one concrete outcome>

## Source

- Canonical source: `<exact immutable URL or identity>`
- Revision: `<exact immutable revision>`
- Relevant sections: <compact exact section list>

## Scope

<bounded scope list>

## Non-goals

<explicit non-goals>

## Repositories And Base Branches

<canonical origin, base and repository-supported merge method for every repository used by code, verification or resource cleanup; read-only repositories may omit a PR>

For cross-repository code delivery this section is an explicit ordered merge plan and includes the exact partial-merge recovery contract.

## Required Contracts And Skills

<exact source/stable owners and applicable skills>

## Blockers

<stable blocker node keys; Linear relations are canonical>

## Resource Ownership And Lifetime

<exact resource key, owner identity, lifetime, owning repository, downstream consumer node keys, direct-argv cleanup contract and declaration approval fingerprint>

## Verification Plan

<observable checks, owning repository, working directory, direct argv, semantic result-affecting paths and required environment identity>

## Final Human Decision Boundary

<only for final deployed-result acceptance or a task:human action; omitted from implementation, review and cleanup>

## Evidence And Links

Agent attempts reconcile nested attempt resources before appending concise semantic handoffs with direct verification, commits, exact reviewed PR base/head identities, CI and any exact exposed usage telemetry. Raw logs, prompts and credentials never appear here.
```

The issue description is visible durable execution context, not executable shell input. Project-local commands remain requirements until the task skill validates them against current project instructions.
