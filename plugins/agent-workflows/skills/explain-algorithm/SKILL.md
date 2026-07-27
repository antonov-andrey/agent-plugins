---
name: explain-algorithm
description: Explain current algorithms, workflows, run flow, selection logic, and step order from actual project code.
---

# Explain Algorithm

## Goal
Explain the requested algorithm from current repository code in a way a human can read before changing or operating that code.

## Rules
- Follow the `agent-workflows` plugin support owner `lib/explain-skill/protocol.md`.
- Include object-selection rules, DB filters, ordering, deduplication, batching, external calls, side effects, and stop/error conditions when they are part of the algorithm.
- Do not dump implementation details that do not change how the algorithm behaves.

## Output Shape
- Then give a compact ordered algorithm.
- Add a short section for selection/filtering rules when the algorithm chooses objects from DB, files, queues, APIs, or in-memory collections.
