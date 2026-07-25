---
name: workflow-container-audit
description: Use when semantically auditing workflow-container instructions, prompt templates, workflow docs, step contracts, verification prompts, recovery instructions, or workflow-container authoring changes for clarity, reliability, persistence boundaries, and recoverability.
---

# Workflow Container Audit

Use this skill to semantically audit workflow-container instruction artifacts. This skill is not a wrapper around any Python CLI command or static checker.

## Required Reading

Before auditing, read:

1. this `SKILL.md`;
2. `../workflow-container-developer/SKILL.md`;
3. `../workflow-container-developer/references/workflow-container-authoring.md`;
4. the target workflow-container project's instruction-bearing artifacts.

## Audit Scope

Instruction-bearing artifacts include:

- `AGENTS.md`;
- `DESIGN.md`;
- `design/**`;
- prompt templates;
- step prompts;
- verification prompts;
- validator instructions;
- recovery instructions;
- public input schemas, workflow and run configuration contracts, and input migration instructions;
- workflow documentation that defines behavior.

## Semantic Review Workflow

Use one audit order: role detection -> terminology and identity review -> minimality review -> public input review -> runtime boundary review -> persistence and recovery review -> prompt refactor gate -> domain findings.

1. role detection: determine whether each artifact is a boundary contract, workflow sequence, FSM/retry/recovery procedure, generated or persisted data handler, external-source handler, verification guidance, or checking guidance.
2. terminology and identity review: require every workflow, step, attempt, result, state and artifact reference to use the canonical role from `1.3. Основные термины`.
3. minimality review: apply `Minimality Review` before reporting domain-level wording or behavior findings.
4. public input review: apply `Public Input Review` to `WorkflowInputT`, `input.schema.json`, Workflow/WorkflowRun snapshots, step config routing and input migrations.
5. runtime boundary review: apply `Runtime Boundary Review` to public classes, DBOS methods, validators, handoffs, `result.json`/`verification.json` ownership and container execution.
6. persistence and recovery review: apply `Persistence And Recovery Review` to artifact trees, incremental state, atomic publication, restart and retry-layer ownership.
7. prompt refactor gate: apply `Prompt Refactor Gate` before narrower domain findings for prompt templates.
8. domain findings: report remaining role-specific semantic findings after the earlier gates. Use these role criteria:
   - boundary contracts must make inputs, outputs, ownership, and forbidden behavior clear;
   - stateful processes must have explicit states and transitions;
   - linear procedures must have an unambiguous step sequence;
   - significant structured data flow must have clear persistence boundaries;
   - recovery must be deterministic where known errors can occur;
   - instructions must not depend on model memory for durable handoff data;
   - another agent must be able to execute the instruction consistently without hidden chat context.

## Minimality Review

Before reporting domain-level wording or behavior findings, check the artifact against `6.1. Минимальный стабильный контракт` from `../workflow-container-developer/references/workflow-container-authoring.md`.

Report a minimality finding when the artifact introduces or preserves:

- two objects for one semantic concept;
- mirrored semantic data across result, typed step input, private state, artifact handles, audit views, or DBOS handoff payloads;
- duplicate channels for one semantic fact or decision;
- prompt schema text that duplicates Pydantic models or mechanical validators;
- validator logic that reconstructs the next handoff object instead of only validating the current boundary;
- compatibility bridges, proxy layers, alias payloads, or translation layers that exist only to keep an older contract shape alive;
- private step state that later steps consume as public handoff data;
- a new abstraction that does not remove duplication, stabilize one boundary, or simplify validation and recovery.

The recommended fix must prefer simplification first: remove duplicate data or objects, reuse the existing stable model, move the data to the single owner, derive the value from an existing stable handle, or make one boundary the only source of truth. If simplification is impossible, the finding must propose the smallest idiomatic change that satisfies `KISS`, `DRY`, single source of truth and explicit ownership.

Do not report one minimal public routing decision as duplicated SQLite state when a DBOS workflow needs it to choose the next step without filesystem IO, the current owner derives it unambiguously from the verified database, mechanical validation enforces exact parity, and no row data, reasons, counts, or metadata are copied.

Keep the finding scoped to the changed or directly affected boundary. Do not ask for unrelated broad refactoring unless the duplicated contract crosses that boundary and prevents a correct fix.

## Public Input Review

Use `2.3. Исходные контракты и версии`, `2.4. Публичный вход и форма настроек`, `2.5. Миграция публичного входа`, `2.6. Платформенный интерфейс, сборка и проверка`, `3.1. Протоколы типов`, `3.2. Контексты выполнения и состояние Codex`, `5.4. Маршрутизация prompt`, `7.1. Контейнер, секреты и writeback`, and `7.3. Browser runtime` from `../workflow-container-developer/references/workflow-container-authoring.md` as the public-input source of truth.

Report a public-input finding when one artifact:

- separates request and run settings into independently persisted payloads instead of one concrete `WorkflowInputT`;
- stores a partial patch, applies runtime merge precedence, or relies on a missing-field default after the full input has been saved;
- defines `step_map` as an arbitrary mapping, adds empty config objects for steps without settings, or gives a non-concurrent step a `concurrency` field;
- gives a browser-backed workflow a profile writeback policy outside `WorkflowBrowserConfigBase`, hides profile settings outside the exact step config, or treats an empty profile-name prefix as disabling rather than removing filtering;
- copies workflow or step config into step `input.json` instead of storing `workflow_input_path` and passing the exact selected config as a DBOS-recorded argument;
- keeps model, reasoning, correction limit, concurrency, or user instructions in a constructor-owned runtime config or lets action and verifier use different model settings;
- fails to make both workflow-level and current-step instructions visible to both action and verifier with the precedence owned by `5.4. Маршрутизация prompt`;
- accepts a model or reasoning value outside the exact enums declared by the config base classes;
- generates UI or skill questions from a schema other than the immutable schema snapshot of the selected `WorkflowSourceVersion`;
- imports only part of an input object, merges imported JSON into current form state, or validates only the changed fragment instead of the complete object;
- changes the public input incompatibly without a new contract version, declares an ambiguous migration graph, mutates the source file during migration, or skips validation against the target schema;
- lets `workflow-container-input-create` launch the workflow or mutate marketplace state instead of producing one complete validated file;
- leaves source-owned Data, secret, dataset, step, or capability declarations outside exact `workflow.yaml`, accepts undeclared manifest keys or non-exact path parameters, or makes `should_wait_athena_projection=false` an implicit default instead of an explicit per-step exception.

## Runtime Boundary Review

Use `2. Архитектура экосистемы`, `3.1. Протоколы типов`, `3.2. Контексты выполнения и состояние Codex`, `3.3. Иерархия классов`, `3.4. Граница DBOS`, `5.1. Жизненный цикл workflow`, `5.2. Детерминированный шаг`, `5.3. FSM шага Codex`, `5.4. Маршрутизация prompt`, `5.5. Результат проверки`, `5.6. Классификация ошибок и повторов`, `6.2. Результат workflow`, `7. Среда выполнения`, and `Приложение A. Публичные интерфейсы` from `../workflow-container-developer/references/workflow-container-authoring.md` as the runtime-boundary source of truth. Public input ownership is audited only through `Public Input Review`; do not restate it here.

Report a runtime-boundary finding when one artifact:

- uses workflow, step, action, verification or attempt as interchangeable owners;
- keeps `WorkflowBase` as an empty marker, makes `WorkflowStepBase` inherit from `WorkflowBase`, or preserves incompatible hook signatures between deterministic and Codex step types;
- stores invocation input, execution paths, attempt state or results in reusable DBOS or step instance fields;
- lets `CodexRunner` retain model or reasoning between calls, omits explicit per-call `CodexRunnerConfig`, or uses different model settings for action and verifier in one attempt;
- treats a semantic step helper as a DBOS method owner or executes external IO outside a checkpointed DBOS step; ordinary steps use a synchronous `@DBOS.step` wrapper, while concurrent-capable steps use the inherited bounded `DBOS.run_step_async` path;
- implements concurrent-capable step scheduling outside `WorkflowStepCodexConcurrentBase`, changes DBOS queue configuration per run, returns results in completion order, or makes the reported failure depend on task completion order;
- routes browser invocations differently from `7.3. Browser runtime`, including serialization by the shared MCP router URL, reuse of one named profile without a lifecycle lease, domain-owned physical profile paths, or source reset during verification;
- accepts a concurrent invocation batch spanning different run roots or workflow inputs, or permits duplicate or out-of-run step instance directories;
- lets concrete steps override runtime-owned `run(...)` or lifecycle dispatch instead of implementing only declared domain hooks;
- builds `InputT` outside `input_build(execution_context, input_source)` or gives one step a parallel input channel for the same domain data;
- builds `InputSourceT` from previous private state instead of public workflow input, successful results and declared artifact handles;
- performs filesystem, network, Codex or other external IO directly in a DBOS workflow method;
- lets concrete code write standard workflow or step files instead of runtime-owned lifecycle;
- decorates inherited `WorkflowBase` publication methods with `@DBOS.step`, calls them without `await`, or uses synchronous `DBOS.run_step` instead of the runtime-owned `DBOS.run_step_async` boundary;
- asks a verifier to own artifact selection, artifact namespaces, artifact lists, or a second failure channel;
- asks a semantic verifier, Codex action, validator, or concrete step to compute result digest/revision, return persisted `VerificationResult`, or write `verification.json` instead of returning only transient `VerificationDecision` to the runtime;
- duplicates Pydantic/schema checks or mechanical validator checks as prompt text;
- adds a second mechanical validator boundary or lets a validator build downstream handoff data;
- routes retry data through copied values instead of the paths owned by `5.4. Маршрутизация prompt`;
- routes runtime prompt paths differently from `5.4. Маршрутизация prompt`;
- exposes runtime-owned prompt resources without the protected `runtime/` namespace, allows project templates to shadow that namespace, or loads runtime system prompts through unprefixed names;
- conflates correct partial domain output with runtime failure or derives one status from multiple channels;
- uses verification status as the `WorkflowRun` outcome or workflow status as the acceptance decision for `result.json`;
- uses a JSON carrier name that disagrees with its actual serialized-text, validated-model or boundary-payload role;
- changes a public contract without the version boundary owned by `2.3. Исходные контракты и версии`;
- places workflow/Codex in the browser VPN namespace, uses writable input secrets, or gives Docker Compose and Kubernetes different execution semantics;
- appends platform CLI arguments, omits one standard major-2 environment value, duplicates `WorkflowRunContext` provenance in another payload, lets one context change `WorkflowDataPath` between workflow and step, or builds control requests without the exact saved `WorkflowDefinition`;
- introduces a custom step runtime that should belong to `workflow-container-runtime`;
- preserves compatibility aliases, forwarding wrappers or obsolete lifecycle owners after migration.

## Persistence And Recovery Review

Use `4.1. Дерево экземпляров`, `4.2. Стандартные файлы`, `4.3. Межшаговая передача`, `4.4. Объявленные артефакты`, `4.5. Инкрементальные данные`, `5.7. Атомарная публикация и восстановление`, `7.3. Browser runtime`, and `7.4. Материализация внешних артефактов` from `../workflow-container-developer/references/workflow-container-authoring.md` as the persistence source of truth.

Report a persistence or recovery finding when one artifact:

- gives one workflow or step invocation more than one owner directory or gives independent recoverable objects only one aggregate state;
- makes downstream code read previous private `state.json` or private SQLite state instead of a declared database artifact referenced by the previous result;
- rewrites an accumulating JSON array or implements mutable current state as an append-only JSONL revision chain instead of one SQLite row per stable domain key;
- uses JSONL for workflow state, worklists, inventories or FSM data rather than only for immutable event/log streams or fixtures;
- stores one natural compound identity as a concatenated surrogate column alongside its component fields instead of one composite primary key;
- lets Codex execute raw SQL, select an arbitrary database path or table, bypass the statically registered Pydantic row model, or control transaction boundaries;
- lets a downstream read command accept a database path outside its validated input or mutate the previous owner's declared database;
- publishes a declared SQLite database in WAL mode so required current state may remain in untracked `-wal` or `-shm` companions;
- copies SQLite-owned rows into `state.json`, `result.json` or a second artifact instead of passing only the declared database reference;
- publishes or digests an already-created Pydantic object without rebuilding and validating one exact snapshot, so in-place nested mutation can bypass the model contract;
- publishes standard JSON files non-atomically, deletes the previous verdict before replacing `result.json`, or otherwise creates a delete-first crash window instead of relying on result-identity-bound verdicts;
- accepts `verification.json` without the required SHA-256 digest and result revision, or accepts a verdict whose digest/revision identity does not match the current publication;
- includes artifacts or private state in `result_digest` instead of keeping the digest limited to canonical result content and distinguishing Codex attempts through `result_revision_index`;
- repeats a deterministic or Codex action solely because the current result has no verdict or has a verdict for another identity, instead of applying the recovery matrix for the already published current result;
- accepts a previous-attempt verdict when a Codex retry produced identical result bytes, or fails to probe current artifacts in `ready` before deciding whether to accept or run the current action;
- cannot distinguish matching completed input, incomplete execution, failed attempt, stale verdict and mismatched input during restart;
- creates a missing `input.json` after later lifecycle files or artifacts already exist, or recreates missing Codex `state.json` after result/verdict publication and thereby resets attempt identity or budget;
- resumes a failed Codex attempt without a durable `verification_failed -> ready` transition or increments the same failed `VerificationResult` again after `ready` was already committed;
- omits durable Codex attempt state or cannot reconcile a private FSM checkpoint that trails one atomic standard-file publication;
- omits files required to repeat validation or verification from the recovery bundle;
- uses one value as both filesystem write target and public artifact handle;
- accepts an action-supplied path for an artifact whose identity is created during the action instead of deriving the target from the validated identity inside one declared artifact root;
- lets Codex directly or collectively write independently recoverable declared artifacts when the step requires each item to be validated and durably published before advancing, instead of using the owner-defined single-item producer boundary from `4.4. Объявленные артефакты`;
- materializes external artifacts without prevalidating the complete tree, allows writes to runtime-owned root files or diagnostics, partially copies a rejected tree, or replaces existing targets non-atomically;
- creates private state when declared artifacts already own all durable progress;
- publishes browser profile state without the single atomic writeback candidate and `McpPlaywrightProfileWritebackPolicy` from `7.3. Browser runtime`, invents a second candidate registry, omits expected-revision conflict for an overlapping secret writeback, or lets a DBOS step return before a required `working` writeback and owning safepoint complete.

Do not report temporary coexistence of a previous verdict and a newly published result as a defect by itself. That state is crash-safe only because recovery requires both the canonical result digest and the current publication revision.

## Prompt Refactor Gate

Before reporting domain-level findings for one prompt template, evaluate whether the prompt itself needs structural refactoring.

Use `6.3. Правила prompt` and `6.4. Механическая и семантическая проверка` from `../workflow-container-developer/references/workflow-container-authoring.md` as the prompt and verification source of truth.

Report prompt refactoring as the first finding when a prompt has multiple responsibilities but presents them as one flat instruction block without clear role-specific structure or an explicit execution sequence.

The refactor finding must state the target structure required by the prompt's actual role. Do not require identical headings in every prompt; require a structure that makes the real workflow executable and reviewable.

Do not bury a prompt-structure problem under narrower domain findings. If the prompt form makes reliable execution, recovery, or later audit unclear, the audit must recommend prompt refactoring before recommending domain-specific wording fixes.

If a clearer, more reliable, or more recoverable alternative formulation exists, report the current text as a finding. Do not accept weaker text only because it is syntactically valid or because a static checker would pass.

## Finding Contract

Each finding must be self-contained. The reader must be able to implement the fix without asking what artifact, fragment, failure mode, or rewrite target the finding meant.

Use exactly this outer shape:

```text
- <High|Medium|Low>: <artifact path, role, current problem and concrete impact>
  Fix: <one exact rewrite and recheck target>
```

Use `High` when the current contract permits incorrect execution, lost or inconsistent durable data, unsafe recovery, or an impossible public interface. Use `Medium` for competing owners, ambiguous transitions, duplicated contracts, or materially weaker verification. Use `Low` only when behavior is still deterministic and the defect is limited to avoidable clarity or maintenance cost.

Each finding must include:

- the exact artifact path;
- the concrete artifact role being audited;
- the current fragment or instruction pattern that causes the problem;
- why that fragment weakens execution, recovery, persistence, verification, or future audit;
- one concrete rewrite direction with the target structure, target fields, target state transition, or target step sequence;
- the exact recheck target and the expected property after rewriting.

Vague findings are forbidden. Do not write findings whose fix is only "clarify", "make explicit", "improve", "tighten", "refactor", "document better", or "consider changing" unless the same finding also states the exact artifact change and recheck target.

The audit must not require identical headings in all prompts. It must not reduce review to regex checks, mandatory heading checks, or static lint rules. Mechanical observations may be mentioned only as supporting evidence when the semantic issue is clear.
