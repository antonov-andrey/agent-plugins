# Agent Plugins And Project Standards Design

## Назначение

Повторяемые agent workflows и личные инженерные стандарты имеют разных владельцев.

Repository `agent-plugins` является marketplace source для independently installable plugins. Он владеет универсальными agent workflows и отдельными reusable domain plugins, но не владеет личными инженерными предпочтениями, структурой конкретных проектов или их предметной логикой.

Repository и installable plugin `project-standards` являются отдельным opinionated provider личных инженерных стандартов. Они определяют, как разрабатывать и проверять проекты с выбранными capabilities, но не становятся runtime-зависимостью application code.

Имя `project-tools` не является целевым именем repository, marketplace, package или plugin. Общий префикс для repository и provider names не выбирается до утверждения окончательного имени основного проекта; обязательный suffix domain plugin identifiers уже определён как `-agent-tools`. Hosting repository rename, remote rename и добавление общего префикса выполняются только как отдельные явно разрешённые publication actions.

Все новые GitHub repositories этой системы создаются у personal owner `antonov-andrey`. Marketplace repository размещается как `antonov-andrey/agent-plugins`, а standards provider — как `antonov-andrey/project-standards`.

## Владельцы проектных артефактов

Каждое требование имеет одного владельца:

- пользовательский глобальный instruction-файл владеет личными правилами взаимодействия, применимыми ко всем проектам;
- пользовательская глобальная конфигурация harness владеет общими model, feature, approval и sandbox defaults;
- project `AGENTS.md` владеет назначением и структурой конкретного проекта, локальными owner paths, runtime versions, точными командами, security boundaries, project-specific constraints, выбранными external standards и локальными overrides;
- capability skill в `project-standards` владеет одним reusable opinionated engineering standard и его audit contract; mechanical checker и owner-local checker tests существуют только для самостоятельного замкнутого правила с полным детерминированным алгоритмом;
- workflow skill в `agent-workflows` владеет повторяемой task procedure, её report or handoff contract, orchestration mechanics, tools и tests, но не копирует engineering standards;
- `agent-workflows:goal-brainstorm` является единственным нормативным владельцем authoring harness-neutral `goal.md` и `spec.md` до Linear handoff;
- plugin `linear-agent-tools` владеет source-independent Linear task graph, operational lifecycle и local task workspaces после handoff;
- domain skill в одном independently installable domain plugin владеет reusable domain procedure, instructions, references, agent tools и tests, но не project-specific business logic;
- корневой `DESIGN.md` владеет стабильной архитектурой проекта и служит её канонической точкой входа;
- `design/*.md` владеет подробными стабильными контрактами отдельных областей, когда одного `DESIGN.md` недостаточно;
- `docs/**` владеет пользовательской, эксплуатационной и другой документацией, которая не является архитектурным контрактом;

Project `AGENTS.md` обязан объявить полный текущий каталог `project-standards`, а также явно назвать требуемые task workflows из `agent-workflows` и skills из применимых domain plugins. Capability standard из полного каталога применяется только когда его provider-owned trigger соответствует фактическому состоянию проекта или текущей задаче; появление новой entity, technology, boundary, artifact family или workflow автоматически включает уже объявленный standard без изменения каталога. Не использовать или заменить применимый skill можно только по явному требованию пользователя; project-local convenience, существующее несоответствие или молчаливое решение agent не создают исключение. Если обязательный provider или skill недоступен, agent должен остановиться до изменения соответствующего scope. Молчаливое продолжение без объявленного provider contract запрещено.

Другой harness может использовать adapter к тем же canonical contracts из provider repositories. Копирование standards или workflows обратно в consumer repository ради поддержки другого harness запрещено.

## Design-документы

`DESIGN.md` описывает текущее целевое состояние, а не историю его получения. Он содержит назначение системы, границы владельцев, архитектуру, публичные интерфейсы, данные и состояния, существенное поведение при отказах, безопасность и проверяемые архитектурные инварианты в объёме, необходимом конкретному проекту.

Небольшой проект хранит весь stable design в `DESIGN.md`. Крупный проект оставляет в `DESIGN.md` общую модель и ссылки на тематические `design/*.md`. Пустые документы и каталоги ради одинакового внешнего вида не создаются.

Task history, progress, rejected alternatives и завершённые implementation specifications не переносятся в `DESIGN.md` или `design/**`. После реализации туда переходят только устойчивые решения, которые продолжают ограничивать или объяснять работающую систему.

Эксплуатационные runbooks, инструкции использования и справочные материалы остаются под `docs/**`. Каталог `doc/**` и смешение design, operations и task artifacts в одном documentation tree не являются целевой структурой.

## GitHub Repository Lifecycle

Создание, rename и удаление GitHub repository выполняет agent, когда active paired specification называет exact repository owner/name, lifecycle transition, dependency gates и verification contract. Новые repositories создаются только у `antonov-andrey`; rename существующего repository сохраняет его current owner, если пользователь явно не утвердил перенос.

Rename repository является атомарным workspace-wide identity cutover. После hosting rename все active canonical Git URLs должны использовать новое exact repository full name: это относится к configured remotes, tracked `.gitmodules`, dependency и provider manifests, CI и deployment configuration, project instructions и documentation, а также global harness и plugin configuration. Старый URL не может оставаться active fallback или compatibility remote.

Все live local directories, symlinks и configured paths, которые представляют checkout, worktree, marketplace source, установленный plugin или cache repository, должны соответствовать canonical имени. Repository checkout, worktree и marketplace-source copies используют имя `agent-plugins`; installable-plugin source и cache copies используют identifiers `agent-workflows`, `linear-agent-tools`, `marketplace-agent-tools` и `workflow-container-agent-tools`.

Cutover не переписывает Git history, reflogs, завершённые logs, immutable task history или другие historical evidence только ради удаления прежнего имени. После перехода old identity допустима исключительно в таком явно историческом содержимом; active URL, filesystem owner path, configuration key, provider metadata или discovery result со старым именем является незавершённым cutover.

Repository создаётся до consumer cutover, получает canonical remote, default branch и требуемую provider structure, после чего должен пройти standalone verification и fresh-consumer discovery. Consumer dependency не переключается на ещё не проверенный repository.

Repository удаляется только после доказанного отсутствия active consumers, gitlinks, configured remotes, deployment references и required data. Удаление использует exact full repository name и запрещает prefix, wildcard или inferred bulk selection. Требуемый GitHub authorization scope проверяется непосредственно перед mutation; scope `delete_repo` добавляется через authenticated GitHub CLI flow, если он отсутствует. Credentials и authorization tokens не записываются в project artifacts или отчёты.

Удалённый repository не воссоздаётся как compatibility bridge, mirror или forwarding owner, если paired specification явно не определяет новый lifecycle.

## Agent Plugins Marketplace

`agent-plugins` является marketplace repository, а не installable plugin. Он содержит четыре независимо устанавливаемых plugins.

```text
agent-plugins/
  .agents/plugins/marketplace.json
  plugins/
    agent-workflows/
      .codex-plugin/plugin.json
      skills/
      lib/
    linear-agent-tools/
      .codex-plugin/plugin.json
      skills/
      lib/
    marketplace-agent-tools/
      .codex-plugin/plugin.json
      skills/
    workflow-container-agent-tools/
      .codex-plugin/plugin.json
      skills/
  test/
    agent_workflows/
    linear_agent_tools/
```

Plugin `agent-workflows` владеет skills:

- `code-antipattern-audit`;
- `code-audit`;
- `explain-algorithm`;
- `explain-interface`;
- `explain-internal-api`;
- `explain-internal-map`;
- `explain-persistence`;
- `git-commit`;
- `goal-brainstorm`;
- `instruction-audit`;
- `instruction-migration`;
- `sequential-batch`.

Plugin `workflow-container-agent-tools` владеет skills:

- `workflow-container-audit`;
- `workflow-container-developer`;
- `workflow-container-input-create`.

Plugin `marketplace-agent-tools` владеет reusable marketplace-specific skills, references и agent tools, включая `ozon-seller-api-developer`. Его имя намеренно не совпадает с существующим application repository `marketplace-tools`.

Plugin `linear-agent-tools` владеет skills:

- `workflow-configure`;
- `task-graph-create`;
- `task-implement`;
- `task-review`;
- `task-accept`;
- `task-merge`;
- `task-cleanup`.

`goal-brainstorm` принадлежит `agent-workflows`, потому что authoring source specification применим к проектам без Linear и без workflow-container. Linear-specific graph publication и operational lifecycle принадлежат `linear-agent-tools`.

Общие `explain`, `section-audit`, `sequential-batch` и `subagent-transport` mechanics принадлежат plugin-local support owners внутри `agent-workflows`. Public `agent-workflows:sequential-batch` является stable dependency для project-local workflows, которым нужны эти mechanics. Consumer projects не содержат их копии. Opinionated audit cards и mechanical code-standard checks не принадлежат `agent-workflows`; они поступают из выбранных `project-standards` skills и project-local contracts.

Plugin-local support owner размещается в `plugins/<plugin>/lib/<owner>/`. Переиспользуемый Python-модуль, вызываемый skill-local script, принадлежит непосредственно этому owner path. Каталог `tool/` существует только для исполняемых CLI entrypoints самого support owner, а `tool/lib/` — только для реализации, общей для нескольких таких entrypoints. Тонкий исполняемый интерфейс, принадлежащий workflow skill, размещается в `skills/<skill>/scripts/`. Структура каталогов определяется реальным владельцем и направлением вызова, а не копируется с другого support owner.

`linear_boundary` разделяет shared validation и status vocabulary, один `configuration/` subsystem для workspace-global configuration, один `task/` owner для dispatch и transitions и отдельный HTTP transport. Внутри `configuration/` каталог желаемых статусов и labels принадлежит `catalog.py`, typed payload-модели вместе с их intrinsic validation и approved-plan invariants — `model.py`, сравнение current state с каталогом — `reconciliation.py`, а единственный отсутствующий в MCP GraphQL transport adapter — `graphql.py`; сваливать эти владельцы обратно в один общий модуль запрещено. Configuration GraphQL adapter не владеет task state machine, task workflow не владеет authenticated workspace configuration, а transport не знает Product policy. Package `__init__.py` не является re-export facade: потребитель импортирует символ из его defining module, поэтому перенос владельца не маскируется compatibility alias.

`task_graph/reconciliation/` является cohesive subsystem, а не плоским compatibility facade: `model.py` владеет remote snapshots, mutation actions и reconciliation result; `initial.py` владеет только initial Project activation barrier; `delta.py` владеет только active-Project delta. Reconciler object связывает immutable graph или delta один раз и предоставляет операции над remote state; публичные receiverless pass-through functions и возврат к одному смешанному `task_graph/reconciliation.py` запрещены. Provider transaction-document reads принадлежат отдельному `TransactionDocumentReader`, а не случайным free helpers.

Initial и delta reconcilers показывают реальный порядок provider transaction через короткий public `plan`: identity/receipt, staged issues, blocker relations, activation metadata, node activation и final Project state; delta дополнительно владеет resource collision, topology и reverification gates. Каждая нетривиальная фаза остаётся именованной domain operation того же workflow owner, а не разрастается внутри одного монолитного метода и не маскируется generic `stage`-классами, tuple carriers или pass-through facade.

Provider-owned Project descriptions, issue descriptions и transaction documents сразу рендерятся в canonical Markdown Linear: unordered lists используют `*`, ordered repository plans используют настоящий ordered list, absolute HTTP(S) references получают explicit link с percent-encoded target, а trailing newline отсутствует. Reconciler сравнивает именно эту read-after-write форму; он не предполагает, что Linear сохранит произвольный входной Markdown byte-for-byte, и не вводит permissive normalization, которая могла бы скрыть пользовательское изменение provider-owned content. Для issue mutation identity provider принимает exact UUID либо canonical team identifier вроде `AND-5`, потому что официальный Linear MCP возвращает identifier в поле `id`; это не разрешает lookup по title или другому display text.

`task_workspace/transaction.py` владеет только последовательностью создания и восстановления complete multi-repository workspace transaction. `task_workspace/planning.py` отдельно выводит durable initial state одного repository из exact Git baseline до первой Git mutation; `repository.py` остаётся владельцем Git и private-state boundary, а `bootstrap.py` — manifest и materialization contract. Orchestrator не должен заново втягивать baseline discovery, manifest parsing или Git mutation внутрь одного длинного метода.

`task_cleanup/reconciliation.py` владеет одним bounded cleanup workflow. Его public `cleanup` удерживает issue lock и явно упорядочивает repository-state read-back, terminal identity snapshot, PR reconciliation, declared-resource cleanup, workspace retirement и Project-final absence proof. Общие изменяемые counters и repository snapshots принадлежат одному run-local `CleanupState`; immutable config и injected GitHub/resource boundaries остаются на workflow owner. `task_cleanup/workspace.py` отдельно владеет crash-recoverable lifecycle удаления одного repository workspace: project cleanup binding, durable branch snapshot, worktree и exact local/remote branch retirement, integration proof и private-state deletion. Workflow manager выполняет только последовательность и dependency wiring; добавление нового cleanup boundary обязано либо переиспользовать существующего injected collaborator, либо создать cohesive collaborator в `task_cleanup`, а не возвращать boundary implementation в orchestration method, создавать hidden dependency или передавать один и тот же широкий набор несвязанных аргументов между generic helpers.

Codex plugin manifest не устанавливает произвольные Python dependencies: он объявляет plugin surfaces, а skill metadata может объявлять только MCP tool dependencies. Поэтому непосредственно исполняемые Python scripts и их plugin-local `lib` используют Python standard library. Capability, которому действительно нужен third-party runtime, получает отдельный явно устанавливаемый runtime owner либо MCP boundary; неописанный import из случайного project venv запрещён. `requirements-dev.txt` принадлежит только воспроизводимым formatting, validation и test tools и не является runtime dependency installable plugins.

Эта installability boundary является явным исключением из обычного project-owned `retry_runtime`: узкий `linear_boundary.transport` самостоятельно владеет только bounded HTTP retry для доказанно repeat-safe Linear GraphQL operations, никогда не повторяет non-repeat-safe mutation и не превращается в общий retry framework. Все внешние и transient JSON inputs independently installable `linear-agent-tools` проходят через один stdlib-only `json_contract` owner, который запрещает duplicate object keys, non-standard numeric constants и malformed UTF-8; domain owners затем проверяют exact shape. Разрозненные permissive `json.loads` на внешних границах запрещены.

Тот же strict external-input contract применяется независимо внутри installable `agent-workflows`: один plugin-local stdlib-only `json_contract` обслуживает authoring recovery journals и session JSONL readers. Plugin boundaries не импортируют реализацию друг друга, но duplicate keys, non-standard numeric constants и malformed UTF-8 не принимаются ни одним recovery owner.

Все repository-level provider suites расположены только под `test/<plugin>/<owner>/`. Корневой `pytest.ini` явно включает shared owner-aware pytest plugin; `pytest -q` обязан обнаруживать весь tracked provider suite, а не только корневые smoke tests. Произвольные `plugins/**/lib/**/test/` roots запрещены, поскольку они не являются самостоятельными Skill или Submodule test owners.

Workflow входит в `agent-workflows` только по явно утверждённому пользователем source-to-target решению. Для утверждённого переноса workflow обязан:

- сохранять утверждённую reusable семантику вне исходного project;
- не содержит абсолютных workspace paths, имён конкретных application projects и закрытых domain contracts;
- принимает repository или workspace scope через явную границу;
- владеет полноценной повторяемой процедурой, а не только перенаправляет на один project document;
- поставляется со всеми необходимыми references, templates, tools и tests.

`agent-plugins` не предоставляет отдельный Python package или CLI для поиска соседних workflow-container projects. Plugin installation, skills и owner-local agent tools являются полным public provider surface; параллельный локальный discovery path отсутствует.

## Goal Brainstorm И Linear Task Workflow

`agent-workflows:goal-brainstorm` владеет только authoring одного coherent source outcome. Он создаёт и свободно пересматривает tracked `project-goals/<common-prefix>/goal.md` и `spec.md` через короткие serialized direct-main transactions. До Linear handoff нет `seal`, persistent goal, immutable-spec state, implementation branch или implementation worktree.

Repository `project-goals` использует только canonical checkout ветки `main`. Coordination task branches, linked worktrees, bootstrap manifests, project-local `.spec/`, task-artifact copies и symlinks запрещены. Exact published Git commit, один commit-pinned canonical URL source directory и его exact children `goal.md`/`spec.md` образуют immutable source snapshot для downstream consumer; URL отдельного файла не заменяет directory identity. После успешного Linear handoff current execution changes принадлежат только Linear; изменение later `project-goals/main` не изменяет уже отправленный snapshot.

`linear-agent-tools:workflow-configure` явно подготавливает exact Linear destination: team-level issue statuses в fixed categories, workspace-level Project statuses `Planned`, `In Progress`, `Completed`, `Canceled`, role labels, required `agent:codex` label и полное отсутствие team Git status automations, включая target-branch rules. GitHub integration остаётся включённой только как официальный issue-to-branch/PR link owner; PR events не меняют task status, потому что lifecycle transitions, evidence gates и cleanup принадлежат provider workflow. `workflow-configure` полностью пагинирует эти правила, показывает их exact deletion в global mutation plan, удаляет только утверждённые identities и требует empty read-back. Он показывает global mutation до применения, принимает только exact non-conflicting configuration и не создаёт task graph. Official Linear MCP и user-level OAuth используются первыми; missing provider operation доказывается contract probe до появления minimal Linear-specific GraphQL boundary.

`linear-agent-tools:task-graph-create` принимает explicit source независимо от его происхождения, показывает complete bounded DAG пользователю и публикует один Linear Project с issues и blocker relations. Provider-owned initial import plan и каждый approved active-Project delta живут как immutable visible Linear Project transaction documents; они сохраняют recovery envelope, но не образуют второй operational graph. Пока Project имеет status `Planned`, все его issues non-dispatchable; после полного read-back proof один Project transition `Planned -> In Progress` активирует graph. Agent-executable nodes используют exact label `agent:codex`, fully defined nodes находятся в `Todo`, а unresolved blockers независимо сохраняют `dispatchable=false`. Stable source keys обеспечивают идемпотентное reconciliation без `task-graph.yaml` или persistent graph carrier вне Linear.

Linear Project является task container одного согласованного source outcome, а не представлением Git repository. Одна issue обычно изменяет один repository, но неделимая issue может перечислять несколько repositories; один Linear Project поэтому может охватывать несколько Git repositories, а один Git repository может участвовать в нескольких независимых Linear Projects. Связь с Git хранится только на уровне конкретных issue через canonical repository/base/branch/PR identities.

Один plugin-owned issue template задаёт task outcome, source provenance, scope/non-goals, delivery kind, repository/base identity, applicable contracts, blockers, resources, lifetime и exact downstream consumers, verification plan, human boundary и evidence links. Issue-lifetime resource сохраняет workspace owning issue до terminal state всех declared consumers; attempt resource очищается на границе attempt, а project resource — final cleanup node. Role-specific sections условны; отдельные несовместимые copies шаблона запрещены. Linear issue является canonical per-task goal и execution journal.

Linear statuses образуют единый workflow: `Backlog`, `Todo`, `In Progress`, `Human Review`, `Rework`, `Merging`, `Done`, `Canceled`. `Todo`, `In Progress`, `Rework` и `Merging` являются active states. `Done` и `Canceled` являются terminal states. Blockers, required label, exact assignee или delegate, совместимая пара task role/delivery и complete task contract определяют explicit `dispatchable`; `Merging` допускает только `task:implementation` с `code`, отдельный blocked status не создаётся.

Каждая top-level agent attempt до dispatch или status mutation получает один process-lifetime host-local lock по exact Linear issue и удерживает его до attempt-resource cleanup и final Linear read-back; crash освобождает kernel lock. Nested cleanup переиспользует guard вызывающей attempt, а отдельный short-lived operation lock сериализует только Git workspace и cleanup transactions. Каждая code-mutating issue использует branch `linear/<lowercase-issue-identifier>` и project-local `.worktree/<lowercase-issue-identifier>`. `linear-agent-tools:task-implement` владеет creation, adoption, project-local bootstrap resources, crash recovery и fresh-thread implementation attempts. Первый prepare инициализирует recursive submodules как detached read-only checkout exact index gitlinks и возвращает полный path/commit snapshot; до Product mutation один typed Linear comment фиксирует source fingerprint, deterministic branch и exact baseline commit каждого repository, а recovery принимает только совпадающее evidence и не сбрасывает пользовательскую работу. Base commit фиксируется при первом dispatch, а `Rework` принимает существующую branch и PR без destructive reset. `task-review` и `task-accept` проверяют exact current state без скрытых fixes. Finding против merged state принадлежит review/acceptance no-fix boundary, а его graph mutation выполняет `task-graph-create` как approved active-Project delta: новая remediation issue получает новую branch, а текущая review или acceptance issue возвращается в blocked `Todo`. `task-merge` работает только после human transition в `Merging` и возвращает changed candidate в `Rework`. Каждый graph завершается `task:cleanup`: `task-cleanup` идемпотентно закрывает exact canceled pull request, удаляет только exact local and remote workspace state terminal issue, выполняет explicit project-owned resource cleanup и переводит Project в `Completed` только после final acceptance и полного reconciliation. Product commit и push остаются у `agent-workflows:git-commit`.

Verification receipt хранится в Linear issue или GitHub check и допускает reuse только для exact source fingerprint, direct command, canonical absolute working directory, corpus content, model, model configuration и применимых environment или release identity. Checkout list хранит для каждого result-affecting checkout его canonical absolute path, roles, repository URL, full commit, recursive submodule commits и dependency locks с canonical checkout-relative paths, поэтому две ревизии одного repository не коллидируют. Stable verification key связывает только полный набор result-affecting inputs. Отдельный receipt key связывает verification key, outcome, UTC completion instant, canonical artifact URL и content SHA-256. Artifact URL обязан быть durable canonical HTTPS provider identity без credentials, port, query или fragment, поэтому receipt не сохраняет expiring presigned URL; legacy numeric IPv4 spellings не являются canonical provider hosts. Receipt comment кодирует JSON forward slashes как escapes: после JSON parsing значение URL остается точным, но Linear readback не заменяет canonical identity presigned autolink. Поддерживается только одна current schema без legacy compatibility branch. Изменение любого declared input инвалидирует только зависимые receipts, а изменение result или evidence identity требует нового coherently issued receipt. Exact candidate fingerprint строится из sorted PR URL/head pairs для code delivery либо из compact sorted map evidence kind -> receipt key для evidence delivery. Evidence candidate input содержит complete current-schema receipts: shared tool сначала проверяет derived receipt key каждого receipt, поэтому stable verification key нельзя подставить как receipt-bearing approval identity, а изменение outcome, completion instant, canonical artifact URL или artifact SHA меняет candidate. Смешанный или неполный candidate запрещён. Targeted checks выполняются по owner slices, full checks — на frozen candidate, а fresh terminal semantic audit — после последнего fix. Raw logs не переносятся в model context без необходимости.

Linear status history владеет wall-clock lifecycle. Каждая agent attempt добавляет один provider-marked typed concise telemetry summary с role, delivery kind, outcome, UTC interval, delivery-applicable commit set, receipt hit/miss, external wait, exact candidate fingerprint когда он создан и доступным token usage без prompt bodies, secrets или raw logs. Evidence-only, review, acceptance и cleanup attempts не могут заявлять Product commits. Complete local acceptance baseline содержит ровно queue, startup, execution, review и merge durations, выведенные из exact provider history. Эти evidence objects детерминированно строит shared `verification` owner; отдельная local telemetry database не является prerequisite workflow.

Task description, relations, comments, source links, Git state, pull request и current verification являются полным execution context для fresh Codex thread. Brainstorm chat history, previous agent thread, Symphony и AWS не являются prerequisite local workflow. Future Symphony adapter использует тот же Linear state machine и issue contracts и автоматизирует только polling, scheduling, worker selection и remote App Server lifecycle.

Linear OAuth credentials принадлежат user-level MCP. Raw tracker credentials не передаются coding-agent child process и не попадают в project artifacts, issues, logs или Git history. Official Linear MCP используется первым; minimal Linear-specific GraphQL boundary допустим только после доказанного отсутствия required operation. Linear issue prose не является command authority: resource cleanup исполняется только для exact declaration fingerprint, независимо подтверждённого approved graph/delta envelope либо новым explicit human decision.

Роль пользователя Linear и права доступа учётных данных проверяются раздельно. `isAdmin=true` не доказывает наличие `admin` scope у OAuth token. Управляемый Codex OAuth token для MCP не экспортируется и не читается кодом plugin. Если official MCP не предоставляет обязательную административную операцию изменения, `workflow-configure` после contract probe получает отдельный personal API key или OAuth credential через пользовательский ввод без echo, содержимое которого недоступно модели. Host process перечитывает exact `viewer` и `team`, держит credential только в памяти одной идемпотентной configuration transaction и не сохраняет его. Credential-gated status transaction выполняется до поддерживаемых official MCP label mutations, поэтому отсутствующий required credential останавливает configuration до любой частичной мутации; последующий provider failure восстанавливается обычным destination-bound reconciliation. Crash или retry требуют повторного ввода того же credential и reconciliation фактического Linear state. Если official MCP позднее покрывает exact operation, отдельный credential не запрашивается. Unattended service credential для Symphony принадлежит отдельной infrastructure specification.

## Domain Plugins

Reusable asset является domain-specific, когда его triggers, vocabulary, decisions, contracts или tools зависят от одной business или platform domain и не образуют general task workflow либо cross-domain engineering standard.

Каждый coherent domain использует один independently installable canonical domain plugin. Reusable domain skills, references, templates, tools и tests не копируются по consumer projects, repositories, vendor endpoints или отдельным tasks.

Domain plugin раскрывает reusable contract через один или несколько independently triggerable domain skills. Пустой plugin или plugin без skill entrypoint не является допустимым domain owner.

Классификация каждого skill, reference, template или agent tool как project-local либо reusable domain asset определяется явным пользовательским source-to-target решением. Количество текущих consumers, потенциальная будущая применимость и agent inference не заменяют такое решение. Если пользователь утвердил asset как reusable domain asset, он переходит в canonical plugin этого domain; если canonical plugin отсутствует, он создаётся до удаления исходного owner.

Generic source authoring, Git publication и cross-domain task procedures принадлежат `agent-workflows`. Linear-specific graph и task lifecycle принадлежат `linear-agent-tools`. Cross-domain opinionated engineering standards принадлежат `project-standards`. Явно назначенные пользователем reusable workflow-container domain assets принадлежат `workflow-container-agent-tools`. Явно назначенные пользователем reusable marketplace domain assets принадлежат `marketplace-agent-tools`.

Domain plugin ссылается на generic workflow и engineering owners и не копирует их contracts. Stable runtime provider design остаётся в `DESIGN.md` provider repository. Application-specific business behavior, paths, configuration, data и executable runtime logic остаются у owning project, если domain plugin не владеет реальным reusable agent tool.

Имя domain plugin обязано использовать общий shape `<domain>-agent-tools`, явно обозначать domain и agent-tool role и не совпадать с active application repository, marketplace source или другим plugin identifier. Другой suffix или suffixless domain plugin identifier запрещён, пока пользователь явно не изменит этот naming contract.

Недоступность применимого domain plugin или required domain skill разрешает read-only discovery, но запрещает mutation соответствующего domain scope. Пропуск, замена или обход применимого domain plugin допустимы только по явному требованию пользователя.

## Project Standards

`project-standards` является отдельными repository, marketplace source и installable plugin с одинаковым именем. Он содержит independently triggerable capability skills:

```text
project-standards/
  .agents/plugins/marketplace.json
  plugins/
    project-standards/
      .codex-plugin/plugin.json
      skills/
  test/
```

- `project-foundation`;
- `project-instruction-developer`;
- `project-documentation-developer`;
- `python-developer`;
- `legacy-python-maintainer`;
- `python-cli-developer`;
- `python-logging-developer`;
- `python-retry-developer`;
- `pytest-developer`;
- `sqlalchemy-developer`;
- `runtime-config-developer`;
- `http-api-client-developer`;
- `rest-api-server-developer`;
- `typescript-developer`;
- `react-ui-developer`;
- `submodule-developer`;
- `docker-compose-developer`;
- `kubernetes-developer`;
- `aws-cloudformation-developer`;
- `zitadel-developer`;
- `project-standard-audit`.

Каждый capability skill является canonical owner своего reusable standard и содержит применимые development rules и audit contract. Mechanical checker и его tests принадлежат этому skill только при выполнении строгого критерия полной детерминированной проверяемости. `project-standard-audit` классифицирует условную применимость skills из полного объявленного каталога, а общий `agent-workflows:code-audit` управляет audit procedure и report contract.

Project связывается со всем provider одной canonical секцией `Required Standards` в `AGENTS.md`; её набор обязан точно совпадать с полным текущим каталогом `project-standards`. Это объявление не утверждает наличие каждой технологии: фактическая применимость определяется provider-owned trigger во время задачи и semantic audit. Исключение или project-local specialization допустимы только по явному требованию пользователя и должны называть внешний owner и точную локальную область. Generated copies standard prose в `AGENTS.md` не создаются и drift synchronization между provider и consumer prose не используется. Project-local overlay не повторяет standard и содержит только реальные локальные bindings, ограничения и явно разрешённые исключения.

Named term, определённый обязательным capability skill, входит в instruction model проекта в пределах applicability этого skill и может использоваться в project `AGENTS.md` без копирования definition. Provider term block остаётся единственным canonical definition owner. Project-local `Core Terms` содержит только специфичные для проекта terms, которых нет в применимых standards. Несовместимые definitions одного term в двух применимых providers являются fail-closed conflict. Явно разрешённая пользователем локальная specialization может расширить использование provider-owned term только в объявленной области, но не становится вторым definition owner.

Один task может применять несколько skills. Например, изменение inbound REST API использует `project-standards:rest-api-server-developer` как reusable engineering standard и project-local `AGENTS.md` или design как owner конкретного framework, router, authentication и domain contract.

## Project-local boundary

Project-specific workflow не переносится в generic plugin целиком. Его:

- общая task mechanics переходит в `agent-workflows`;
- reusable opinionated engineering standard переходит в соответствующий `project-standards` capability skill;
- явно утверждённая reusable domain-specific procedure, instruction или agent tool переходит в canonical domain plugin;
- stable product и domain semantics переходят в project `DESIGN.md` или `design/**`;
- executable domain logic остаётся в project code или tool;
- exact commands, owner paths, runtime versions, security boundaries и local routing остаются в project `AGENTS.md`.

Product-specific names и contracts, включая concrete API routers, identity providers, delegated-user semantics, storage models, marketplace payloads и workflow names, не становятся standards только потому, что они встречаются более чем в одном большом `AGENTS.md`. Их перенос в reusable owner либо сохранение в project-local contract определяется явным решением пользователя.

Унаследованные от retired repository `template-bin` файлы `bin/**` явно классифицированы как project-local deployment и support assets каждого consumer. Совпадение их текущего содержимого не создаёт shared owner, synchronization contract или право автоматически восстановить общий template; каждый project развивает эти файлы независимо, пока пользователь явно не утвердит другое owner decision.

Решение о переносе concept в reusable capability skill либо сохранении project-local owner принимает пользователь для конкретного source scope; автоматический consumer-count criterion не применяется. В текущем workspace `Worker script` и concrete runtime owner `base_worker` остаются project-local contracts `workflow-control-center`. `marketplace-tr-priority` не имеет `Worker script` и не выбирает этот contract; после отдельного явного lifecycle approval пользователя его физически присутствующий неиспользуемый `base_worker` submodule удаляется сразу.

## Конфигурация

Общие пользовательские настройки harness принадлежат его global configuration. Project-local harness configuration допускается только для реального project-specific отличия. Копии одинаковых model, reasoning, feature, sandbox и role settings в нескольких projects запрещены.

Global Codex configuration использует `project_doc_max_bytes = 524288`, `[agents].max_concurrent_threads_per_session = 255` и `[agents].max_depth = 3`. Instruction-size value является верхней границей загружаемой project instruction chain, а не резервированным объёмом контекста. Concurrency value задаёт пользовательский cap и не гарантирует наличие такого количества физических harness slots.

После provider cutover `workflow-control-center` и `marketplace-tr-priority` не сохраняют `.codex/config.toml`: их общие значения принадлежат global configuration, а named role entries заменяются provider-owned workflow contracts. Новый project-local harness config создаётся только при доказанном project-specific отличии.

Полный provider catalog объявляется в `AGENTS.md`; отдельный project-standard manifest не создаётся без доказанной потребности в machine-readable boundary. Plugin installation и skill discovery могут быть harness-specific, но canonical standard и design contracts остаются обычным Markdown.

Projects и объявленные provider catalogs для workspace standardization обнаруживаются по filesystem и project metadata относительно явно переданного workspace root. Generic implementation не содержит списка пользовательских checkout или абсолютного пути `/home/andrey/Projects`.

## Проверка

`agent-plugins` и `project-standards` проверяются независимо. Каждый plugin проходит structural validation, owner-local automated tests и scenario tests своих workflows или capability skills.

Перенос существующего workflow обязан сохранить существенные сценарии прежних owners. Разошедшиеся `code-audit` и `instruction-audit` разделяются на один canonical workflow and report contract, reusable standards-owned audit cards и project-local product checks; одна случайно выбранная consumer copy не становится source of truth.

Workspace standardization verification подтверждает:

- доступность каждого required plugin и skill до изменения consumer project;
- точное равенство `Required Standards` полному provider catalog и отдельную семантическую классификацию фактически применимых capability skills;
- отсутствие local copies общих skills и orchestration assets в consumer projects;
- отсутствие generated copies `project-standards` prose;
- наличие корректного project `AGENTS.md` с `Required Standards`, owner paths, commands, local boundaries и overlays;
- корректную классификацию stable design и прочих docs;
- отсутствие абсолютных workspace paths и project-specific domain contracts в generic provider assets;
- прохождение применимых проверок каждого изменённого project.

Механическая и семантическая проверки образуют разные обязательные фазы. Исполняемый checker допустим только для самостоятельного закрытого правила, которое он полностью и детерминированно решает на всей объявленной области. Эвристические сигналы, выбранные примеры, thresholds, name/path allowlists и exception lists не являются проверкой правила и не поставляются как checker.

Semantic audit строит coverage независимо из полного набора применимых canonical owners и каждого их нормативного требования. Checker inventory, успешный exit code, implementation plan, исторические findings и заранее замеченные concerns не могут определять или сужать semantic scope. Каждый section result содержит отдельный статус и текущее evidence для каждого назначенного требования; финальный report сохраняет отдельно mechanical evidence и полное semantic coverage. Формальный validator подтверждает только структуру и наличие переданного parent-инвентаря, но не смысл, истинность evidence или корректность semantic verdict. После любого fix полный semantic audit начинается заново; acceptance требует свежего прохода без findings и без непокрытых требований.

Обычные writing workflows после последнего fix и исполняемой verification выполняют такой же полный прямой semantic pass по всем применимым owner requirements, но не создают report, completion ledger или другой evidence artifact. Structured audit workflow и его report запускаются только по явному запросу пользователя. Успешные scripts, tests или validators не могут заменить ни прямой semantic acceptance writing workflow, ни явно запрошенный structured audit.

Provider проверяется, устанавливается и становится доступным в fresh harness session раньше удаления consumer copy. Неуспешная provider validation оставляет consumer project с прежним рабочим workflow и допускает исправление provider без промежуточного compatibility layer.

### Поведенческая Проверка Skills

`skill_behavior_eval/corpus-v1.json` является версионированным набором direct, indirect, incomplete, negative и overlap scenarios для четырёх plugins этого repository. Каждый case задаёт expected и forbidden activation и смысловые invariants ответа.

Общий runner принадлежит `project-standards:project-instruction-developer` и не копируется в этот repository. Он выполняет read-only generation и отдельное semantic judging на target model. Эта opt-in фаза обязательна при существенном изменении triggers, explicit-only policy, overlap boundaries или workflow output contract, но остаётся отдельной от plugin validator, skill validator и `pytest`.
