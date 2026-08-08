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
- `task-graph-sync`;
- `task-implement`;
- `task-review`;
- `task-accept`;
- `task-merge`;
- `task-cleanup`.

`goal-brainstorm` принадлежит `agent-workflows`, потому что authoring source specification применим к проектам без Linear и без workflow-container. Linear-specific graph publication и operational lifecycle принадлежат `linear-agent-tools`.

Общие `explain`, `section-audit`, `sequential-batch` и `subagent-transport` mechanics принадлежат plugin-local support owners внутри `agent-workflows`. Public `agent-workflows:sequential-batch` является stable dependency для project-local workflows, которым нужны эти mechanics. Consumer projects не содержат их копии. Opinionated audit cards и mechanical code-standard checks не принадлежат `agent-workflows`; они поступают из выбранных `project-standards` skills и project-local contracts.

Plugin-local support owner размещается в `plugins/<plugin>/lib/<owner>/`. Переиспользуемый Python-модуль, вызываемый skill-local script, принадлежит непосредственно этому owner path. Каталог `tool/` существует только для исполняемых CLI entrypoints самого support owner, а `tool/lib/` — только для реализации, общей для нескольких таких entrypoints. Тонкий исполняемый интерфейс, принадлежащий workflow skill, размещается в `skills/<skill>/scripts/`. Структура каталогов определяется реальным владельцем и направлением вызова, а не копируется с другого support owner.

`linear_boundary` разделяет shared validation и status vocabulary, один `configuration/` subsystem для workspace-global configuration, один `task/` owner для dispatch и transitions и отдельный HTTP transport. Внутри `configuration/` каталог желаемых статусов и labels принадлежит `catalog.py`, typed payload-модели вместе с их intrinsic validation и approved-plan invariants — `model.py`, сравнение current state с каталогом — `reconciliation.py`, а единственный отсутствующий в MCP GraphQL transport adapter — `graphql.py`; сваливать эти владельцы обратно в один общий модуль запрещено. Configuration GraphQL adapter не владеет task state machine, task workflow не владеет authenticated workspace configuration, а transport не знает Product policy. Package `__init__.py` не является re-export facade: потребитель импортирует символ из его defining module, поэтому перенос владельца не маскируется compatibility alias.

`task_graph/` является одним cohesive synchronization owner. Его typed model хранит только canonical source directory, synchronized Git commit, stable task keys и поля, которые управляют текущим Linear transition. Reconciliation читает semantic current state, вычисляет только следующую безопасную mutation и не создаёт import/delta model, transaction document, receipt, fingerprint, compatibility facade или private graph. Нетривиальные фазы initial barrier, active staging, relation reconciliation, activation и terminal revision остаются именованными operations одного owner.

Provider-owned Project metadata и issue cards сразу рендерятся в human-readable Markdown. Project metadata содержит canonical repository, полный source-directory path и latest synchronized commit. Exact card schema и presentation semantics принадлежат только `plugins/linear-agent-tools/lib/task_graph/issue-contract.md`; `DESIGN.md` зависит только от полученного stable task key и semantic task contract. Renderer сравнивает эти semantic fields, а не prose bytes или checksum. Для mutation provider принимает exact Linear UUID либо один canonical team identifier, нормализованный transport boundary; title и другие display values не являются identity.

`task_workspace/transaction.py` владеет только последовательностью создания и восстановления complete multi-repository workspace transaction. `task_workspace/planning.py` до первой Git mutation фиксирует единственное недеривируемое private state одного repository — exact first-attempt baseline commit. Issue identifier и canonical repository identity выводят deterministic branch, worktree path и state location; immutable baseline manifest выводит bootstrap resources, а Git index — recursive submodules. `repository.py` остаётся владельцем Git и minimal private-state boundary, `bootstrap.py` — manifest и direct semantic materialization contract. Retry перечитывает эти current owners и не хранит phase, path, branch, manifest/resource snapshot, checksum или parallel recovery representation.

`task_cleanup/reconciliation.py` владеет одним bounded cleanup workflow. Его public `cleanup` удерживает issue lock и явно упорядочивает current repository/private-baseline readback, terminal authority validation, complete exact PR reconciliation, workspace retirement и Project-final absence proof. Общие изменяемые counters и current repository reads принадлежат одному run-local `CleanupState`; immutable config и injected GitHub boundary остаются на workflow owner. `task_cleanup/workspace.py` отдельно выводит branch и worktree из issue/repository identity, перечитывает live Git/GitHub state, доказывает baseline ancestry и successful integration, затем удаляет exact worktree, leased remote branch, local branch и private baseline. Наличие каждого current provider target является crash-recovery state; durable cleanup phases, project cleanup binding, branch/resource snapshots и arbitrary command runner отсутствуют. Новый genuinely non-standard resource boundary допустим только как cohesive typed provider handler, который реально consumes natural owner identity и lifetime; generic argv execution, fingerprint и issue-prose authority запрещены.

`git_origin/identity.py` является единственным plugin-local owner нормализации credential-free Git origin. Task workspace и typed workspace baseline используют его напрямую; второй URL parser или сохранение repository URL с embedded username/password, query либо fragment запрещены. Внешняя ошибка валидации не повторяет rejected URL, чтобы credential-bearing input не попадал в output или evidence.

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

`agent-workflows:goal-brainstorm` владеет только authoring одного coherent source outcome. Он создаёт и пересматривает tracked `project-goals/<common-prefix>/goal.md` и `spec.md` через короткие serialized direct-main transactions. До первого Linear handoff нет `seal`, persistent goal, immutable-spec state, implementation branch или implementation worktree. После handoff он может опубликовать только новую явно утверждённую revision той же source pair и не меняет Linear сам. Каждая revision, предназначенная для synchronization, явно назначает каждому source task один stable task key. Replacement work получает новый key в source до handoff; downstream provider не создаёт и не изменяет task keys.

Repository `project-goals` использует только canonical checkout ветки `main`. Coordination task branches, linked worktrees, bootstrap manifests, project-local `.spec/`, task-artifact copies и symlinks запрещены. Canonical repository identity и полный source-directory path образуют durable Linear Project identity. Exact published Git commit выбирает immutable revision этого directory и его children `goal.md`/`spec.md`; URL отдельного файла не заменяет directory identity. После handoff current execution changes принадлежат Linear, а later approved commit синхронизирует новую revision того же Project только через `task-graph-sync`.

`Cross-repository provider cutover`, который одновременно меняет `agent-plugins` и root contracts `project-goals`, остаётся одной implementation task с двумя явно различимыми publication surfaces. Её visible `Delivery` перечисляет обычный pull-request block для `agent-plugins` и отдельный direct-main block для `project-goals/AGENTS.md` и `project-goals/DESIGN.md`. Direct-main block не создаёт task branch, worktree или pull request и не входит в composite PR candidate list. Root `DESIGN.md` владеет sequencing. Card schema владеет только visible representation.

Cutover использует один exact ordered owner sequence:

1. Implementation завершает и публикует exact green `agent-plugins` PR candidate.
2. Implementation получает тот же workspace-global direct-main lock, который сериализует source authoring. Она перечитывает clean canonical `project-goals/main` с `HEAD == origin/main` и использует этот commit как observed pre-push main текущей transaction.
3. Initial publication создаёт не более одного detached-index commit, который меняет только два объявленных root contract path. Его обычный Git subject содержит exact issue token `AND-47`. Non-force compare-and-swap push публикует commit, canonical checkout fast-forward-ится, а exact commit и diff перечитываются. Если attempt прерывается после push, этот же результат принимает recovery rule ниже.
4. Direct-main publication является последней mutation перед `Review`. Handoff содержит direct check с permalink exact `project-goals` commit и обычный exact PR candidate. Independent Review перечитывает оба результата как один ordered owner set. Изменение любого результата возвращает task в `Rework`.
5. Zero-finding Review связывает exact direct-main commit и PR base/head. `task-merge` требует, чтобы current `project-goals/origin/main` всё ещё равнялся reviewed commit, и только затем merge-ит exact reviewed `agent-plugins` PR.
6. После merge canonical plugin cachebuster/reinstall устанавливает exact merged provider. Fresh discovery обязан показать `task-graph-sync` и отсутствие old discoverable `task-graph-create` до любой synchronization mutation.
7. Уже установленный `task-graph-sync` выполняет migration текущего Project, все требуемые inactive staging и activation mutations, затем complete provider readback. Только после этого owning attempt публикует terminal handoff и завершает `AND-47`.

Между direct-main publication и успешным fresh discovery `project-goals` fail closed на отсутствующем required skill. Единственное pre-install исключение принадлежит exact existing issue `AND-47` со stable task key `project-goals-linear-project-sync-implementation`. Его authority состоит из live process-lifetime `AND-47` attempt guard и natural current Linear, Git и GitHub provider state. Она не зависит от того, что current issue status всё ещё равен `Rework`: fresh recovery продолжает attempt, которая стартовала из `Todo` либо `Rework` и до crash уже перешла в `In Progress`. Latest semantically provider-read independent Review handoff, когда он существует, фиксирует reviewed PR URL, base, head branch, reviewed head commit и уже опубликованный reviewed direct-main commit. Этот старый handoff не обязан называть новый correction head. Ordinary `project-goals` mutation остаётся disabled. Исключение не разрешает `goal-brainstorm`, новый skill, task, branch, worktree, pull request, source-file edit или иной compatibility route.

До первого Review direct-main anchor initial publication recovery работает под тем же guard и workspace-global lock. Если observed pre-push main равен `B`, retry принимает только один exact `origin/main` tip `D`: у `D` один parent `B`, его ordinary Git subject содержит exact issue token `AND-47`, diff `B..D` содержит точно `AGENTS.md` и `DESIGN.md`, а их contents семантически соответствуют уже опубликованному current PR candidate. До local fast-forward `B` обязан быть clean canonical `HEAD`; после уже выполненного fast-forward тот же proof получает `B` из единственного parent `D` и требует local `HEAD == origin/main == D`. Recovery fast-forward-ит checkout только когда это ещё требуется, перечитывает exact commit и diff и никогда повторно не push-ит accepted `D`. Remote state, отличный от `B` или такого единственного `D`, является drift.

Confirmed independent Review finding разрешает только одну correction sequence. Current PR сохраняет reviewed URL, base и head branch; reviewed head является exact ancestor current head, а intervening diff содержит только bounded owner correction, которая семантически устраняет тот же finding. Reviewed direct-main commit является correction base `R`. Под shared lock `origin/main` обязан быть `R` либо одним successor `C`, чей единственный parent равен `R`, diff содержит точно `AGENTS.md` и `DESIGN.md`, а current contents семантически соответствуют corrected PR candidate. В первом случае attempt может опубликовать не более одного такого `C`; во втором она принимает `C`, fast-forward-ит canonical checkout при необходимости и выполняет exact readback без повторного push. Второй successor, другой parent, другой path, unrelated PR change или unresolved finding является drift. Новый Review связывает current corrected head и `C` либо доказанный semantic no-op; old finding handoff остаётся anchor reviewed inputs, а не списком будущих outputs.

Каждая fresh guarded `AND-47` recovery сначала перечитывает current status, latest provider-marked handoffs, exact PR и branch history, local и remote `project-goals/main`, merged state, installed provider discovery и current Project. Она продолжает первую незавершённую фазу и не повторяет уже принятый provider result. Это одинаково закрывает crash после dispatch, corrected PR push, initial или correction direct-main push, canonical fast-forward, Review handoff publication, merge preflight или merge, provider install и final readback. Current handoff принимается без повторной publication, когда parsed provider marker и все fields, которые управляют следующей transition, семантически совпадают; exact merged PR не merge-ится снова; already installed exact merged provider с правильным discovery не переустанавливается; incomplete installation reconciles canonical cachebuster/reinstall; completed migration, activation и final readback только перечитываются. Если terminal handoff и terminal Linear state уже существуют, recovery выполняет только guarded final provider readback. Attempt никогда не освобождает borrowed или foreign guard; process exit после final readback освобождает только её собственный guard. Любая неоднозначность, multiple successor либо semantic mismatch останавливает recovery. Alias, forwarding skill, journal, receipt, fingerprint и dual compatibility wording запрещены.

`linear-agent-tools:workflow-configure` явно подготавливает exact Linear destination и explicit GitHub repository/base: team-level issue statuses в fixed categories, workspace-level Project statuses `Planned`, `In Progress`, `Completed`, `Canceled`, role labels, required `agent:codex` label, полное отсутствие team Git status automations, merge-mechanism-compatible branch protection и repository merge policy с `delete_branch_on_merge=false`. GitHub integration остаётся включённой только как официальный issue-to-branch/PR link owner; PR events не меняют task status, потому что lifecycle transitions, evidence gates и cleanup принадлежат provider workflow. GitHub protection read связывает repository/base с authenticated login, numeric/node identity и exact write permission и строит closed mutation-complete snapshot classic protection, fully paginated active effective rules, full ruleset identities, bypass actors и required-check definitions. Snapshot явно содержит PR/review gate, strict checks, linear history, signatures, conversation resolution, lock, restrictions, force/delete/admin enforcement, merge queue и каждый известный applicable ruleset type; unknown, incompatible, missing, disabled, ineffective, bypassed, malformed или failed provider state отвергается и не может вернуть configuration action `none`. Для absent protection approved exact path может создать только minimal classic no-bypass protection без human PR gate и required checks для exact reviewed-base CAS `merge`, а existing protection не ослабляется. Отдельный principal-bound repository-policy plan показывает complete current и desired snapshots; approved apply повторно проверяет их exact equality, изменяет только `delete_branch_on_merge=false`, когда это требуется, и принимает success только после complete exact mutation response и fresh final readback. Stale, foreign, partial, malformed или failed policy state останавливает readiness. `squash`/`rebase` protection inspection требует existing strict up-to-date protection и хотя бы один required check, но `task-merge` fail closed до mutation или terminal certification, пока equally exact immutable strategy proof не реализован. `workflow-configure` показывает exact typed global delta, применяет только утверждённые natural destination/status/automation identities и требует owner readback. Standard status с правильными name/category принимается, missing status создаётся, conflicting same-name state останавливает configuration; compatibility status migration и alternate status vocabulary отсутствуют. Он не создаёт task graph и не меняет Product source. Official Linear MCP и user-level OAuth используются первыми; missing provider operation доказывается contract probe до появления minimal Linear-specific GraphQL boundary.

For an approved GitHub configuration transaction, `workflow-configure` requires each separate second pre-mutation protection or repository-policy and execution-principal snapshot to equal the approved typed snapshot exactly. Final readback is valid only for the same approved repository, GitHub login, numeric user ID and node ID and the exact desired policy with automatic branch deletion disabled. Normal merge and recovery each run one fresh successful behavioral proactive-authentication probe immediately before every individual authenticated GitHub fetch, push and ref-readback command; one probe authorizes exactly one such command.

`task-merge` owns a separate reproducible Git transport host boundary for Ubuntu 24.04 amd64. An explicit one-time provision operation downloads one exact URL, byte count and SHA-256-pinned Git 2.54.0 Noble package without ambient proxy resolution, atomically installs it in a private versioned path under the standard OS-user `HOME`, and rereads its executable build. Candidate review requires that runtime before code can enter `Merging`; merge and recovery inspect it again before dispatch. Neither path provisions implicitly or accepts an executable override. Every semantic Git command resolves to the installed absolute executable and matching libexec path, so closed `/usr/bin:/bin` `PATH` cannot select the older host Git; package identity and version are setup evidence, while the fresh first-request behavior probe of that same executable remains runtime authority.

`linear-agent-tools:task-graph-sync` принимает явно утверждённую revision одного `project-goals` source directory, показывает complete bounded issue graph и синхронизирует один Linear Project. Semantic Project identity является парой canonical credential-free repository identity и полного normalized repository-relative directory path. Basename directory всегда является display name Project; name drift exact identity match исправляется in place, но совпадение display name не разрешает adoption. Latest synchronized full Git commit является revision metadata, а не частью Project identity. После unique lookup exact Linear Project UUID становится mutation identity текущей synchronization. Zero matches создаёт один `Planned` Project, one match переиспользует его, а multiple matches останавливают operation как data conflict. Malformed либо incomplete source metadata не становится zero matches и требует explicit repair. Другой coherent outcome получает другой source directory и другой Project.

После canonicalization source и до первого Linear Project lookup synchronization делает non-blocking acquisition одного process-lifetime host-local guard по exact Project source identity. Namespace выводится только из explicit `LINEAR_AGENT_WORKSPACE_ROOT`, canonical repository identity, полного directory path и закрытого synchronization purpose. Busy guard останавливает attempt до mutation без ожидания. Guard удерживается через Project creation, все mutation phases и final provider readback; process exit является единственным release. Он не создаёт persisted lock row, journal, fingerprint, provider metadata или generic lock database. В текущем local workflow этот guard сериализует zero-match creation и retry одного source directory, поэтому две local attempts не могут создать duplicate Projects.

Source boundary в existing `git_origin` owner один раз сводит поддерживаемые GitHub HTTPS, SSH URL и SCP remote forms к transport-independent `github.com/<owner>/<repository>` identity без `.git`; другой host требует отдельного явно утверждённого provider rule. Directory boundary один раз получает normalized POSIX repository-relative path и full commit object ID. Эти boundaries отвергают credentials, query, fragment, encoded path separators, empty/dot segments, backslash и path escape, проверяют exact `goal.md`/`spec.md` siblings в выбранном commit и требуют совпадения repository, commit и directory частей одной source link. Linear transport один раз преобразует provider response shapes, issue references и URLs в typed Project UUID, issue UUID либо canonical team identifier. Внутренние owners не принимают альтернативные представления и не выполняют fallback normalization. Один и тот же Project не получает local mapping, import document, delta document, source/graph fingerprint, transaction receipt или synchronization journal.

Task identity является парой exact Project UUID и stable task key, явно присутствующего в approved source revision. Title, card content, source commit, Linear issue number и content hash не входят в identity. Stable-key lookup строит collision index по всем issues этого Project, включая archived, `Done` и `Canceled`. Current и mutable cards предоставляют key только через canonical final line card contract. Уже terminal card может предоставить тот же semantic slug через narrow read-only historical decoder, exact syntax которого принадлежит `plugins/linear-agent-tools/lib/task_graph/issue-contract.md`. Missing, malformed, multiple или conflicting decoder input останавливает complete synchronization до mutation. Для terminal issue synchronization читает только его immutable issue identity и decoded task key; остальная prose, comments, attachments, documents и history не входят в mutable synchronization или recovery input и не изменяются. Такая Project-plus-key identity остаётся зарезервированной навсегда и никогда не считается zero match. Zero key matches создаёт issue с source-provided key без generated suffix, one match сохраняет exact issue UUID, comments, attachments и history, а duplicate key останавливает synchronization. Replacement получает в approved source новый distinct key; после его появления interrupted retry разрешает replacement только по этому же новому Project-plus-key и никогда не переиспользует зарезервированный terminal key. Linear statuses, role и dispatch labels, assignee либо delegate, comments, attachments и native blocker relations являются единственным operational graph state. Git и GitHub остаются владельцами branch, commit, PR, check, review и merge identities.

После полного semantic plan synchronization получает guards всех existing issues, чьи cards, relations, assignment, labels, status или dispatchability могут измениться. Он использует тот же exact issue attempt-guard namespace, что и task attempts, сортирует canonical Linear identifiers и получает guards только в этом порядке. Единственное исключение применяется к self-migration, которую owning guarded attempt запускает для своего Project, когда exact caller issue входит в affected set. Caller передаёт live guard handle своего issue. Synchronization проверяет его namespace, issue identity и непрерывное ownership, считает этот один guard уже held и никогда не пытается получить или освободить его повторно. Boolean flag, issue prose, environment claim, missing handle, foreign owner или другая issue identity не являются ownership и останавливают operation до mutation. Поскольку outer attempt уже удерживает caller issue guard, source guard и все остальные affected issue guards всегда получаются non-blocking. Busy source либо issue guard немедленно останавливает synchronization, освобождает только guards, полученные этой synchronization, и никогда не ожидает guard, owner которого может ожидать caller issue. Это исключает reversed-order deadlock. Все остальные affected issue guards пробуются в обычном sorted order. После acquisition synchronization заново читает complete Project state и вычисляет plan повторно. Mutation допустима только когда affected set этого reread точно равен acquired guard set с caller issue, заменённой validated borrowed handle, если она affected; любой drift останавливает attempt до mutation, а fresh retry заново получает полный sorted set. Новая issue создаётся только non-dispatchable в `Backlog`; сразу после provider возвращает её canonical identifier synchronization получает её issue guard до relation, metadata или activation mutation. Source guard, applicable borrowed caller guard и все acquired issue guards удерживаются до final readback. Synchronization не закрывает borrowed handle; interruption оставляет его owning attempt, а process exit после outer final readback является единственным release. Эта ordering исключает reentrant acquisition, race с active task attempt и operational state вне native Linear objects.

Project synchronization использует следующие lifecycle rules:

| Current Project state | Synchronization rule |
| --- | --- |
| Missing | Создать один Project в `Planned`, stage весь graph и выполнить `Planned -> In Progress` только после complete readback. |
| Archived | Сначала восстановить тот же Project, затем применить rule его current Project status. |
| `Planned` | Продолжить interrupted staging из semantic current state; не создавать replacement Project. |
| `In Progress` | Оставить существующие задачи operational. Все новые, replacement и update-eligible issues сначала stage в `Backlog` без `agent:codex`; `Todo` является последней activation mutation после complete relation и metadata readback. |
| `Completed` или `Canceled` | Сначала вычислить complete semantic plan без status mutation. Если меняется только revision metadata, сохранить terminal status и обновить только latest synchronized commit. Перевести тот же Project в `Planned` только когда approved source содержит actual new или replacement graph work; затем stage это work и выполнить `Planned -> In Progress` после complete readback. |
| Absent, foreign или unsupported status value | Остановить synchronization без mutation. |

Complete semantic graph plan всегда вычисляется и перечитывается до изменения terminal Project status. Одинаковая approved commit revision с уже совпадающим semantic state является no-op. Одинаковая revision после interruption всё равно reconciles current state и не принимает revision metadata как completion proof. Новая commit revision без new или replacement graph work обновляет только latest synchronized commit под тем же source guard и сохраняет `Completed` либо `Canceled`. Последняя synchronized commit для actual graph work записывается после всех issue activations, а для `Planned` Project — перед final activation. Crash recovery повторно читает полностью paginated Project, issues, labels и blocker relations и продолжает следующую semantic phase без receipt или expected historical prose.

Existing issue с неизменившимся semantic task contract не мутируется. Changed issue в `Backlog` или `Todo` сначала становится non-dispatchable, затем обновляется in place и активируется только после readback. Active или terminal issue под старым key считается immutable execution identity: incompatible change того же source task под этим key отвергает approved revision до mutation. Source должен сохранить old task semantics либо добавить replacement task с новым distinct stable key, явно записанным в той же approved revision. Synchronization создаёт и повторно находит replacement только из этого exact source-provided key, сохраняет reserved old key, делает old issue blocker replacement, а replacement blocker current downstream Review. Terminal issues никогда не reopen и не меняют delivered history. Удалённая новой revision inactive issue может перейти в `Canceled` только как часть явно утверждённого synchronization plan; removal active issue требует отдельного explicit human cancellation decision, а terminal issue сохраняется без mutation.

Каждая revision имеет не более одной non-terminal downstream chain: один owner-oriented `task:review`, затем один final `task:acceptance`, затем применимый cleanup. Все новые и replacement work блокируют этот Review, а Review блокирует acceptance. Inactive existing gates могут получить новые blockers. Synchronization не забирает running gate у owning attempt и останавливается, пока этот owner не вернёт gate в безопасное inactive state. Terminal Review, acceptance и cleanup не reopen, поэтому later revision получает новые meaningful task keys. Старые terminal chains остаются Linear history.

Linear Project является task container одного согласованного source outcome, а не представлением Git repository. Одна issue обычно изменяет один repository, но неделимая issue может перечислять несколько repositories; один Linear Project поэтому может охватывать несколько Git repositories, а один Git repository может участвовать в нескольких независимых Linear Projects. Связь с Git хранится только на уровне конкретных issue через canonical repository/base/branch/PR identities.

Один plugin-owned card contract является единственным владельцем visible card shape, conditional sections и их omission rules. Stable architecture требует от него human-readable semantic task contract, stable task key и exact delivery kind для каждой implementation issue. Code delivery обязан назвать repository, base branch и merge method; evidence delivery не содержит неприменимые Git fields. Operational Linear state не дублируется в card. Linear issue является canonical per-task goal и execution journal.

Linear statuses образуют единый workflow: `Backlog`, `Todo`, `In Progress`, `Review`, `Rework`, `Merging`, `Done`, `Canceled`. `Todo`, `In Progress`, `Review`, `Rework` и `Merging` являются active states, но `Review` dispatchable только для independent Codex review `task:implementation`; final acceptance в том же status остаётся единственной human boundary и non-dispatchable. Zero-finding code review переводит implementation в `Merging`, evidence implementation — в `Done`, finding — в `Rework`. `Done` и `Canceled` являются terminal states. Blockers, required label, exact assignee или delegate, совместимая пара task role/delivery и complete task contract определяют explicit `dispatchable`; `Merging` допускает только `task:implementation` с `code`, отдельный blocked status не создаётся.

Каждая top-level agent attempt до dispatch или status mutation получает один process-lifetime host-local lock по exact Linear issue и удерживает его до attempt-resource cleanup и final Linear read-back; crash освобождает kernel lock. Namespace lock выводится только из explicit canonical multi-repository `LINEAR_AGENT_WORKSPACE_ROOT`, issue и purpose; workspace root не может быть Git repository/worktree, а CWD, repository и task worktree не меняют identity. Nested cleanup переиспользует guard вызывающей attempt, а отдельный short-lived operation lock сериализует только Git workspace и cleanup transactions. Каждая code-mutating issue использует branch `linear/<lowercase-issue-identifier>` и project-local `.worktree/<lowercase-issue-identifier>`. `linear-agent-tools:task-implement` владеет creation, adoption, bootstrap, crash recovery и fresh-thread implementation attempts. Первый prepare фиксирует exact remote-base baseline; `Rework` принимает existing branch/PR без destructive reset. `task-review` независимо выводит complete coverage и не меняет Product code. Candidate-review findings возвращают implementation в `Rework`; findings против merged state принадлежат review/acceptance no-fix boundary, а новые remediation blockers добавляет `task-graph-sync` через approved synchronization plan. `task-merge` работает только с ordered composite PR candidate list из zero-finding review. Каждый элемент содержит URL, base branch, base commit и head commit. Historical `CLOSED`-unmerged PR не считается merge evidence и не блокирует создание одного replacement open candidate для того же deterministic branch/base. `task-merge` проверяет same-repository head и отвергает effective merge-queue rules как deferred mutation против later base. Для open PR absent auto-merge request, closed protection/check snapshot и exact executing principal проверяются до mutation. Каждый merge-time Git/`gh` process получает closed allowlisted environment со standard OS-user `HOME`, unset `CODEX_HOME`, disabled prompts, `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_SYSTEM=/dev/null`, disabled system attributes и без inherited authentication, config, askpass, proxy, URL rewrite, hook, transport, replace-ref или alternate-object controls. Task worktree остаётся только read-only audit source: ordinary config bytes один раз snapshot-ятся без includes, canonical origin и unsafe keys/state проверяются, после чего object lookup/construction, network и recovery никогда не выполняются в task repository и later local-config mutation не влияет на них. Provider создаёт private mode-`0700` temporary bare repository, удаляет generated local config, требует отсутствие hooks, alternates, grafts, replace refs, shallow state и `info/attributes`, задаёт `core.attributesFile=/dev/null` и fetch-ит exact reviewed refs только из explicit canonical GitHub HTTPS URL. Construction требует, чтобы reviewed base был ancestor exact reviewed head, и использует exact head tree напрямую без merge machinery или drivers. Complete relevant repository identity/status/merge-policy fields строго читаются под fresh exact principal checks непосредственно до construction и повторно непосредственно до push; selected method должен быть enabled, automatic head-branch deletion — disabled, snapshots — равны, а missing, malformed, inactive, identity-conflicting или drifted policy останавливает transaction до ref mutation. Invocation-local credential helper bound к exact HTTPS host/path, получает named token только для этого destination, передаёт token в GitHub `/user` только как stdin-fed `/usr/bin/curl -q ... --config -` header, проверяет exact login, numeric user ID и node ID из approved protection/write-authority snapshot и только затем передаёт credential Git; token не входит в Python process, any process argv/environment, files, logs, state или evidence, а persistent credential config не меняется. Absolute `/usr/bin/curl` и `/usr/bin/jq` являются runtime prerequisites вместе с behaviorally supported Git 2.46 или later. Push использует inert hooks path и `--no-verify`, exact reviewed-base old-OID lease и публикует подготовленный exact two-parent no-ff commit только в base ref; open PR head не обновляется и не удаляется. Immediate readback доказывает prepared merge commit в base и unchanged exact reviewed commit в head. GitHub не предоставляет atomic transaction между REST repository policy и Git receive-pack, поэтому second fresh policy read закрывает observed drift, но оставляет unavoidable narrow metadata-change race до push; policy и refs нельзя описывать как одну atomic provider operation. CAS требует exact zero required-check definitions, потому что новый merge commit не может получить provider checks до push. `squash`/`rebase` fail closed до mutation, пока для них не реализован equally exact immutable strategy proof. Typed valid zero set является CAS-only; generic exit `1`, empty/malformed output, protection absence/disable/bypass не превращаются в success. Public terminal inspection не возвращает verified success из generic merged metadata: GitHub обязан показать PR как `MERGED`; `CLOSED` без merged state никогда не является success. Already-merged `merge` recovery не зависит от later policy/protection/check definitions, mutable title либо advanced current base tip; новый identically closed private repository доказывает reviewed ancestry, exact reviewed head tree, ordered parents, exact REST terminal login/numeric user ID/node ID, canonical destination и retained exact reviewed source head. Только после terminal provider/Git readback и terminal issue transition ordinary idempotent issue cleanup удаляет task branch; branch, attached к open PR, не удаляется никогда. Reviewed-base lease rejection либо changed base branch/base commit/head до successful mutation требует nested cleanup и `Rework` без fix-forward. Per-PR human approval workflow отсутствует. Каждый graph завершается exact cleanup, а Product commit/push остаются у `agent-workflows:git-commit`.

Успешная verification переиспользуется только после direct current proof, что result-affecting source, exact command, environment/release и semantic contract не изменились. Это semantic решение agent, а не persistent receipt, candidate fingerprint либо generic invalidation gate. Targeted checks выполняются после coherent owner slices, directly applicable complete deterministic checks — на frozen result, а fresh complete semantic owner audit — после последнего fix. Behavior evaluation использует только current failed IDs; после одного owner-level root correction rerun получает только immediately preceding failed subset, passed cases остаются принятыми в cycle, а semantic contract определяет fix provider или case/judge. Empty failed list означает zero model cases; full corpus не запускается без отдельного owner task.

Review принимает finding только для semantic defect либо root canonicalization/ownership correction. Предложение добавить check, wrapper, metadata, fingerprint или compatibility path без concrete protected boundary отклоняется. Каждый pass собирает все current justified findings в один owner-oriented set и объединяет manifestations одного root cause. Remediation изменяет owning implementation, после чего новый fresh Review снова выводит complete coverage; цикл завершается только zero-finding pass. Candidate, который меняет собственный Review либо lifecycle provider, проверяется fresh generic `gpt-5.6-sol` max thread по branch-local contracts и exact base-to-head diff; implementation thread и installed/cached provider под review не являются review authority.

Linear status history владеет wall-clock lifecycle. Каждая attempt boundary сначала завершает nested attempt cleanup, затем добавляет один provider-marked minimal human-first handoff. Первым значением является concise human summary. Handoff добавляет только nonempty outcome values для следующего transition: ordered composite PR candidates, direct check results с optional evidence links и optional merged commit внутри соответствующего candidate. Linear уже владеет issue, role, delivery, status, outcome history и timestamps. Handoff не повторяет эти значения, cleanup state, UUID, schema version, отдельные commit/PR maps или empty collections. Он служит context для semantic recovery, а не approval object или automatic cache. Provider readback fully paginates comments, parses the marker once at the boundary and compares only typed fields consumed by the next semantic transition; raw Markdown byte equality is not proof. Непустое closed subset доступных exact structured `input_tokens`, `cached_input_tokens`, `cache_write_input_tokens`, `output_tokens`, `reasoning_output_tokens` агрегируется и валидируется независимо; usage object отсутствует только когда counters не exposed, а unknown, invalid, empty, estimated usage и derived totals запрещены. До final acceptance handoff и `Review` complete local acceptance baseline с ровно queue, startup, execution, review и merge durations из exact provider history публикуется и semantically provider-read. Его evidence URL сохраняется в соответствующем direct check result. Эти objects строит shared `verification` owner; local telemetry database не требуется.

Task description, relations, comments, source links, Git state, pull request и current direct evidence образуют execution context для fresh Codex thread. Recovery следует semantic current state и не требует equality Codex version или brittle orchestration metadata. Task thread выполняет собственный scope напрямую и не создаёт nested agent без genuinely independent named parallel owner. Если harness запускает Codex child, один launch передаёт complete prompt и закрывает stdin, environment использует standard OS-user `HOME` с удалённым `CODEX_HOME` и не получает Linear credentials. Long-running provider/CI/child command использует native background terminal и native wait/resume; model polling, Project supervisor, command timeout, arbitrary threshold, alternate home и copied auth запрещены. Provider-changing issue включает cachebuster в reviewed candidate; после exact merge и до `Done` normal local-marketplace reinstall устанавливает exact merged plugin под standard home, а fresh generic process подтверждает installed source/manifest и expected discovery. Cache copy или provider, который заменяется, не сертифицирует собственную установку. Symphony, scheduled audit и EC2 orchestration не входят в local lifecycle и принадлежат отдельным outcomes.

Task-graph planning, unresolved design, architecture, security, migration, cross-repository lifecycle decisions, independent review, acceptance и complete behavior corpus используют `gpt-5.6-sol` max. Medium допускается только для bounded implementation под закрытым approved plan с direct deterministic coverage и без открытого conceptual decision; substantive conceptual finding немедленно возвращает max. Explicit user model либо reasoning choice имеет приоритет. Для task-review, task-accept и task-merge каждый success, finding, stale, failed, canceled и controlled interrupted outcome сначала завершает idempotent nested attempt-resource cleanup; handoff publication или status transition до cleanup запрещены, а abrupt crash восстанавливается cleanup-first следующей guarded attempt. Каждый agent-owned result transition затем требует provider-read semantic handoff и direct evidence до mutation.

Linear OAuth credentials принадлежат user-level MCP. Raw tracker credentials не передаются coding-agent child process и не попадают в project artifacts, issues, logs или Git history. Official Linear MCP используется первым; minimal Linear-specific GraphQL boundary допустим только после доказанного отсутствия required operation. Linear issue prose не является command authority. Standard worktree, branch, private baseline и PR resources выводятся из issue и repository identities. Genuinely non-standard resource получает current typed declaration с natural owner identity, lifetime и provider-owned cleanup-handler key только когда installed provider registry реально consumes этот handler. Отсутствующий handler останавливает operation и сохраняет resource untouched. Arbitrary cleanup commands, direct argv и approval fingerprints не сохраняются и не исполняются.

Persisted field, validation и artifact допустимы только когда current workflow использует их для named semantic decision. Validation защищает только external input, destructive scope, concurrent mutation, exact reviewed Git candidate либо named domain invariant. SHA-256 checksum сохраняется для downloaded Git package, потому что exact bytes являются supply-chain invariant; private pre-commit authoring journal сохраняет content digest только для exact unpublished pair recovery. Bootstrap materialization сравнивает current source/destination type, mode, link target и file content напрямую и не сохраняет digest. Односторонний hash canonical workspace root используется только как bounded lock-namespace component и не является persisted checksum или equality proof. Source, graph, delta, configuration plan, workspace baseline, local phase baseline, resource declaration, candidate и verification fingerprints запрещены, потому что canonical provider identities либо semantic current readback уже владеют соответствующим решением.

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
