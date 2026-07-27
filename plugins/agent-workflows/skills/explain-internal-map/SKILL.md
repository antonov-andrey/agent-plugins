---
name: explain-internal-map
description: Map current internal dependencies, calls, component interactions, and workflow relationships.
---

# Explain Internal Map

## Goal
Draw a runtime interaction map for the requested current code.

## Rules
- Follow the `agent-workflows` plugin support owner `lib/explain-skill/protocol.md`.
- Show one fenced `text` ASCII runtime map where the top-level owner, workflow owner, boundary owners, external dependencies, and persistence or API sinks are visible at a glance.
- Include participants that materially own workflow state, data handoff, external boundaries, persistence, or important decisions.
- Use one map line per participant or material interaction.
- Start the map with the requested entrypoint, script, package, module, class, or workflow and `tx=-`.
- Use `-> <participant> tx=<label>` for direct control flow or ownership handoff.
- Use `-> <operation> -> <boundary> tx=<label>` for reads, writes, API calls, file access, queues, subprocesses, and other boundary interactions.
- Indent nested interactions under the participant that owns them.
- Use `tx=-` for non-transactional work, `tx=TX1` for the primary DB session, and short descriptive transaction labels such as `tx=TX-batch`, `tx=TX-final`, or `tx=bootstrap` when the code has separate bounded transaction or bootstrap phases.

## Output Shape
- Then provide exactly one fenced `text` ASCII runtime map.
