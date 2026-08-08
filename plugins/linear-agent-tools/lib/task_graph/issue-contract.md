# Linear Issue Card Contract

This file is the single canonical owner for provider-created Linear issue cards. The card is human-readable execution context, not a transaction envelope or operational graph.

## Required Shape

Use the Linear issue title for one direct task name. Do not repeat it as a Markdown heading in the description.

Start the description with one concise paragraph. State the task and its required result without provider metadata.

Keep these sections:

```markdown
<one standalone task-and-result paragraph>

## Required work

<only the work needed to produce the result>

<required Delivery section for task:implementation only>

## Completion criteria

<observable conditions that make the task complete>

<optional helpful sections>

Task key: `<stable-task-key>`
```

`Task key` is always the final non-empty line. It is a lowercase semantic slug matching `[a-z0-9]+(?:-[a-z0-9]+)*`.

## Historical Task Key Input

The synchronization owner emits only the current final-line `Task key` form. New cards and every mutable provider-owned card use no other key form.

One read-only decoder may extract identity during the declared current-Project migration. Later synchronization uses it only for unchanged terminal cards. It accepts only these exact non-fenced Markdown forms:

```markdown
Task key: `<stable-task-key>`
```

This current form must be the final non-empty line.

```markdown
## Source

* Task key: `<stable-task-key>`
```

This historical embedded form must be a direct bullet in the `Source` section.

```markdown
## Provider Identity

* Node key: `<stable-task-key>`
```

This historical provider form must be a direct bullet in the `Provider Identity` section. The decoder extracts only the slug and validates it with the current `Task key` pattern. It does not read a full source key, fingerprint, import document, delta document, comment, attachment or other prose as identity.

Exactly one accepted key line must exist. Reject no accepted line, more than one key line even when values match, conflicting values, an invalid slug, a key label outside its exact accepted position, or any malformed accepted form. Rejection stops synchronization before mutation. Migration rewrites an eligible mutable card to the current final-line form. Historical decoding never authorizes terminal-card mutation and never becomes an emitted schema, issue map or general compatibility parser.

## Implementation Delivery

Every `task:implementation` card contains one `## Delivery` section. Its first field is exactly one of:

```markdown
## Delivery

* Kind: `code`
* Repository: `<canonical-origin>`
  * Publication: `pull-request`
  * Base branch: `<base-branch>`
  * Merge method: `<merge|squash|rebase>`
```

Repeat the ordered repository block for each pull-request repository. Every code delivery names at least one pull-request repository and includes its base branch and merge method.

When root `DESIGN.md` explicitly authorizes a main-only publication boundary, add this second block to the same ordered `Delivery` section:

```markdown
* Direct-main publication:
  * Repository: `<canonical-origin>`
  * Branch: `main`
  * Paths: `<complete-owned-path-list>`
  * Contract: `DESIGN.md`, section `<exact-heading>`
```

This block declares a non-PR result and is not a task workspace, task branch or PR candidate. Root `DESIGN.md` owns its sequencing, concurrency, review and recovery policy.

```markdown
## Delivery

* Kind: `evidence`
```

Evidence delivery may name the required external result when that aids execution. It omits repository, base branch and merge method fields because they are inapplicable. The provider reconstructs the exact implementation role/delivery pair from the native role label and this closed card field.

## Optional Sections

Add `Constraints`, `Verification` or `Source` only when that section helps execute or verify this task.

- `Constraints` contains only task-specific boundaries that are not already owned by a referenced contract.
- `Verification` contains observable checks that this task must run or obtain.
- `Source` names useful canonical owners and the synchronized Git commit. It does not contain a content fingerprint.

Put a consumed non-standard resource declaration in `Required work` or `Delivery`. Its visible fields are the natural owner identity, lifetime and provider-owned cleanup-handler key; shell text, direct cleanup argv and approval fingerprints are not card fields.

## Native Linear Ownership

Do not copy native operational state into the card:

- role and dispatch labels;
- assignee or delegate;
- status;
- blocker relations;
- comments and attachments;
- handoffs and evidence links.

The provider reads those values from Linear. Git and GitHub remain the owners of branches, commits, pull requests, checks, reviews and merges.

The role label uniquely determines delivery for review, acceptance, cleanup and human tasks. Only implementation has two allowed kinds, so its exact card field is required durable task state.

## Excluded Bookkeeping

Do not render a provider identity block, source fingerprint, full source key, graph or delta identity, schema version, transaction document reference, receipt, candidate fingerprint or empty evidence section.

The title, first paragraph, required work and completion criteria must stand alone for a fresh task thread.

Root `DESIGN.md`, section `Goal Brainstorm И Linear Task Workflow`, owns execution trust and validation policy.
