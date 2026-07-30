# Complete Workflow Input Procedure

## Required Inputs

Resolve these values before editing data:

| Value | Required contract |
| --- | --- |
| Workflow source root | Directory containing the selected `workflow.yaml` and `versions.yaml`. |
| Target version | Exact SemVer selected by the user or by the saved Workflow source version. |
| Target schema | File at `source_root / WorkflowDefinition.input_schema_path`. |
| Destination | File that will receive one complete JSON object. |
| Existing input | Required for WorkflowRun revision. |
| Existing version | Must equal the selected current source version. |

Load `workflow.yaml` with `WorkflowDefinition.from_path(...)`, `versions.yaml` with `WorkflowVersionDefinition.from_path(...)`, and schemas with `WorkflowInputSchema.from_path(...)`. Do not reimplement their validation.

## Public Object

The complete value has exactly the root objects `request` and `config`. `request` owns the requested domain work. `config` owns the complete run settings, including the workflow instruction and the closed typed `step_map`. The schema is the field and default owner; the skill does not add fallback values.

## Mode Selection

### New Workflow

Materialize every schema default into one new working object. Ask for each remaining required value and every optional user choice. Do not write until the complete object validates.

### WorkflowRun

Copy the saved complete Workflow input into memory. Ask which values should differ for this run, one field at a time, and update that complete object. Never treat a user-supplied partial object as a machine patch or generic recursive merge.

### Version Mismatch

The selected source version and the version owning an existing input must match exactly. Do not migrate, recursively merge, infer field correspondence, or execute source scripts. Leave the destination unchanged and offer creation of a separate new input from current schema defaults.

## Interactive Decisions

Ask one short question at a time. Prefer schema titles, descriptions, enum choices, numeric limits, and current values. For nested objects and arrays, decide the owning collection first and then each item. A user may paste a complete candidate object; validate and replace the working object only when that candidate is complete. A partial candidate remains conversational intent and must be resolved field by field.

After every answer, keep the working value in the schema's canonical types. Do not coerce ambiguous strings, infer an unlisted enum, fabricate step configs, or add config entries for steps without settings.

## Validation And Write

Validate through `WorkflowInputSchema.input_validate(...)`. Report each failure with its JSON path, expected constraint, and current value. Continue the dialogue instead of weakening the schema.

When validation succeeds, show the final object and destination. If the destination exists, ask explicitly before replacement. Write to a sibling temporary file, flush it, and replace the destination atomically. Remove the temporary file after failure. The existing destination must remain byte-for-byte unchanged unless the atomic replacement succeeds.

## Completion Report

Report the selected current source version, schema path, and destination path. State that no workflow was launched and no marketplace state was changed.
