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

For local development, update the affected plugin cachebuster in the candidate, commit it with the plugin change, then reinstall that exact source through the normal marketplace under the operating-system user's standard `HOME` with `CODEX_HOME` unset:

```bash
python3 ~/.codex/skills/.system/plugin-creator/scripts/update_plugin_cachebuster.py \
  plugins/linear-agent-tools
env -u CODEX_HOME codex plugin add linear-agent-tools@agent-plugins
```

During a lifecycle-provider merge, do not run that reinstall against a stale local marketplace source. The retained reviewed `task-merge` provider first runs its fixed `scripts/provider_install.py` boundary, which synchronizes the configured clean base worktree to the exact merged commit and performs the normal install only when needed. Its returned complete prompt is then passed directly to a fresh generic max Codex process with closed stdin and native waiting. The issue does not reach `Done` until the installed manifest/source and expected skill discovery match the returned exact result.

## Manual Linear Workflow

1. Run `linear-agent-tools:workflow-configure` for the exact workspace/team and every selected GitHub repository merge-policy and base-protection boundary.
2. Run `linear-agent-tools:task-graph-create` for an agreed source and approve the complete preview before publication.
3. Open a fresh thread for one ready issue and invoke its role skill: `task-implement`, `task-review`, `task-accept`, `task-merge`, or `task-cleanup`.
4. A separate fresh Codex reviewer moves implementation from `Review` to `Merging`/`Done` on zero findings or to `Rework` on findings. If the candidate changes that provider, use a generic max-reasoning thread against branch-local contracts and the exact diff, not the installed plugin under review.
5. Keep human approval only at final deployed-result acceptance in `Review`, then retain the `Completed` or `Canceled` Linear Project as history after exact cleanup.

A Linear Project is the task container for one agreed source outcome, not a mirror of one Git repository. Its issues may reference different repositories, and the same repository may participate in multiple independent Linear Projects.

## Development

```bash
python -m pip install -r requirements-dev.txt
pytest -q
python ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/agent-workflows
python ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/linear-agent-tools
python ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/marketplace-agent-tools
python ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/workflow-container-agent-tools
```
