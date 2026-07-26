---
name: code-writer
description: Use when the user explicitly requests the `code-writer` skill or a project-local code-writing workflow.
---

# Code Writer

## Goal
Implement the requested change and drive the run to the correct terminal state under `Final Response Contract`.

## When To Use
- Use this skill only when the user explicitly requests the `code-writer` skill or a project-local code-writing workflow.
- Do not trigger this skill only because a task creates, deletes, restructures, or updates code artifacts.

## Canonical Sources
- `AGENTS.md`
- Every applicable provider skill selected in `Required Standards`.

## Inputs
- Any user-supplied scope or focus paths for the run.
- The relevant repository-state change set for the run.
- Any user focus notes about specific risks or required outcomes.
- Any user-required runtime command, test, or verification target for the changed behavior.

## Outputs
- Updated artifacts when the run reaches implementation changes.
- Verification results and evidence for the run.
- One final response that satisfies `Final Response Contract`.

## Final Response Contract
- `Final Response Contract` is the single response contract for the run.
- Follow `Writing And Reporting Rules` and `Language Zones` from `AGENTS.md`.
- `Successful handoff` for this skill means: the requested change is implemented, required in-repository cutover inside the working scope is complete, and every required verification check has passed.
- The final response MUST state the outcome, verification, and unresolved problems or blockers.
- The final response format is otherwise governed by `AGENTS.md` and the current task context.
- Verification reporting MUST name each executed verification command, pass or fail result, and any warning or notable diagnostic.
- Unrun required verification MUST be named with the reason it was not run.

## Code Writing Contract
- This skill owns only code-writing workflow; code-owner semantics remain external to this skill.
- Inspect repository state directly instead of trusting prior conversation state.
- When `AGENTS.md` requires companion skills, read and use them as required.
- Before choosing code shape for a change, choose the owning directory entity and stable owner first.
- When one bounded Python algorithm has ordered stages, apply `project-standards:python-developer/references/script-workflow-owner.md`.
- Favor explicit boundaries, dependency injection, and small dependency surfaces.
- API and signature changes MUST migrate all in-repository call sites in the same change.
- Refactors, renames, and schema migrations MUST follow the governing `Code Refactoring Rules` and `Database Schema Migration Rules` in `AGENTS.md`.
- Python refactor scope rules:
  - when a refactor task touches Python code, declare explicit Python scope for the run,
  - when the run targets one root `Python script` and the user does not explicitly narrow the Python scope, the default Python scope includes that entrypoint and the `Python code` that implements its runtime behavior,
  - every Python file inside the declared Python scope MUST be migrated in the same task to current canonical standards,
  - compatibility-only Python edits outside the declared Python scope MUST NOT expand that scope when both conditions hold:
    - the edit is limited to the minimum needed to keep the in-scope change executable against the new contract,
    - the file does not receive new behavior, refactor work, or standards-migration work beyond that compatibility fix.
- Current-result inspection rules:
  - before leaving the implementation loop for verification, inspect the diff and touched call sites directly,
  - remove dead code, stale imports, obsolete compatibility layers, and partial-cutover leftovers inside the working scope,
  - do not stop at one green mechanical check when the diff still shows contract, ownership, cutover, or dead-code problems.

## Scope Derivation Contract
- Working scope = user-supplied scope if given; otherwise task-discoverable focus paths; otherwise current changed paths in repository state.
- Add any additional scope required by governing rules or by `Successful handoff`.
- Required scope is mandatory and does not by itself require separate scope approval.
- Do not expand scope without a concrete task or instruction reason.
- Compatibility-only edits outside a declared Python refactor scope MUST remain outside that scope.

## Verification Selection Contract
- Build required verification from user-required checks, then owner-required checks, then targeted coverage checks.
- Keep executable verification and semantic verification separate. Executable commands prove only the exact behavior or closed predicate they run.
- Skipped optional checks MAY be reported only when they were explicitly considered during verification selection and intentionally not run.
- Required handoff verification MUST include the ordinary handoff suite from the governing `Evidence And Verification Rules` in `AGENTS.md`.
- Do not add `test/code/**` to ordinary code-writing verification unless the user explicitly requests it, the current task changes code-contract tests or helpers, or a narrower workflow gate explicitly requires it.
- If root `pytest` is skipped, the final response MUST record that skip and the reason in the appropriate verification subsection.
- Do not claim that one narrower targeted check covers behavior it did not actually execute.

## Semantic Acceptance Contract
- Derive one complete applicable requirement inventory independently from the current user request, every governing owner resolved in step 3, and every directly affected stable Product contract.
- Do not derive or narrow that inventory from checker identities, checker output, tests, the implementation plan, the changed-file list, previous findings, or the first apparently successful implementation.
- After the last implementation or documentation fix and successful required executable verification, inspect the complete current working scope against every requirement in that inventory.
- This direct semantic pass does not create a report, checklist file, completion ledger, or other evidence artifact unless the user explicitly requests one.
- Any semantic finding returns the run to the owning implementation, owner-resolution, or scope step. After its fix, rerun affected executable verification and restart the complete semantic pass from owner discovery.
- Successful handoff requires one fresh complete semantic pass after the last fix with no finding and no uncovered requirement.

## Failed Verification Inspection Contract
- `Failed Verification Inspection Contract` applies only when a required verification check fails and yields a failed verification path to inspect.
- If a failed check has a failing test or checker body, inspect that body, its owner-local helpers, fixtures, and adjacent comments or docstrings before changing code.
- Otherwise inspect the command, its output, and any directly relevant project-local implementation, config, or failing artifact.
- If verification is blocked without a failed verification path to inspect, do not apply this contract; classify that state directly under `Blocker Contract`.

## Blocker Contract
- Treat a problem as an external blocker only when safe progress depends on:
  - a required contract change that this skill cannot absorb directly,
  - unavailable external state, such as missing environment variables, credentials, services, artifacts, generated inputs, or dependencies not created by this run,
  - conflicting repository state outside the task that the governing rules in `AGENTS.md` do not allow this run to absorb directly, such as unrelated user-owned worktree changes.
- Terminal outcome classification remains owned by `Final Response Contract`.
- When one required verification check is blocked, report:
  - the blocked check,
  - the exact reason,
  - any narrower checks that did run successfully,
  - whether the remaining risk is local to the changed scope or caused by an external blocker.

## Iteration Cycle Contract
- `Iteration Cycle` is the mandatory execution state machine for every run of this skill.
- Execute this cycle exactly as written.
- Do not skip, merge, reorder, or replace steps with free-form workflow.
- Do not perform work assigned to a later step before the current step sends the run there.
- When one step defines an explicit `go to step N` transition, follow that transition exactly instead of improvising a different return path.
- A run that deviates from this cycle is non-compliant even if the code change itself looks correct.

## Iteration Cycle
1. Inventory the requested scope and the current changed paths in repository state.
   - If repository state reveals one blocker under `Blocker Contract`, go to step 10.
   - Otherwise go to step 2.
2. Derive the working scope for the run under `Scope Derivation Contract`.
   - If `Blocker Contract` classifies one needed contract change that this skill cannot absorb directly, go to step 10.
   - Otherwise go to step 3.
3. Resolve owner, rule, and verification requirements.
   - If that resolution changes the working scope, go to step 2.
   - Otherwise go to step 4.
4. Implement the requested changes in the correct owner roots under `Code Writing Contract`.
   - If implementation reveals unresolved owner, rule, or verification requirements, go to step 3.
   - If implementation shows that `Scope Derivation Contract` requires broader scope, go to step 2.
   - Otherwise go to step 5.
5. Inspect the resulting diff and touched call sites directly under `Code Writing Contract`.
   - If inspection reveals unresolved owner, rule, or verification requirements, go to step 3.
   - If inspection shows that `Scope Derivation Contract` requires broader scope, go to step 2.
   - If inspection shows a confirmed implementation or cutover problem under `Code Writing Contract`, go to step 6.
   - Otherwise go to step 7.
6. Fix the confirmed implementation or cutover problems in the working scope.
   - If fix work reveals unresolved owner, rule, or verification requirements, go to step 3.
   - If fix work shows that `Scope Derivation Contract` requires broader scope, go to step 2.
   - Otherwise go to step 5.
7. Apply current documentation-update requirements.
   - If documentation work reveals unresolved owner, rule, or verification requirements, go to step 3.
   - If documentation work shows that `Scope Derivation Contract` requires broader scope, go to step 2.
   - Otherwise run the verification set required by `Verification Selection Contract`.
   - If every required verification check passes, go to step 9.
   - Otherwise go to step 8.
8. Classify the failed or blocked verification result before any further code change.
   - If one required check failed, apply `Failed Verification Inspection Contract`.
   - If verification was blocked without one failed verification path, apply `Blocker Contract`.
   - This step MUST end with exactly one of these transitions:
     - if verification review reveals unresolved owner, rule, or verification requirements, go to step 3,
     - if verification review shows that `Scope Derivation Contract` was applied incompletely, go to step 2,
     - if failed-check inspection shows a confirmed implementation or cutover problem under `Code Writing Contract`, go to step 6,
     - if `Blocker Contract` classifies one blocker, go to step 10,
     - if required verification ran and failed and `Blocker Contract` does not classify one blocker, go to step 10.
9. Apply `Semantic Acceptance Contract` to the complete current working scope.
   - If the pass reveals unresolved owner or requirement selection, go to step 3.
   - If the pass reveals incomplete scope, go to step 2.
   - If the pass finds an implementation or cutover problem, go to step 6.
   - If the pass finds a documentation problem, go to step 7.
   - If `Blocker Contract` classifies one blocker, go to step 10.
   - If the fresh complete pass has no finding and no uncovered requirement, go to step 10.
10. Stop and report the current terminal state under `Final Response Contract`, using `Blocker Contract` when the terminal state is blocked.

## Anti-Patterns
- Deviating from `Iteration Cycle` by skipping, merging, reordering, or improvising steps instead of following the declared `go to step N` transitions.
- Returning from failed verification or blocked verification directly to code edits without first applying `Failed Verification Inspection Contract` or `Blocker Contract`, as applicable.
- Violating `Code Writing Contract` by writing code without first identifying the correct owner and applicable rules.
- Violating `Code Writing Contract` by changing one API or signature without migrating in-repository call sites in the same change.
- Violating `Code Writing Contract` by leaving dead code, bridge layers, stale imports, or partial refactor cutovers in the changed scope.
- Violating `Verification Selection Contract` by selecting verification without following the governing `Evidence And Verification Rules` from `AGENTS.md`.
- Treating successful executable verification as semantic acceptance or deriving semantic scope from checker output, tests, the implementation plan, or previous findings.
- Reusing a semantic pass completed before the latest fix instead of restarting the complete pass.
- Violating `Final Response Contract` by claiming verification that was not actually run.
- Violating `Final Response Contract` or `Blocker Contract` by claiming success while a required verification check still fails or remains blocked.
- Violating `Scope Derivation Contract` by expanding a Python refactor scope because of compatibility-only edits outside the declared scope.
