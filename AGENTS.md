# Repository Guidelines

## Table Of Contents

- [Required Standards](#required-standards)
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

## Project Contract

- This repository is the canonical Codex marketplace source `agent-plugins`.
- Generic task workflows live only under `plugins/agent-workflows/`.
- Reusable marketplace-domain agent procedures live only under `plugins/marketplace-agent-tools/`.
- Reusable workflow-container agent procedures live only under `plugins/workflow-container-agent-tools/`.
- This repository is not a runtime dependency of application or workflow-container code.
- Product-specific logic, configuration, prompts, validators, and data remain in their owning application repositories.
- Versioned activation and semantic output scenarios for these providers live under `skill_behavior_eval/`; the shared model runner remains owned by `project-standards:project-instruction-developer`.
- `DESIGN.md` owns the stable provider architecture and cross-project artifact model.
- The repository exposes no Python distribution or project-discovery CLI.

## Required Workflows

- `agent-workflows:instruction-migration` applies only to explicitly approved multi-owner instruction migrations.
- `agent-workflows:git-commit` applies when repository changes are committed or pushed.
- `agent-workflows:goal-brainstorm` applies when stable design or a persistent implementation goal is prepared.
- `marketplace-agent-tools:ozon-seller-api-developer` applies to its owned marketplace-domain skill.
- `workflow-container-agent-tools:workflow-container-developer` applies to workflow-container plugin content.

## Commands

- Validate each plugin with `python ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py <plugin-root>`.
- Validate each skill with `python ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py <skill-root>`.
- Format changed Python with `black --target-version py314 --line-length 120 <python-scope>`.
- Run provider tests with `pytest -q`.
- Validate or run `skill_behavior_eval/corpus-v1.json` with the shared `project-standards:project-instruction-developer` behavior-eval runner; keep this model phase separate from `pytest`.
