# Section Audit Protocol

This file owns shared section-agent orchestration, artifact contracts, validators, and deterministic merge for structured audit skills that split one checklist into top-level section tasks. Each skill owns its scope derivation, applicable owner selection, checklist text, and final handoff.

## Required Skill Inputs
- One `audit_name` used as the run directory prefix under `tmp/<audit_name>/<run_uuid>/`.
- One checklist heading and one ordered top-level checklist-section list.
- One task-result path family `tmp/<audit_name>/<run_uuid>/<section_slug>.<agent_id>.result.md`.
- The shared task-result validator `lib/section-audit/tool/audit_section_result_check.py`.
- One final report path family under `tmp/`.
- The shared merger `lib/section-audit/tool/audit_report_merge.py` and report validator `lib/section-audit/tool/audit_report_check.py`.

## Run State
- Create one `run_uuid` with `uuidgen`.
- Create `tmp/<audit_name>/<run_uuid>/agent.json` before assigning work.
- Use the `agent-workflows` plugin support owner `lib/subagent-transport/protocol.md` in `agent_pool` mode.
- `agent.json` MUST be a flat JSON object whose keys are current active `agent_id` values.
- `agent.json` values are workflow-local metadata owned by this protocol; each current value MUST include the current `section_slug`.
- `section_slug` MUST be the stable snake_case form of the literal top-level checklist-section heading.

## Section Tasks
- Create exactly one section task per top-level checklist section in canonical checklist order.
- Each section task MUST include the literal top-level checklist-section heading and every checklist item that belongs to that section in canonical order.
- Each section task MUST include every skill-required checklist-card metadata field literally, without parent-local paraphrase or weakening.
- When one owning skill requires exhaustive semantic coverage, checklist items MUST be derived from the complete canonical owner requirement inventory before findings or mechanical output are inspected. Mechanical check identities, signals, historical findings, and implementation-plan items MUST NOT define or limit that inventory.
- Each section task MUST include the resolved scope entries, reviewed files, user focus notes when present, and exact required task-result path pattern.
- Different top-level checklist sections MUST NOT be merged into one task, and one top-level checklist section MUST NOT be split across multiple tasks.

## Section Agents
- The default section-agent profile is one dedicated default sub-agent per top-level checklist section with `fork_context=false`.
- The owning skill MAY declare a named project agent or another runtime-supported agent creation profile for section agents.
- Send all section tasks in parallel.
- If one required section sub-agent cannot be created through the selected profile, the owning skill MUST fail explicitly.
- Corrective feedback for one invalid section-task result MUST go to the same section agent while `lib/subagent-transport/protocol.md` keeps that agent current.

## Result Handling
- Every section-task result MUST contain one `## Requirement Results` block with exactly one canonical-order entry for every requirement assigned by the parent:
  - `### <literal assigned requirement>`;
  - `- Status: Satisfied|Problems|Not applicable`;
  - `- Evidence: <current concrete evidence>`;
  - `- Not applicable reason: None|<concrete reason>`.
- Validate every written section-task result with the skill-local task-result validator and pass every assigned literal through repeated `--expected-requirement` arguments before merging it.
- Formal validation MUST NOT semantically re-grade, weaken, or strengthen section-agent findings.
- Formal validation proves artifact structure and exact presence of the parent-supplied requirement literals only. It MUST NOT be reported as semantic validation, semantic completeness, evidence truth, or correctness of one finding disposition.
- The parent MUST merge only validated section-task results into the final report in canonical checklist order.
- Before merge, the parent MUST perform every skill-required coverage comparison against the independently derived owner inventory. Missing or duplicated coverage forbids a clean report even when every artifact validator passes.
- The parent MUST NOT replace missing section-agent findings with locally improvised findings.
- The final merge MUST receive the separately executed mechanical status and concrete command/result evidence. Mechanical output MUST NOT be exposed to semantic section agents before their discovery and confirmation are complete.
- Validate the final report with the skill-local final report validator before returning the report path.
