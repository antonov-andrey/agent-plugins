---
name: workflow-container-audit
description: Audit workflow-container contracts, prompts, persistence, recovery, and cross-runtime ownership against project design.
---

# Workflow Container Audit

Produce a read-only semantic audit. This skill does not wrap a static checker and does not own source, runtime, platform, browser, VPN, or concrete-workflow requirements.

## Required Owners

Read completely:

- `workflow-container-developer/SKILL.md`;
- the target repository `AGENTS.md` chain;
- every applicable owning `DESIGN.md` and routed `design/**` document identified by the developer skill;
- the current instruction-bearing artifacts, code boundaries, schemas, prompts, tests, and configuration in the requested scope.

Fail closed when an affected owner design is unavailable or two owners conflict.

## Audit Workflow

1. Normalize the exact repository and artifact scope.
2. Build the complete owner closure from current boundaries before reading checker output, prior findings, or an implementation plan.
3. Derive every applicable requirement from those owners and assign it exactly once. Record an explicit reason for every owner category that is not applicable.
4. Review the current implementation and instruction artifacts against each requirement with direct path and behavior evidence.
5. Cover these dimensions when their owner is applicable:
   - source interface, versioning, complete public input, migrations, result envelope, and control payloads;
   - dependency direction, runtime class ownership, DBOS checkpoint boundaries, concurrency, and capability transport;
   - instance identity, standard files, artifacts, atomic publication, incremental state, retries, recovery, and replay;
   - prompt ownership, phase routing, action and verifier consistency, mechanical validation, and semantic verification;
   - platform build, conformance tests, scheduling, replacement fencing, Data safepoints, finalization, and cleanup;
   - browser profiles, proxy lookup, VPN gateway lifecycle, DNS, secrets, privilege, and fail-closed behavior;
   - concrete workflow domain topology, validators, result semantics, and end-to-end acceptance.
6. Check for duplicate semantic owners, mirrored payloads, hidden defaults, copied configs, compatibility bridges, and abstractions that do not simplify one real boundary.
7. Compare completed coverage with the independently derived owner inventory. Missing, duplicated, checker-derived, or assumption-only coverage forbids a clean audit.

The dimension list routes review; it is not a substitute checklist and must not introduce requirements absent from the current owning designs.

## Finding Contract

Return each finding in exactly this shape:

```text
- <High|Medium|Low>: <exact artifact path, role, current defect, and concrete impact>
  Fix: <one exact owner-local correction and recheck target>
```

Use `High` for incorrect execution, unsafe recovery, inconsistent durable data, security failure, or an impossible public interface. Use `Medium` for competing owners, ambiguous transitions, duplicated contracts, or materially incomplete verification. Use `Low` only for a deterministic clarity or maintenance defect.

Every finding must identify the current fragment or behavior, affected owner requirement, failure mode, exact correction, and expected property after recheck. Vague requests to clarify, improve, tighten, or refactor are forbidden.

A clean result requires complete current coverage and no findings. Mechanical checks may be reported as separate evidence but never define, narrow, seed, or close semantic review.
