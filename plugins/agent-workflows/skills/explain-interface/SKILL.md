---
name: explain-interface
description: Explain how current code interacts with external systems, APIs, databases, files, CLI, and configuration.
---

# Explain Interface

## Goal
Explain the interaction interfaces of the requested project code from current repository evidence.

## Rules
- Follow the `agent-workflows` plugin support owner `lib/explain-skill/protocol.md`.
- Cover the stable interaction boundaries that matter to callers or operators: CLI, config files, DB tables, ORM models, external APIs, local files, subprocesses, caches, submodules, and generated artifacts.
- For each boundary, describe what is read, what is written, which side owns the contract, what keys or matching rules identify objects, what selectors or predicates bound the scope, and what safety gate applies.
- Do not merge separate DB tables, files, endpoints, subprocess channels, queues, caches, or generated artifacts into one interface entry when they have different keys, matching rules, selectors, lifecycle behavior, or failure behavior.
- DB interface entries must include exact table names, ORM classes, natural or persisted keys used by the inspected code, join or lookup predicates, read filters, write/update predicates, lifecycle semantics, and transaction or commit owner when present.
- CLI interface entries must include exact flags, defaults when contract-relevant, validation rules, selector semantics, and runtime routing effects such as `--test`.
- External API entries must include exact API names or endpoints, request direction, request identity keys, response entities consumed, mutation gates, and failure behavior.
- Local file or artifact entries must include exact path, path template, or glob, read/write mode, generated-artifact lifecycle, and missing-file behavior when code defines it.
- Process, subprocess, queue, and cache entries must include channel names, message or key object types, ownership, lifecycle, and failure behavior.
- Do not include internal helper call chains unless they are themselves a boundary.
- For one actual interface, output only fields that add applicable information; never print placeholder fields whose content would be equivalent to `not applicable`, `none`, `absent`, or `not used`.

## Output Shape
- Group actual interfaces by boundary type, and omit empty boundary groups.
- Render each interface as one compact multi-line block: one title line followed immediately by consecutive field lines.
- Separate boundary groups and interface blocks with one blank line; keep field lines inside the same block adjacent with no blank lines between them.
- Keep one short field value on the same line as its label. Split one field into continuation lines only when it contains several distinct keys, selectors, predicates, status values, or safety conditions that are hard to read inline.
- For each interface, output one compact repeatable fielded block with localized labels selected only from applicable semantic fields:
  - interface name,
  - direction,
  - source or sink,
  - payload or entity,
  - keys and matching rules,
  - selectors and scope,
  - writes and lifecycle,
  - safety and failure behavior,
  - owner,
  - evidence.
- Every interface block must include evidence, and must include every other applicable field from the list above when the inspected code defines meaningful data for that field.
- Evidence must use root-relative paths plus the class, function, method, table, endpoint, flag, or field names that support the interface claim.
