# Repository Guidelines

## Table Of Contents

- [Required Standards](#required-standards)
- [Key Directory Map](#key-directory-map)
- [Project Contract](#project-contract)
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
- `agent-workflows:git-commit`
- `agent-workflows:goal-brainstorm`
- `agent-workflows:instruction-migration`
- `linear-agent-tools:workflow-configure`
- `linear-agent-tools:task-accept`
- `linear-agent-tools:task-cleanup`
- `linear-agent-tools:task-graph-create`
- `linear-agent-tools:task-implement`
- `linear-agent-tools:task-merge`
- `linear-agent-tools:task-review`
- `marketplace-agent-tools:ozon-seller-api-developer`
- `workflow-container-agent-tools:workflow-container-developer`

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
    linear-agent-tools/
    marketplace-agent-tools/
    workflow-container-agent-tools/
  README.md
  skill_behavior_eval/
    corpus-v1.json
  test/
    agent_workflows/
    linear_agent_tools/
  .worktree/
  worktree-bootstrap.yaml
```

- `.agents/plugins/marketplace.json` owns marketplace discovery and the installable plugin catalog for this repository.
- `DESIGN.md` owns the stable provider architecture and cross-project artifact model.
- `.gitignore` owns tracked repository-local ignore behavior.
- `plugins/agent-workflows/` owns generic task workflows.
- `plugins/linear-agent-tools/` owns source-independent Linear task-graph, execution, review, acceptance, merge, and cleanup procedures.
- `plugins/marketplace-agent-tools/` owns reusable marketplace-domain agent procedures.
- `plugins/workflow-container-agent-tools/` owns reusable workflow-container agent procedures.
- `README.md` owns user-facing repository documentation.
- `skill_behavior_eval/corpus-v1.json` owns versioned activation and semantic output scenarios for this repository's providers; the shared model runner remains owned by `project-standards:project-instruction-developer`.
- `test/` owns every repository-level provider test; suites are grouped first by plugin and then by the exact code owner they verify. Provider-library tests MUST NOT be hidden under arbitrary `plugins/**/lib/**/test/` paths because the shared owner-aware pytest discovery does not recognize those as test owners.
- `.worktree/` is the local Linear task-worktree container whose reusable semantics are owned by `linear-agent-tools:task-implement` and `linear-agent-tools:task-cleanup`.
- `worktree-bootstrap.yaml` binds this repository's local task resources and typed provider cleanup-handler keys to the reusable manifest contract owned by `linear-agent-tools:task-implement` and `linear-agent-tools:task-cleanup`; source artifacts themselves live only in `project-goals` before Linear handoff.

## Project Contract

- This repository is the canonical Codex marketplace source `agent-plugins`.
- This repository is not a runtime dependency of application or workflow-container code.
- Product-specific logic, configuration, prompts, validators, and data remain in their owning application repositories.
- The repository exposes no Python distribution or project-discovery CLI.

## Commands

- Validate each plugin with `python ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py <plugin-root>`.
- Validate each skill with `python ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py <skill-root>`.
- Format changed Python with `black --target-version py314 --line-length 120 <python-scope>`.
- Run provider tests with `pytest -q`.
- Validate or run `skill_behavior_eval/corpus-v1.json` with the shared `project-standards:project-instruction-developer` behavior-eval runner; keep this model phase separate from `pytest`.
