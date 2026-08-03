---
name: goal-checkpoint
description: Create, validate, and publish an explicitly approved complete cross-repository closing-commit checkpoint for one active tracked goal.
---

# Goal Checkpoint

Require the user's explicit approval of this checkpoint. First use `agent-workflows:git-commit` to create and push the logical closing commits for the complete recorded implementation participant set. Keep that publication a distinct subworkflow: `goal-checkpoint` does not implement Git commit policy itself and must not append metadata until every participant is clean and its exact task ref is fully pushed.

Run `scripts/checkpoint.py` with the canonical `project-goals` root, exact common prefix, and every participating implementation task root. Before reading commits, the command recovers any known private-state transaction and validates the active coordination artifacts, every private replica, manifest and resource binding, cleanup receipt, and worktree identity. For every participant it separately proves that the exact task HEAD remains a descendant of its recorded preparation baseline; this forbids a rewritten task history even when no prior checkpoint exists. Main may already contain a prior selected checkpoint during fix-forward, so the operation additionally proves main compatibility through fetched exact `origin/main` ancestry of every new task commit instead of requiring preparation-time main identity. It then validates clean exact task branches, origin equality, full commit identities, prior-checkpoint ancestry, and the unchanged complete participant set before appending one full snapshot through the serialized direct-main coordination transaction.

Whether executing or explaining this operation, state the persisted boundary explicitly: the new checkpoint contains the complete unchanged participant set sorted by `project_path`, and every item is the exact pair `project_path` plus full `git_commit_final`. Do not rely on the script name to imply these guarantees.

Never rebase, reset, force-push, infer participants from the workspace, include `project-goals` in the snapshot, or publish a partial project set. Report the checkpoint id, coordination commit, and exact implementation commits.
