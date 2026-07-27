---
name: explain-persistence
description: Explain current database and file effects, lifecycle, upserts, snapshots, and generated artifacts.
---

# Explain Persistence

## Goal
Explain current persistence and side effects for the requested code from actual repository implementation.

## Rules
- Follow the `agent-workflows` plugin support owner `lib/explain-skill/protocol.md`.
- Cover DB tables, ORM models, transaction boundaries, upsert/delete/stale-mark behavior, file reads/writes, generated artifacts, external mutating API calls, and idempotency where relevant.
- Distinguish dry-run behavior from apply/write behavior.
- State when a side effect is scoped by selectors such as `--test`, `--limit`, `--shop-id`, `--period`, `--force`, or `--apply`.

## Output Shape
- Group side effects by storage or external system.
- Render each side effect as one compact multi-line block: one title line followed immediately by consecutive field lines.
- Separate storage groups and side-effect blocks with one blank line; keep field lines inside the same block adjacent with no blank lines between them.
- Keep one short field value on the same line as its label. Split one field into continuation lines only when it contains several distinct selectors, predicates, lifecycle updates, or safety conditions that are hard to read inline.
- For each side effect, state read/write direction, scope, lifecycle rule, and safety gate.
