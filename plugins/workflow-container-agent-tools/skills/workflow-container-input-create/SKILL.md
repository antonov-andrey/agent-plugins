---
name: workflow-container-input-create
description: Use when preparing, revising, validating, or exporting complete current workflow-container input JSON for Workflow or WorkflowRun.
---

# Workflow Container Input Create

Create exactly one complete input object that conforms to the selected workflow source schema. Never launch the workflow or mutate marketplace state.

**REQUIRED REFERENCE:** Read `references/input-workflow.md` before changing an input.

## Workflow

1. Resolve the workflow source root, target version, `workflow.yaml`, `versions.yaml`, and the schema named by `WorkflowDefinition.input_schema_path`. Load source contracts through `workflow-container-contract`.
2. Select one mode from known inputs:
   - new Workflow: start from target schema defaults;
   - WorkflowRun: start from the saved complete `Workflow.workflow_input_json`;
3. Ask one question at a time in the user's language. Show the current value and schema constraints for the field being decided.
4. Keep one complete working object. A partial JSON supplied in conversation is user intent, not a patch protocol: do not recursively merge it, write it, or pass it to validation as the workflow input. Apply confirmed field decisions to the complete working object.
5. Validate the whole object after each decision that can affect another field. Explain validation failures by JSON path and continue until the complete object is valid.
6. Show the destination and final complete object, obtain overwrite confirmation when the destination exists, then write atomically.

## Terminal Rules

- Write only one schema-valid complete replacement.
- Reject an input from another source version instead of migrating or heuristically copying it. Offer a new current-version input as a separate operation.
- Leave the existing file unchanged after any error or cancellation.
- Do not emit a patch, merge recipe, hidden default, or second settings object.
- Do not start a WorkflowRun, call marketplace mutation APIs, or modify the source schema.
