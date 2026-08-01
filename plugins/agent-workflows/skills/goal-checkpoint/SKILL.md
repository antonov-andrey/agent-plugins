---
name: goal-checkpoint
description: Publish an explicitly approved complete cross-repository closing-commit checkpoint for one active tracked goal after committing and pushing every implementation participant.
---

# Goal Checkpoint

Use `agent-workflows:git-commit` first to create and push logical closing commits in every participating task worktree. Require the user's explicit approval of this checkpoint.

Run `scripts/checkpoint.py` with the canonical `project-goals` root, exact common prefix, and every participating implementation task root. The command validates clean exact task branches, origin equality, full commit identities, origin-main ancestry, prior-checkpoint ancestry, and the unchanged complete participant set. It then appends one full snapshot through the serialized direct-main coordination transaction.

Never rebase, reset, force-push, infer participants from the workspace, include `project-goals` in the snapshot, or publish a partial project set. Report the checkpoint id, coordination commit, and exact implementation commits.
