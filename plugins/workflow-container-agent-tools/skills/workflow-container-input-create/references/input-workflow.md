# Complete Workflow Input Procedure

## Required Inputs

Resolve these values before editing data:

| Value | Required contract |
| --- | --- |
| Workflow source root | Directory containing the selected `workflow.yaml` and `versions.yaml`. |
| Target version | Exact SemVer selected by the user or by the saved Workflow source version. |
| Target schema | File at `source_root / WorkflowDefinition.input_schema_path`. |
| Destination | File that will receive one complete JSON object. |
| Existing input | Required for WorkflowRun revision and migration. |
| Existing version and schema | Required when migration starts from an older contract. |

Load `workflow.yaml` with `WorkflowDefinition.from_path(...)`, `versions.yaml` with `WorkflowVersionDefinition.from_path(...)`, and schemas with `WorkflowInputSchema.from_path(...)`. Do not reimplement their validation or migration graph logic.

## Public Object

The complete value has exactly the root objects `request` and `config`. `request` owns the requested domain work. `config` owns the complete run settings, including the workflow instruction and the closed typed `step_map`. The schema is the field and default owner; the skill does not add fallback values.

## Mode Selection

### New Workflow

Materialize every schema default into one new working object. Ask for each remaining required value and every optional user choice. Do not write until the complete object validates.

### WorkflowRun

Copy the saved complete Workflow input into memory. Ask which values should differ for this run, one field at a time, and update that complete object. Never treat a user-supplied partial object as a machine patch or generic recursive merge.

### Migration

Require the exact source version, target version, unchanged complete source input, and source schema. Validate and migrate the unchanged source input before applying any desired changes. Desired partial values belong only to the complete target object after migration.

1. Validate the unchanged complete source input with its exact source `WorkflowInputSchema`. If validation fails, run no script and leave the destination unchanged.
2. Load declared edges from `WorkflowVersionDefinition.input_migration_list`.
3. Resolve one path with `workflow_input_migration_path_list_get(...)`.
4. For each edge, resolve `script_path` beneath the workflow source root and require the source-owned file to be executable.
5. Execute it from the workflow source root. Send only the current complete JSON object to `stdin`. Capture `stdout` and `stderr` separately.
6. Reject a non-zero exit, invalid JSON, a non-object JSON value, undeclared side effects, or output that cannot continue through the declared chain. Do not write any intermediate result to the destination.
7. Validate the migrated object with the target `WorkflowInputSchema`.
8. Apply confirmed user changes field by field to that complete target object and validate the resulting complete object again.

Migration scripts are deterministic transforms. They receive no prompt answers, network state, marketplace state, or destination path. Diagnostics from `stderr` may be shown to the user but never become input data.

## Interactive Decisions

Ask one short question at a time. Prefer schema titles, descriptions, enum choices, numeric limits, and current values. For nested objects and arrays, decide the owning collection first and then each item. A user may paste a complete candidate object; validate and replace the working object only when that candidate is complete. A partial candidate remains conversational intent and must be resolved field by field.

After every answer, keep the working value in the schema's canonical types. Do not coerce ambiguous strings, infer an unlisted enum, fabricate step configs, or add config entries for steps without settings.

## Validation And Write

Validate through `WorkflowInputSchema.input_validate(...)`. Report each failure with its JSON path, expected constraint, and current value. Continue the dialogue instead of weakening the schema.

When validation succeeds, show the final object and destination. If the destination exists, ask explicitly before replacement. Write to a sibling temporary file, flush it, and replace the destination atomically. Remove the temporary file after failure. The existing destination must remain byte-for-byte unchanged unless the atomic replacement succeeds.

## Completion Report

Report the selected source and target versions, schema path, destination path, whether migration ran, and the declared edges executed. State that no workflow was launched and no marketplace state was changed.
