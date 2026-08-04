# Agent Plugins

`agent-plugins` is a Codex marketplace repository with four independently installable providers:

- `agent-workflows` for reusable task workflows and orchestration;
- `linear-agent-tools` for source-independent Linear task graphs and task execution;
- `marketplace-agent-tools` for explicitly shared marketplace-domain procedures;
- `workflow-container-agent-tools` for workflow-container authoring, audit, and input preparation.

Application-specific logic and reusable opinionated engineering standards are not owned here. Standards are provided separately by `project-standards`.

## Local Plugin Install

Run from the `agent-plugins` checkout root:

```bash
cd <agent-plugins-checkout>
codex plugin marketplace add .
codex plugin add agent-workflows@agent-plugins
codex plugin add linear-agent-tools@agent-plugins
codex plugin add marketplace-agent-tools@agent-plugins
codex plugin add workflow-container-agent-tools@agent-plugins
```

Start a new Codex thread after installing or reinstalling the plugin.

## GitHub Plugin Install

```bash
codex plugin marketplace add antonov-andrey/agent-plugins --ref main
codex plugin add agent-workflows@agent-plugins
codex plugin add linear-agent-tools@agent-plugins
codex plugin add marketplace-agent-tools@agent-plugins
codex plugin add workflow-container-agent-tools@agent-plugins
```

## Update

For a GitHub marketplace source:

```bash
codex plugin marketplace upgrade agent-plugins
codex plugin add agent-workflows@agent-plugins
codex plugin add linear-agent-tools@agent-plugins
codex plugin add marketplace-agent-tools@agent-plugins
codex plugin add workflow-container-agent-tools@agent-plugins
```

For local development, update the affected plugin cachebuster, then reinstall that plugin:

```bash
python3 ~/.codex/skills/.system/plugin-creator/scripts/update_plugin_cachebuster.py \
  plugins/linear-agent-tools
codex plugin add linear-agent-tools@agent-plugins
```

Start a new Codex thread after reinstalling.

## Manual Linear Workflow

1. Run `linear-agent-tools:workflow-configure` for the exact workspace and team.
2. Run `linear-agent-tools:task-graph-create` for an agreed source and approve the complete preview before publication.
3. Open a fresh thread for one ready issue and invoke its role skill: `task-implement`, `task-review`, `task-accept`, `task-merge`, or `task-cleanup`.
4. At `Human Review`, approve the exact fingerprint into `Merging`/`Done`, request `Rework`, or cancel the issue.
5. Keep the resulting `Completed` or `Canceled` Linear Project as history after exact cleanup.

A Linear Project is the task container for one agreed source outcome, not a mirror of one Git repository. Its issues may reference different repositories, and the same repository may participate in multiple independent Linear Projects.

## Development

```bash
pytest -q
python ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/agent-workflows
python ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/linear-agent-tools
python ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/marketplace-agent-tools
python ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/workflow-container-agent-tools
```
