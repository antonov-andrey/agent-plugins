---
name: workflow-container-developer
description: Develop or refactor workflow-container sources, shared runtime packages, platform integration, browser or VPN capabilities, DBOS steps, prompts, and artifacts.
---

# Workflow Container Developer

Treat this skill as an owner router and implementation procedure. Stable source, runtime, platform, browser, VPN, and concrete-workflow contracts remain in the owning project designs.

## Owner Closure

Read the target repository `AGENTS.md` chain and its `DESIGN.md` before changing anything. Add only the designs required by the affected boundary:

- source declarations, input schema, migrations, immutable run context, control payloads, and the open result envelope: `workflow-container-contract/DESIGN.md`;
- reusable workflow, step, Codex, prompt, artifact, persistence, verification, and recovery implementation: `workflow-container-runtime/DESIGN.md`;
- image import, build, post-build conformance, scheduling, control service, replacement execution, Data acceptance, and capability orchestration: `workflow-control-center/DESIGN.md` plus the applicable document under `workflow-control-center/design/`;
- browser process, Playwright MCP routing, physical profiles, and browser security: `browser-runtime/DESIGN.md`;
- VPN protocol validation, gateway lifecycle, SOCKS5, DNS, fencing, and leak prevention: `vpn-runtime/DESIGN.md`;
- one concrete workflow's domain input, DBOS topology, prompts, validators, artifacts, and acceptance scenarios: that workflow project's `DESIGN.md` and applicable source/runtime designs above.

When one change crosses owners, read every affected owner design before editing. Do not reconstruct a missing contract from this skill, a neighboring repository, chat history, or an example project.

## Implementation Workflow

1. Classify the requested behavior into the exact owner closure above.
2. Inspect current code, tests, source declarations, schemas, prompts, and runtime wiring at those boundaries.
3. Resolve any contradiction between owners before mutation; one semantic contract must have one canonical owner.
4. Implement the final owner-local state and migrate all direct consumers in the same change. Do not leave aliases, forwarding wrappers, duplicated schema text, copied configuration, or transition-only compatibility layers.
5. Update stable design only when the working contract changes. Keep concrete product and domain semantics out of shared runtime and plugin content.
6. Verify each changed repository with its own `AGENTS.md` commands and direct behavior coverage. A platform integration change must also verify the complete affected cross-repository path.
7. Reread the complete affected owner closure after the last fix. Any finding returns to the owning implementation and invalidates the previous semantic pass.

## Boundaries

- A concrete workflow may depend at runtime on `workflow-container-contract` and optionally `workflow-container-runtime`; it must never depend on this plugin.
- The shared base image and shared runtime package are optional implementations of the platform interface, not requirements for third-party images.
- Generic mechanics move to their runtime owner only when they are truly source-neutral. Concrete DBOS topology, data meaning, prompts, validators, and result semantics stay with the concrete workflow.
- Browser and VPN runtimes remain independent optional capabilities. Workflow code receives only typed run-local capability values and never controls their provider internals.
- Use `workflow-container-audit` only for an explicitly requested structured semantic audit; ordinary implementation still requires the direct post-fix semantic pass in step 7.
