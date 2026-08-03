# Repository Guidelines

## Table Of Contents

- [Required Standards](#required-standards)
- [Key Directory Map](#key-directory-map)
- [Project Contract](#project-contract)
- [Required Workflows](#required-workflows)
- [Commands](#commands)

## Required Standards

- `project-standards:aws-cloudformation-developer`
- `project-standards:docker-compose-developer`
- `project-standards:http-api-client-developer`
- `project-standards:kubernetes-developer`
- `project-standards:legacy-python-maintainer`
- `project-standards:project-documentation-developer`
- `project-standards:project-foundation`
- `project-standards:project-instruction-developer`
- `project-standards:project-standard-audit`
- `project-standards:pytest-developer`
- `project-standards:python-cli-developer`
- `project-standards:python-developer`
- `project-standards:python-logging-developer`
- `project-standards:python-retry-developer`
- `project-standards:react-ui-developer`
- `project-standards:rest-api-server-developer`
- `project-standards:runtime-config-developer`
- `project-standards:sqlalchemy-developer`
- `project-standards:submodule-developer`
- `project-standards:typescript-developer`
- `project-standards:zitadel-developer`

If one required provider skill is unavailable, continue read-only discovery only and do not mutate this repository until the provider is restored.

## Key Directory Map

```text
agent-plugins/
  .agents/
    plugins/
      marketplace.json
  DESIGN.md
  .gitignore
  plugins/
    agent-workflows/
    marketplace-agent-tools/
    workflow-container-agent-tools/
  README.md
  skill_behavior_eval/
    corpus-v1.json
  test/
  .worktree/
  worktree-bootstrap.yaml
```

- `.agents/plugins/marketplace.json` owns marketplace discovery and the installable plugin catalog for this repository.
- `DESIGN.md` owns the stable provider architecture and cross-project artifact model.
- `.gitignore` owns tracked repository-local ignore behavior.
- `plugins/agent-workflows/` owns generic task workflows.
- `plugins/marketplace-agent-tools/` owns reusable marketplace-domain agent procedures.
- `plugins/workflow-container-agent-tools/` owns reusable workflow-container agent procedures.
- `README.md` owns user-facing repository documentation.
- `skill_behavior_eval/corpus-v1.json` owns versioned activation and semantic output scenarios for this repository's providers; the shared model runner remains owned by `project-standards:project-instruction-developer`.
- `test/` owns repository-level provider tests.
- `.worktree/` is the task-worktree container whose reusable semantics are owned by `agent-workflows:goal-brainstorm`.
- `worktree-bootstrap.yaml` binds this repository's bootstrap resources to the reusable manifest contract owned by `agent-workflows:goal-brainstorm`; task artifacts themselves live only in `project-goals`.

## Project Contract

- This repository is the canonical Codex marketplace source `agent-plugins`.
- This repository is not a runtime dependency of application or workflow-container code.
- Product-specific logic, configuration, prompts, validators, and data remain in their owning application repositories.
- The repository exposes no Python distribution or project-discovery CLI.

## Required Workflows

- `agent-workflows:git-commit` applies when repository changes are committed or pushed.
- `agent-workflows:goal-brainstorm` applies when stable design or a persistent implementation goal is prepared.
- `agent-workflows:goal-checkpoint` applies when an active goal or its non-destructive merge fix-forward publishes a cross-repository closing-commit snapshot.
- `agent-workflows:goal-delete` applies when the user explicitly requests idempotent cleanup of one exact task while retaining its goal registry record.
- `agent-workflows:goal-merge` applies in one exclusive thread when one published checkpoint is merged and accepted on the primary environment.
- `agent-workflows:instruction-migration` applies only to explicitly approved multi-owner instruction migrations.
- `marketplace-agent-tools:ozon-seller-api-developer` applies to its owned marketplace-domain skill.
- `workflow-container-agent-tools:workflow-container-developer` applies to workflow-container plugin content.

## Commands

- Validate each plugin with `python ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py <plugin-root>`.
- Validate each skill with `python ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py <skill-root>`.
- Format changed Python with `black --target-version py314 --line-length 120 <python-scope>`.
- Run provider tests with `pytest -q`.
- Validate or run `skill_behavior_eval/corpus-v1.json` with the shared `project-standards:project-instruction-developer` behavior-eval runner; keep this model phase separate from `pytest`.
