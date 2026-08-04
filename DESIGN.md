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
- `agent-workflows:goal-brainstorm` является единственным нормативным владельцем harness-neutral task artifacts и их lifecycle;
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

Все live local directories, symlinks и configured paths, которые представляют checkout, worktree, marketplace source, установленный plugin или cache repository, должны соответствовать canonical имени. Repository checkout, worktree и marketplace-source copies используют имя `agent-plugins`; installable-plugin source и cache copies используют identifiers `agent-workflows`, `marketplace-agent-tools` и `workflow-container-agent-tools`.

Cutover не переписывает Git history, reflogs, завершённые logs, immutable task history или другие historical evidence только ради удаления прежнего имени. После перехода old identity допустима исключительно в таком явно историческом содержимом; active URL, filesystem owner path, configuration key, provider metadata или discovery result со старым именем является незавершённым cutover.

Repository создаётся до consumer cutover, получает canonical remote, default branch и требуемую provider structure, после чего должен пройти standalone verification и fresh-consumer discovery. Consumer dependency не переключается на ещё не проверенный repository.

Repository удаляется только после доказанного отсутствия active consumers, gitlinks, configured remotes, deployment references и required data. Удаление использует exact full repository name и запрещает prefix, wildcard или inferred bulk selection. Требуемый GitHub authorization scope проверяется непосредственно перед mutation; scope `delete_repo` добавляется через authenticated GitHub CLI flow, если он отсутствует. Credentials и authorization tokens не записываются в project artifacts или отчёты.

Удалённый repository не воссоздаётся как compatibility bridge, mirror или forwarding owner, если paired specification явно не определяет новый lifecycle.

## Agent Plugins Marketplace

`agent-plugins` является marketplace repository, а не installable plugin. Он содержит три независимо устанавливаемых plugins.

```text
agent-plugins/
  .agents/plugins/marketplace.json
  plugins/
    agent-workflows/
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
- `goal-checkpoint`;
- `goal-delete`;
- `goal-merge`;
- `goal-review`;
- `instruction-audit`;
- `instruction-migration`;
- `sequential-batch`.

Plugin `workflow-container-agent-tools` владеет skills:

- `workflow-container-audit`;
- `workflow-container-developer`;
- `workflow-container-input-create`.

Plugin `marketplace-agent-tools` владеет reusable marketplace-specific skills, references и agent tools, включая `ozon-seller-api-developer`. Его имя намеренно не совпадает с существующим application repository `marketplace-tools`.

`goal-brainstorm` принадлежит `agent-workflows`, потому что его specification и goal lifecycle применим к проектам без workflow-container.

Общие `explain`, `section-audit`, `sequential-batch` и `subagent-transport` mechanics принадлежат plugin-local support owners внутри `agent-workflows`. Public `agent-workflows:sequential-batch` является stable dependency для project-local workflows, которым нужны эти mechanics. Consumer projects не содержат их копии. Opinionated audit cards и mechanical code-standard checks не принадлежат `agent-workflows`; они поступают из выбранных `project-standards` skills и project-local contracts.

Plugin-local support owner размещается в `plugins/<plugin>/lib/<owner>/`. Переиспользуемый Python-модуль, вызываемый skill-local script, принадлежит непосредственно этому owner path. Каталог `tool/` существует только для исполняемых CLI entrypoints самого support owner, а `tool/lib/` — только для реализации, общей для нескольких таких entrypoints. Тонкий исполняемый интерфейс, принадлежащий workflow skill, размещается в `skills/<skill>/scripts/`. Структура каталогов определяется реальным владельцем и направлением вызова, а не копируется с другого support owner.

Workflow входит в `agent-workflows` только по явно утверждённому пользователем source-to-target решению. Для утверждённого переноса workflow обязан:

- сохранять утверждённую reusable семантику вне исходного project;
- не содержит абсолютных workspace paths, имён конкретных application projects и закрытых domain contracts;
- принимает repository или workspace scope через явную границу;
- владеет полноценной повторяемой процедурой, а не только перенаправляет на один project document;
- поставляется со всеми необходимыми references, templates, tools и tests.

`agent-plugins` не предоставляет отдельный Python package или CLI для поиска соседних workflow-container projects. Plugin installation, skills и owner-local agent tools являются полным public provider surface; параллельный локальный discovery path отсутствует.

## Goal Brainstorm Worktree

`agent-workflows:goal-brainstorm` владеет подготовкой изолированного task worktree для каждого persistent implementation goal. Его stable contract состоит из skill entrypoint, `plugins/agent-workflows/skills/goal-brainstorm/references/specification-contract.md` и `plugins/agent-workflows/skills/goal-brainstorm/references/worktree-contract.md`. Checkpoint publication, merge и deletion принадлежат отдельным skills `agent-workflows:goal-checkpoint`, `agent-workflows:goal-merge` и `agent-workflows:goal-delete`; `goal-brainstorm` не становится скрытым владельцем этих lifecycle operations.

Harness-neutral task artifacts хранятся только в tracked repository `project-goals`. Один common prefix владеет каталогом `<common-prefix>/` с точными файлами `spec.md`, `goal.md` и `checkpoint.yaml`. Project repositories не создают `.spec/`, task-artifact symlinks или копии этих файлов. Task branch и linked-worktree directory используют common prefix только в participating implementation repositories; linked worktrees остаются под project-local `.worktree/<common-prefix>/`.

Repository `project-goals` использует только canonical checkout ветки `main`: coordination task branch, linked worktree и bootstrap manifest запрещены. `goal-brainstorm` создаёт и пересматривает `spec.md` и `goal.md` через короткие сериализованные direct-main transactions, которые commit-ят и push-ят только exact task-directory delta и возвращают checkout в clean synchronized state. `seal` связывает exact published `main` commit и fingerprints consistent candidate, но не означает user approval. До `active` команда `revise` сохраняет тот же common prefix и task content, разрешает ordinary contract changes и расширение participant set, после чего весь authoring, semantic review и seal повторяются. Только явная activation persistent goal закрывает artifacts и task identity.

Каждый участвующий implementation repository и task-owned submodule имеет tracked root `worktree-bootstrap.yaml`. Closed schema задаёт resource copies/links и optional project-owned external cleanup command как direct argv без shell evaluation. Provider не выводит resource class из имени или content. Отсутствующий manifest создаётся только в task worktree текущей задачи. Формат следует `project-standards:project-foundation` и использует `.yaml`; `.toml` либо `.yml` для этого owner запрещены.

`goal-checkpoint` получает closing commits через `agent-workflows:git-commit`, проверяет clean pushed task branches и атомарно добавляет в `checkpoint.yaml` полный snapshot всех participating projects. Явная activation goal разрешает обычную closing publication, а уже запущенный `goal-merge` разрешает полный descendant fix-forward checkpoint после failed acceptance; отдельное разрешение для этих неразрушающих продолжений не требуется. Каждая entry содержит workspace-relative `project_path` и full `git_commit_final`; absolute paths запрещены. Один commit `project-goals/main` является атомарной публикацией связанного cross-repository commit set, но не утверждает, что последующий multi-repository merge атомарен.

`goal-merge` запускается отдельным эксклюзивным Codex thread и за одну merge operation обрабатывает ровно один selected checkpoint. Он проверяет exact origin commits и ancestry, последовательно выполняет только fast-forward merge каждого project task branch в `main`, затем проверяет полный result в постоянной primary development environment. Частично выполненный merge возобновляется идемпотентно по текущим main commits; automatic rollback запрещён. При acceptance failure pointer не меняется, а merger сохраняет journal и автоматически исправляет входящую в утверждённый scope неразрушающую проблему только в recorded task worktrees. После verification он публикует полный descendant fix-forward checkpoint и продолжает merge в том же exclusive workflow без нового разрешения. Новый checkpoint может supersede failed checkpoint только когда каждый intervening project commit доказан ancestor-or-equal его full snapshot. Workflow останавливается для отдельного решения только если исправление требует новых полномочий либо неизбежной потери данных, потому что lossless migration невозможна. Только после полной acceptance `goal-merge` обновляет `accepted_checkpoint_id` в `checkpoint.yaml` и публикует это изменение.

`goal-delete` запускается только по явному запросу пользователя удалить exact task. Этот запрос разрешает идемпотентное удаление всех оставшихся ресурсов внутри recorded task scope независимо от их clean/merged/pushed состояния и от ранее частично выполненной очистки. Уже отсутствующий in-scope resource является успешным шагом. Блокировать операцию можно только когда нельзя доказать exact ownership/scope, можно затронуть primary/shared/foreign resource либо нельзя честно доказать отсутствие известного ресурса. External cleanup и registry-state mutation выполняются из clean temporary checkouts current `origin/main`; unrelated dirty canonical main не является blocker и не входит в mutation. Затем удаляются worktrees, task refs и private lifecycle state. Каталог `<common-prefix>/` в `project-goals` остаётся постоянной registry записью, а `checkpoint.yaml` получает `task_resource_state: deleted`. Goal completion сам по себе deletion не запускает.

Private lifecycle state хранится в Git administration space participating repositories и связывает task identity с exact `project-goals` repository/commit/path, main and task roots, baseline commits, dirty-state fingerprints, manifests, caller attestations и durable transactions. Он не дублирует task file content. Tracked task changes создаются и проверяются только в recorded task roots. Возможная пользовательская работа не перезаписывается без явного решения.

Обычный worktree workflow не commit, push или merge Product source. `goal-brainstorm`, `goal-checkpoint`, `goal-merge` и `goal-delete` могут менять только свои явно owned coordination surfaces и используют один shared direct-main transaction contract для `project-goals/main`. Product source publication остаётся у `agent-workflows:git-commit` и никогда не происходит скрыто из `goal-brainstorm`.

## Domain Plugins

Reusable asset является domain-specific, когда его triggers, vocabulary, decisions, contracts или tools зависят от одной business или platform domain и не образуют general task workflow либо cross-domain engineering standard.

Каждый coherent domain использует один independently installable canonical domain plugin. Reusable domain skills, references, templates, tools и tests не копируются по consumer projects, repositories, vendor endpoints или отдельным tasks.

Domain plugin раскрывает reusable contract через один или несколько independently triggerable domain skills. Пустой plugin или plugin без skill entrypoint не является допустимым domain owner.

Классификация каждого skill, reference, template или agent tool как project-local либо reusable domain asset определяется явным пользовательским source-to-target решением. Количество текущих consumers, потенциальная будущая применимость и agent inference не заменяют такое решение. Если пользователь утвердил asset как reusable domain asset, он переходит в canonical plugin этого domain; если canonical plugin отсутствует, он создаётся до удаления исходного owner.

Generic task procedures и orchestration принадлежат `agent-workflows`. Cross-domain opinionated engineering standards принадлежат `project-standards`. Явно назначенные пользователем reusable workflow-container domain assets принадлежат `workflow-container-agent-tools`. Явно назначенные пользователем reusable marketplace domain assets принадлежат `marketplace-agent-tools`.

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

`skill_behavior_eval/corpus-v1.json` является версионированным набором direct, indirect, incomplete, negative и overlap scenarios для трёх plugins этого repository. Каждый case задаёт expected и forbidden activation и смысловые invariants ответа.

Общий runner принадлежит `project-standards:project-instruction-developer` и не копируется в этот repository. Он выполняет read-only generation и отдельное semantic judging на target model. Эта opt-in фаза обязательна при существенном изменении triggers, explicit-only policy, overlap boundaries или workflow output contract, но остаётся отдельной от plugin validator, skill validator и `pytest`.
