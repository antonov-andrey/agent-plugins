"""Behavior tests for task-graph validation and activation reconciliation."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
LIBRARY_ROOT = REPOSITORY_ROOT / "plugins" / "linear-agent-tools" / "lib"
if str(LIBRARY_ROOT) not in sys.path:
    sys.path.insert(0, str(LIBRARY_ROOT))

from task_graph.delta import TaskGraphDelta
from task_graph.model import TaskGraph, TaskGraphError
from task_graph.publication import DeltaPublicationView, GraphPublicationView
from task_graph.reconciliation.delta import TaskGraphDeltaReconciler
from task_graph.reconciliation.initial import TaskGraphReconciler
from task_graph.reconciliation.model import (
    PublicationPhase,
    RemoteDocument,
    RemoteIssue,
    RemoteProject,
)

TEAM_ID = "33333333-3333-4333-8333-333333333333"
ASSIGNEE_ID = "22222222-2222-4222-8222-222222222222"
PROJECT_ID = "44444444-4444-4444-8444-444444444444"


def _verification(key: str, kind: str = "targeted") -> dict[str, object]:
    """Return one valid verification payload.

    Args:
        key: Stable verification key.
        kind: Verification kind.

    Returns:
        JSON-ready verification step.
    """

    return {
        "key": key,
        "kind": kind,
        "repository_url": "git@github.com:antonov-andrey/example.git",
        "working_directory": ".",
        "command_argument_list": ["pytest", "-q"],
        "dependency_path_list": ["requirements-dev.txt"],
        "environment_identity_required": False,
    }


def _node(
    key: str,
    role: str,
    delivery: str,
    blockers: list[str],
    *,
    repository: bool = True,
) -> dict[str, object]:
    """Return one complete task node payload.

    Args:
        key: Stable node key.
        role: Task role label.
        delivery: Delivery kind.
        blockers: Stable blocker keys.
        repository: Whether relevant repository metadata is included.

    Returns:
        JSON-ready node.
    """

    return {
        "node_key": key,
        "title": key.replace("-", " ").title(),
        "outcome": f"Complete {key} with observable evidence.",
        "scope_list": [f"Own {key}"],
        "non_goal_list": ["Do not change unrelated owners"],
        "role": role,
        "delivery_kind": delivery,
        "assignee_id": ASSIGNEE_ID,
        "delegate_id": "",
        "repository_list": (
            [
                {
                    "origin_url": "git@github.com:antonov-andrey/example.git",
                    "base_branch": "main",
                    "merge_method": "merge",
                }
            ]
            if repository
            else []
        ),
        "partial_merge_recovery": "",
        "required_contract_list": ["source/spec.md#Required-Outcome"],
        "required_skill_list": ["project-standards:project-foundation"],
        "blocker_key_list": blockers,
        "resource_list": [],
        "verification_list": [
            _verification(
                f"verify-{key}",
                "semantic" if role != "task:implementation" else "targeted",
            )
        ],
        "human_decision_boundary": "Approve only the exact published candidate fingerprint.",
        "source_section_list": ["Required Outcome"],
    }


def _graph_payload() -> dict[str, object]:
    """Return one minimum complete graph payload.

    Returns:
        JSON-ready graph.
    """

    return {
        "schema_version": 1,
        "team_id": TEAM_ID,
        "project_name": "Acceptance Local Workflow",
        "source": {
            "kind": "project-goals",
            "canonical_url": (
                "https://github.com/antonov-andrey/project-goals/tree/"
                + "a" * 40
                + "/2026-08-04-acceptance-local-workflow"
            ),
            "revision": "a" * 40,
            "outcome": "Deliver one closed local workflow.",
            "content": "# Goal\n\nComplete the workflow.\n\n# Spec\n\nDetailed requirements.\n",
        },
        "node_list": [
            _node("implementation", "task:implementation", "code", [], repository=True),
            _node("review", "task:review", "evidence", ["implementation"]),
            _node("acceptance", "task:acceptance", "evidence", ["review"]),
            _node("cleanup", "task:cleanup", "cleanup", ["acceptance"]),
        ],
    }


@pytest.mark.parametrize(
    "canonical_url",
    [
        "https://github.com/antonov-andrey/project-goals/tree/main/example",
        "https://github.com/antonov-andrey/project-goals/tree/" + "a" * 40,
        "https://github.com/antonov-andrey/other/tree/" + "a" * 40 + "/example",
        "https://github.com/antonov-andrey/project-goals/tree/"
        + "a" * 40
        + "/example?mutable=1",
        "https://github.com/antonov-andrey/project-goals/tree/"
        + "a" * 40
        + "/example/goal.md",
    ],
)
def test_project_goals_source_requires_exact_commit_pinned_task_directory(
    canonical_url: str,
) -> None:
    """A mutable or repository-root link cannot masquerade as immutable source provenance."""

    payload = _graph_payload()
    payload["source"] = {**payload["source"], "canonical_url": canonical_url}

    with pytest.raises(TaskGraphError, match="project-goals canonical URL"):
        TaskGraph.from_payload(payload)


def _delta_payload(graph: TaskGraph) -> dict[str, object]:
    """Return one approved remediation delta payload.

    Args:
        graph: Original immutable Project graph.

    Returns:
        JSON-ready delta.
    """

    return {
        "schema_version": 1,
        "team_id": TEAM_ID,
        "project_id": PROJECT_ID,
        "project_key": graph.project_key(),
        "source": _graph_payload()["source"],
        "provenance": {
            "kind": "finding",
            "canonical_url": "https://linear.app/example/issue/AND-99",
            "revision": "comment-17",
            "decision": "Fix the accepted review finding and repeat all downstream gates.",
        },
        "existing_node_key_list": ["review", "acceptance", "cleanup"],
        "reverification_node_key_list": ["review"],
        "node_list": [
            _node(
                "remediation",
                "task:implementation",
                "code",
                [],
                repository=True,
            )
        ],
        "blocker_edge_list": [
            {
                "blocker_node_key": "remediation",
                "blocked_node_key": "review",
            }
        ],
    }


def _remote_issue_list(
    graph: TaskGraph, *, activated: bool, relations: bool
) -> list[RemoteIssue]:
    """Build deterministic remote issue snapshots from rendered content.

    Args:
        graph: Exact desired graph.
        activated: Whether node activation metadata is present.
        relations: Whether blocker relations are present.

    Returns:
        Remote issues.
    """

    view = GraphPublicationView.from_graph(graph)
    node_by_key_map = {item.node_key: item for item in graph.node_list}
    return [
        RemoteIssue(
            id=f"50000000-0000-4000-8000-{index:012d}",
            node_key=item.node_key,
            title=item.title,
            description=item.description,
            status_name="Todo" if activated else "Backlog",
            label_name_list=(
                [node_by_key_map[item.node_key].role]
                + (
                    ["agent:codex"]
                    if node_by_key_map[item.node_key].can_agent_execute()
                    else []
                )
                if activated
                else []
            ),
            assignee_id=item.assignee_id if activated else "",
            delegate_id=item.delegate_id if activated else "",
            blocker_key_list=(
                list(node_by_key_map[item.node_key].blocker_key_list)
                if relations
                else []
            ),
        )
        for index, item in enumerate(view.issue_list, 1)
    ]


def _remote(
    graph: TaskGraph,
    *,
    document: bool,
    issues: bool,
    relations: bool = False,
    activated: bool = False,
    project_status: str = "Planned",
) -> RemoteProject:
    """Return one staged remote Project state.

    Args:
        graph: Desired graph.
        document: Whether the import document is exact.
        issues: Whether all issues exist.
        relations: Whether relations exist.
        activated: Whether issue activation metadata exists.
        project_status: Exact Project status.

    Returns:
        Remote Project.
    """

    view = GraphPublicationView.from_graph(graph)
    return RemoteProject(
        id=PROJECT_ID,
        team_id=TEAM_ID,
        project_key=view.project_key,
        name=view.project_name,
        description=view.project_description,
        status_name=project_status,
        document_list=(
            [
                RemoteDocument(
                    id="55555555-5555-4555-8555-555555555555",
                    title=view.import_document_title,
                    content=view.import_document_content,
                )
            ]
            if document
            else []
        ),
        issue_list=(
            _remote_issue_list(graph, activated=activated, relations=relations)
            if issues
            else []
        ),
    )


def _delta_receipt_add(delta: TaskGraphDelta, remote: RemoteProject) -> RemoteProject:
    """Return a Project snapshot containing the exact approved delta receipt."""

    view = DeltaPublicationView.from_delta(delta)
    return replace(
        remote,
        document_list=[
            *remote.document_list,
            RemoteDocument(
                id="55555555-5555-4555-8555-555555555556",
                title=view.import_document_title,
                content=view.import_document_content,
            ),
        ],
    )


def test_graph_validates_roles_and_renders_one_shared_issue_contract() -> None:
    """A complete graph renders stable visible identities and task sections."""

    graph = TaskGraph.from_payload(_graph_payload())
    view = GraphPublicationView.from_graph(graph)

    assert len(view.issue_list) == 4
    assert view.project_key.endswith(graph.source_fingerprint())
    for issue in view.issue_list:
        assert f"- Node key: `{issue.node_key}`" in issue.description
        assert "## Human Decision Boundary" in issue.description
        assert "## Evidence And Links" in issue.description
    assert '"node_list"' in view.import_document_content


def test_graph_supports_exact_delegate_assignment_without_null() -> None:
    """A task may use one delegate instead of an assignee, but never both or neither."""

    payload = _graph_payload()
    payload["node_list"][0]["assignee_id"] = ""
    payload["node_list"][0]["delegate_id"] = ASSIGNEE_ID

    graph = TaskGraph.from_payload(payload)
    issue = next(
        item
        for item in GraphPublicationView.from_graph(graph).issue_list
        if item.node_key == "implementation"
    )

    assert issue.assignee_id == ""
    assert issue.delegate_id == ASSIGNEE_ID
    assert f"- Execution assignment: `delegate` `{ASSIGNEE_ID}`" in issue.description

    payload["node_list"][0]["assignee_id"] = ASSIGNEE_ID
    with pytest.raises(TaskGraphError, match="exactly one"):
        TaskGraph.from_payload(payload)

    payload["node_list"][0]["assignee_id"] = ""
    payload["node_list"][0]["delegate_id"] = ""
    with pytest.raises(TaskGraphError, match="exactly one"):
        TaskGraph.from_payload(payload)


def test_cross_repository_code_requires_visible_ordered_partial_merge_recovery() -> (
    None
):
    """An indivisible multi-repository task exposes both merge order and recovery."""

    payload = _graph_payload()
    implementation = payload["node_list"][0]
    implementation["repository_list"].append(
        {
            "origin_url": "git@github.com:antonov-andrey/second.git",
            "base_branch": "main",
            "merge_method": "squash",
        }
    )
    with pytest.raises(TaskGraphError, match="partial merge recovery"):
        TaskGraph.from_payload(payload)

    implementation["partial_merge_recovery"] = (
        "Preserve completed merges and open one bounded recovery task."
    )
    view = GraphPublicationView.from_graph(TaskGraph.from_payload(payload))
    issue = next(item for item in view.issue_list if item.node_key == "implementation")
    assert "## Ordered Merge Plan And Partial Recovery" in issue.description
    assert "Preserve completed merges" in issue.description


def test_graph_rejects_cycle_and_wrong_role_delivery_pair() -> None:
    """Incomplete semantics cannot hide in a staged active Project."""

    cycle = _graph_payload()
    cycle["node_list"][0]["blocker_key_list"] = ["cleanup"]
    with pytest.raises(TaskGraphError, match="cycle"):
        TaskGraph.from_payload(cycle)

    wrong = _graph_payload()
    wrong["node_list"][1]["delivery_kind"] = "code"
    with pytest.raises(TaskGraphError, match="unsupported enum|incompatible"):
        TaskGraph.from_payload(wrong)


def test_direct_model_construction_requires_lists_and_detaches_caller_collections() -> (
    None
):
    """Internal callers use the same list contract and cannot mutate stored lists indirectly."""

    graph = TaskGraph.from_payload(_graph_payload())
    implementation = graph.node_list[0]

    with pytest.raises(TaskGraphError, match="Task scope.*list"):
        replace(implementation, scope_list=("Wrong collection",))
    with pytest.raises(TaskGraphError, match="command_argument_list.*list"):
        replace(
            implementation.verification_list[0], command_argument_list=("pytest", "-q")
        )
    with pytest.raises(TaskGraphError, match="node list"):
        replace(graph, node_list=tuple(graph.node_list))

    delta = TaskGraphDelta.from_payload(_delta_payload(graph))
    with pytest.raises(TaskGraphError, match="existing node set"):
        replace(delta, existing_node_key_list=tuple(delta.existing_node_key_list))

    caller_scope_list = ["Detached scope"]
    detached = replace(implementation, scope_list=caller_scope_list)
    caller_scope_list.append("Late mutation")
    assert detached.scope_list == ["Detached scope"]


@pytest.mark.parametrize("field_name", ("resource_list", "verification_list"))
def test_task_node_rejects_untyped_collections_with_domain_error(
    field_name: str,
) -> None:
    """Malformed untrusted graph input never leaks an internal attribute failure."""

    graph = TaskGraph.from_payload(_graph_payload())
    implementation = graph.node_list[0]

    with pytest.raises(
        TaskGraphError,
        match=f"Task {field_name.removesuffix('_list').replace('_', ' ')} list",
    ):
        replace(implementation, **{field_name: (object(),)})


def test_graph_requires_verification_and_resource_repositories_to_be_explicit() -> None:
    """Commands and cleanup bindings cannot target a repository hidden from the task contract."""

    hidden_verification_repository = _graph_payload()
    hidden_verification_repository["node_list"][1]["repository_list"] = []
    with pytest.raises(TaskGraphError, match="Verification repository"):
        TaskGraph.from_payload(hidden_verification_repository)

    hidden_resource_repository = _graph_payload()
    hidden_resource_repository["node_list"][0]["resource_list"] = [
        {
            "key": "issue-environment",
            "lifetime": "issue",
            "owner_identity": "AND-17:environment",
            "repository_url": "git@github.com:antonov-andrey/another.git",
            "cleanup_argument_list": ["python", "manage.py", "destroy"],
            "consumer_node_key_list": [],
        }
    ]
    with pytest.raises(TaskGraphError, match="Resource repository"):
        TaskGraph.from_payload(hidden_resource_repository)


def test_issue_resource_consumers_are_exact_and_downstream() -> None:
    """An issue resource survives only for declared downstream graph consumers."""

    payload = _graph_payload()
    payload["node_list"][0]["resource_list"] = [
        {
            "key": "review-environment",
            "lifetime": "issue",
            "owner_identity": "implementation:review-environment",
            "repository_url": "git@github.com:antonov-andrey/example.git",
            "cleanup_argument_list": ["python", "manage.py", "destroy"],
            "consumer_node_key_list": ["review", "acceptance"],
        }
    ]

    graph = TaskGraph.from_payload(payload)
    resource = graph.node_list[0].resource_list[0]
    implementation = next(
        item
        for item in GraphPublicationView.from_graph(graph).issue_list
        if item.node_key == "implementation"
    )
    assert resource.consumer_node_key_list == ["review", "acceptance"]
    assert (
        'downstream consumers `["review", "acceptance"]`' in implementation.description
    )

    payload["node_list"][0]["resource_list"][0]["consumer_node_key_list"] = ["unknown"]
    with pytest.raises(TaskGraphError, match="unknown consumers"):
        TaskGraph.from_payload(payload)

    payload["node_list"][0]["resource_list"][0]["consumer_node_key_list"] = []
    payload["node_list"][1]["resource_list"] = [
        {
            "key": "upstream-environment",
            "lifetime": "issue",
            "owner_identity": "review:upstream-environment",
            "repository_url": "git@github.com:antonov-andrey/example.git",
            "cleanup_argument_list": ["python", "manage.py", "destroy"],
            "consumer_node_key_list": ["implementation"],
        }
    ]
    with pytest.raises(TaskGraphError, match="consumer must be downstream"):
        TaskGraph.from_payload(payload)


def _resource(key: str, *, lifetime: str = "project") -> dict[str, object]:
    """Return one exact task-owned resource payload."""

    return {
        "key": key,
        "lifetime": lifetime,
        "owner_identity": f"owner:{key}",
        "repository_url": "git@github.com:antonov-andrey/example.git",
        "cleanup_argument_list": ["python", "manage.py", "destroy", key],
        "consumer_node_key_list": [],
    }


def test_delta_resource_keys_remain_unique_across_new_and_accepted_issues() -> None:
    """An additive delta cannot make final Project cleanup resource identity ambiguous."""

    graph_payload = _graph_payload()
    graph_payload["node_list"][0]["resource_list"] = [_resource("shared-environment")]
    graph = TaskGraph.from_payload(graph_payload)

    duplicate_new = _delta_payload(graph)
    duplicate_new["node_list"][0]["resource_list"] = [_resource("delta-environment")]
    second = _node(
        "second-remediation", "task:implementation", "code", [], repository=True
    )
    second["resource_list"] = [_resource("delta-environment")]
    duplicate_new["node_list"].append(second)
    duplicate_new["blocker_edge_list"].append(
        {
            "blocker_node_key": "second-remediation",
            "blocked_node_key": "review",
        }
    )
    with pytest.raises(TaskGraphError, match="repeats one resource key"):
        TaskGraphDelta.from_payload(duplicate_new)

    accepted_collision = _delta_payload(graph)
    accepted_collision["node_list"][0]["resource_list"] = [
        _resource("shared-environment")
    ]
    delta = TaskGraphDelta.from_payload(accepted_collision)
    remote = _remote(
        graph,
        document=True,
        issues=True,
        relations=True,
        activated=True,
        project_status="In Progress",
    )
    with pytest.raises(TaskGraphError, match="repeats accepted resource keys"):
        TaskGraphDeltaReconciler(delta).plan(remote)


def test_graph_requires_every_task_to_reach_all_applicable_downstream_gates() -> None:
    """One reviewed slice cannot hide unrelated implementation or human work from acceptance."""

    unreviewed = _graph_payload()
    unreviewed["node_list"].insert(
        1,
        _node("unreviewed", "task:implementation", "code", [], repository=True),
    )
    with pytest.raises(TaskGraphError, match="unreviewed.*downstream path"):
        TaskGraph.from_payload(unreviewed)

    human_bypass = _graph_payload()
    human_bypass["node_list"].insert(
        1, _node("human-decision", "task:human", "human", [])
    )
    human_bypass["node_list"][-1]["blocker_key_list"].append("human-decision")
    with pytest.raises(TaskGraphError, match="human-decision.*downstream path"):
        TaskGraph.from_payload(human_bypass)


def test_instruction_adoption_is_an_explicit_blocker_not_a_legacy_fallback() -> None:
    """A stale consumer is migrated by a first-class owner task before Product work."""

    payload = _graph_payload()
    adoption = _node(
        "instruction-adoption", "task:implementation", "code", [], repository=True
    )
    product = _node(
        "product-implementation",
        "task:implementation",
        "code",
        ["instruction-adoption"],
        repository=True,
    )
    payload["node_list"] = [
        adoption,
        product,
        _node("review", "task:review", "evidence", ["product-implementation"]),
        _node("acceptance", "task:acceptance", "evidence", ["review"]),
        _node("cleanup", "task:cleanup", "cleanup", ["acceptance"]),
    ]

    graph = TaskGraph.from_payload(payload)

    product_node = next(
        item for item in graph.node_list if item.node_key == "product-implementation"
    )
    assert product_node.blocker_key_list == ["instruction-adoption"]
    assert graph.graph_fingerprint()


def test_activation_barrier_advances_one_idempotent_phase_at_a_time() -> None:
    """Interrupted publication remains Planned until exact node read-back."""

    graph = TaskGraph.from_payload(_graph_payload())

    project_plan = TaskGraphReconciler(graph).plan(None)
    assert project_plan.phase is PublicationPhase.PROJECT
    assert (
        project_plan.action_list[0]
        .payload["description"]
        .startswith("<!-- linear-agent-tools-project:v1 -->")
    )
    document_plan = TaskGraphReconciler(graph).plan(
        _remote(graph, document=False, issues=False)
    )
    assert document_plan.phase is PublicationPhase.DOCUMENT
    assert (
        TaskGraphReconciler(graph)
        .plan(_remote(graph, document=True, issues=False))
        .phase
        is PublicationPhase.ISSUES
    )
    issue_creation = TaskGraphReconciler(graph).plan(
        _remote(graph, document=True, issues=False)
    )
    assert all(
        action.payload["label_name_list"] == [] for action in issue_creation.action_list
    )
    assert all(
        action.payload["assignee_id"] == "" for action in issue_creation.action_list
    )
    assert all(
        action.payload["delegate_id"] == "" for action in issue_creation.action_list
    )
    assert (
        TaskGraphReconciler(graph)
        .plan(_remote(graph, document=True, issues=True))
        .phase
        is PublicationPhase.RELATIONS
    )
    assert (
        TaskGraphReconciler(graph)
        .plan(
            _remote(graph, document=True, issues=True, relations=True),
        )
        .phase
        is PublicationPhase.NODE_METADATA
    )
    node_by_key_map = {item.node_key: item for item in graph.node_list}
    metadata_ready = _remote(graph, document=True, issues=True, relations=True)
    metadata_ready = replace(
        metadata_ready,
        issue_list=[
            replace(
                issue,
                label_name_list=[
                    node_by_key_map[issue.node_key].role,
                    *([] if issue.node_key == "human" else ["agent:codex"]),
                ],
                assignee_id=node_by_key_map[issue.node_key].assignee_id,
                delegate_id=node_by_key_map[issue.node_key].delegate_id,
            )
            for issue in metadata_ready.issue_list
        ],
    )
    assert (
        TaskGraphReconciler(graph).plan(metadata_ready).phase
        is PublicationPhase.NODE_ACTIVATION
    )
    activation = TaskGraphReconciler(graph).plan(
        _remote(graph, document=True, issues=True, relations=True, activated=True),
    )
    assert activation.phase is PublicationPhase.PROJECT_ACTIVATION
    assert activation.activation_ready
    complete = TaskGraphReconciler(graph).plan(
        _remote(
            graph,
            document=True,
            issues=True,
            relations=True,
            activated=True,
            project_status="In Progress",
        ),
    )
    assert complete.phase is PublicationPhase.COMPLETE
    assert not complete.action_list


def test_project_creation_publishes_exact_provider_identity() -> None:
    """Project recovery starts from a visible immutable provider identity."""

    graph = TaskGraph.from_payload(_graph_payload())
    view = GraphPublicationView.from_graph(graph)

    action = TaskGraphReconciler(graph).plan(None).action_list[0]

    assert action.kind == "project-create"
    assert action.stable_key == view.project_key
    assert action.payload == {
        "name": view.project_name,
        "description": view.project_description,
        "project_key": view.project_key,
        "status_name": "Planned",
        "team_id": TEAM_ID,
    }


def test_import_document_recovery_rejects_duplicates_and_foreign_collision() -> None:
    """Recovery updates only one exact provider-owned document and never a title collision."""

    graph = TaskGraph.from_payload(_graph_payload())
    view = GraphPublicationView.from_graph(graph)
    remote = _remote(graph, document=True, issues=False)
    exact = remote.document_list[0]

    duplicate = replace(
        remote,
        document_list=[
            exact,
            replace(exact, id="55555555-5555-4555-8555-555555555556"),
        ],
    )
    with pytest.raises(TaskGraphError, match="duplicate import documents"):
        TaskGraphReconciler(graph).plan(duplicate)

    foreign = replace(exact, content="A user-owned document with the same title.")
    with pytest.raises(TaskGraphError, match="collides with a foreign document"):
        TaskGraphReconciler(graph).plan(replace(remote, document_list=[foreign]))

    stale_provider = replace(
        exact,
        content="\n".join(
            (
                "# Linear Agent Tools Import Plan",
                "",
                f"- Project key: `{view.project_key}`",
                "- Interrupted provider publication",
            )
        ),
    )
    plan = TaskGraphReconciler(graph).plan(
        replace(remote, document_list=[stale_provider])
    )

    assert plan.phase is PublicationPhase.DOCUMENT
    assert plan.action_list[0].kind == "import-document-update"
    assert plan.action_list[0].payload["document_id"] == stale_provider.id
    assert plan.action_list[0].payload["content"] == view.import_document_content


def test_activation_readback_proves_exact_handoff_without_freezing_later_linear_state() -> (
    None
):
    """The immediate handoff proof is strict while ordinary later reconciliation remains source-only."""

    graph = TaskGraph.from_payload(_graph_payload())
    activated = _remote(
        graph,
        document=True,
        issues=True,
        relations=True,
        activated=True,
        project_status="In Progress",
    )
    assert (
        TaskGraphReconciler(graph)
        .activation_readback_require(activated)
        .activation_ready
    )

    progressed = replace(
        activated,
        issue_list=[
            (
                replace(item, status_name="In Progress")
                if item.node_key == "implementation"
                else item
            )
            for item in activated.issue_list
        ],
    )
    with pytest.raises(TaskGraphError, match="exact handoff graph"):
        TaskGraphReconciler(graph).activation_readback_require(progressed)
    assert (
        TaskGraphReconciler(graph).plan(progressed).phase is PublicationPhase.COMPLETE
    )


def test_initial_import_rejects_unapproved_label_before_project_activation() -> None:
    """Unexpected labels must be rejected before the one-way Project handoff."""

    graph = TaskGraph.from_payload(_graph_payload())
    remote = _remote(
        graph,
        document=True,
        issues=True,
        relations=True,
        activated=True,
    )
    first = remote.issue_list[0]
    issue_list = [
        replace(first, label_name_list=[*first.label_name_list, "foreign"]),
        *remote.issue_list[1:],
    ]

    with pytest.raises(
        TaskGraphError, match="outside its approved activation metadata"
    ):
        TaskGraphReconciler(graph).plan(replace(remote, issue_list=issue_list))


def test_interrupted_issue_import_reuses_stable_keys_without_duplicates() -> None:
    """Retry creates only missing nodes and rejects unknown provider-key collisions."""

    graph = TaskGraph.from_payload(_graph_payload())
    partial = _remote(graph, document=True, issues=True)
    partial = RemoteProject(
        id=partial.id,
        team_id=partial.team_id,
        project_key=partial.project_key,
        name=partial.name,
        description=partial.description,
        status_name=partial.status_name,
        document_list=partial.document_list,
        issue_list=[
            item
            for item in partial.issue_list
            if item.node_key in {"implementation", "review"}
        ],
    )
    plan = TaskGraphReconciler(graph).plan(partial)

    assert plan.phase is PublicationPhase.ISSUES
    assert [item.stable_key for item in plan.action_list] == ["acceptance", "cleanup"]

    foreign = RemoteIssue(
        id="60000000-0000-4000-8000-000000000001",
        node_key="foreign",
        title="Foreign",
        description="Foreign",
        status_name="Backlog",
        label_name_list=[],
        assignee_id=ASSIGNEE_ID,
        delegate_id="",
        blocker_key_list=[],
    )
    conflicting = RemoteProject(
        id=partial.id,
        team_id=partial.team_id,
        project_key=partial.project_key,
        name=partial.name,
        description=partial.description,
        status_name=partial.status_name,
        document_list=partial.document_list,
        issue_list=[*partial.issue_list, foreign],
    )
    with pytest.raises(TaskGraphError, match="unknown issue keys"):
        TaskGraphReconciler(graph).plan(conflicting)


def test_remote_snapshot_parser_rejects_malformed_provider_identities() -> None:
    """Transient MCP snapshots are strict external input rather than trusted fixtures."""

    graph = TaskGraph.from_payload(_graph_payload())
    remote = _remote(graph, document=True, issues=True)
    payload = {
        "id": remote.id,
        "team_id": remote.team_id,
        "project_key": remote.project_key,
        "name": remote.name,
        "description": remote.description,
        "status_name": remote.status_name,
        "document_list": [
            {"id": item.id, "title": item.title, "content": item.content}
            for item in remote.document_list
        ],
        "issue_list": [
            {
                "id": item.id,
                "node_key": item.node_key,
                "title": item.title,
                "description": item.description,
                "status_name": item.status_name,
                "label_name_list": list(item.label_name_list),
                "assignee_id": item.assignee_id,
                "delegate_id": item.delegate_id,
                "blocker_key_list": list(item.blocker_key_list),
            }
            for item in remote.issue_list
        ],
    }
    payload["issue_list"][0]["id"] = "not-a-linear-id"

    with pytest.raises(TaskGraphError, match="lowercase UUID"):
        RemoteProject.from_payload(payload)


def test_active_project_is_not_reimported_and_terminal_project_is_not_reopened() -> (
    None
):
    """Post-handoff state belongs to Linear while immutable import receipt remains proven."""

    graph = TaskGraph.from_payload(_graph_payload())
    for status in ("In Progress", "Completed", "Canceled"):
        result = TaskGraphReconciler(graph).plan(
            _remote(graph, document=True, issues=False, project_status=status),
        )
        assert result.phase is PublicationPhase.COMPLETE
        assert not result.action_list

    corrupted = _remote(
        graph, document=False, issues=False, project_status="In Progress"
    )
    with pytest.raises(TaskGraphError, match="import receipt"):
        TaskGraphReconciler(graph).plan(corrupted)

    wrong_identity = replace(
        _remote(graph, document=True, issues=False, project_status="In Progress"),
        description="Foreign Project with a copied key",
    )
    with pytest.raises(TaskGraphError, match="Project key conflicts"):
        TaskGraphReconciler(graph).plan(wrong_identity)

    wrong_team = replace(
        _remote(graph, document=True, issues=False, project_status="In Progress"),
        team_id="99999999-9999-4999-8999-999999999999",
    )
    with pytest.raises(TaskGraphError, match="Project key conflicts"):
        TaskGraphReconciler(graph).plan(wrong_team)


def test_project_cancellation_stops_dispatch_before_canceling_unfinished_issues() -> (
    None
):
    """Cancellation is explicit, Project-first, idempotent and never reopens completion."""

    graph = TaskGraph.from_payload(_graph_payload())
    active = _remote(
        graph,
        document=True,
        issues=True,
        relations=True,
        activated=True,
        project_status="In Progress",
    )
    with pytest.raises(TaskGraphError, match="explicit human decision"):
        TaskGraphReconciler(graph).cancellation_plan(active, human_decision=False)

    project_plan = TaskGraphReconciler(graph).cancellation_plan(
        active, human_decision=True
    )
    assert project_plan.phase is PublicationPhase.PROJECT_CANCELLATION
    assert project_plan.action_list[0].payload["status_name"] == "Canceled"

    canceled = replace(active, status_name="Canceled")
    issue_plan = TaskGraphReconciler(graph).cancellation_plan(
        canceled, human_decision=True
    )
    assert issue_plan.phase is PublicationPhase.NODE_CANCELLATION
    assert {item.payload["status_name"] for item in issue_plan.action_list} == {
        "Canceled"
    }

    terminal = replace(
        canceled,
        issue_list=[
            replace(item, status_name="Canceled") for item in canceled.issue_list
        ],
    )
    assert (
        TaskGraphReconciler(graph)
        .cancellation_plan(terminal, human_decision=True)
        .phase
        is PublicationPhase.COMPLETE
    )

    with pytest.raises(TaskGraphError, match="completed.*cannot be canceled"):
        TaskGraphReconciler(graph).cancellation_plan(
            replace(active, status_name="Completed"), human_decision=True
        )


def test_partial_or_damaged_import_can_be_canceled_without_an_import_document() -> None:
    """Missing auxiliary receipt state never traps an exactly identified Project."""

    graph = TaskGraph.from_payload(_graph_payload())
    partial = _remote(graph, document=False, issues=False, project_status="Planned")

    plan = TaskGraphReconciler(graph).cancellation_plan(partial, human_decision=True)

    assert plan.phase is PublicationPhase.PROJECT_CANCELLATION

    active_without_document = replace(partial, status_name="In Progress")
    assert (
        TaskGraphReconciler(graph)
        .cancellation_plan(active_without_document, human_decision=True)
        .phase
        is PublicationPhase.PROJECT_CANCELLATION
    )


def test_active_project_delta_advances_issue_relation_metadata_and_todo_separately() -> (
    None
):
    """A delta adds its dispatch label only after relations and makes Todo the last mutation."""

    graph = TaskGraph.from_payload(_graph_payload())
    delta = TaskGraphDelta.from_payload(_delta_payload(graph))
    view = DeltaPublicationView.from_delta(delta)
    remote = _remote(
        graph,
        document=True,
        issues=True,
        relations=True,
        activated=True,
        project_status="In Progress",
    )

    receipt_plan = TaskGraphDeltaReconciler(delta).plan(remote)
    assert receipt_plan.phase is PublicationPhase.DELTA_DOCUMENT
    assert receipt_plan.action_list[0].kind == "delta-document-create"
    assert (
        receipt_plan.action_list[0].payload["content"] == view.import_document_content
    )
    remote = _delta_receipt_add(delta, remote)
    assert (
        TaskGraphDeltaReconciler(delta).plan(remote).phase
        is PublicationPhase.DELTA_ISSUES
    )

    remediation = RemoteIssue(
        id="70000000-0000-4000-8000-000000000001",
        node_key="remediation",
        title=view.issue_list[0].title,
        description=view.issue_list[0].description,
        status_name="Backlog",
        label_name_list=["task:implementation"],
        assignee_id=ASSIGNEE_ID,
        delegate_id="",
        blocker_key_list=[],
    )
    with_issue = replace(remote, issue_list=[*remote.issue_list, remediation])
    assert (
        TaskGraphDeltaReconciler(delta).plan(with_issue).phase
        is PublicationPhase.DELTA_RELATIONS
    )

    with_relation = replace(
        with_issue,
        issue_list=[
            (
                replace(
                    item,
                    blocker_key_list=[*item.blocker_key_list, "remediation"],
                    status_name="In Progress",
                )
                if item.node_key == "review"
                else item
            )
            for item in with_issue.issue_list
        ],
    )
    reverification = TaskGraphDeltaReconciler(delta).plan(with_relation)
    assert reverification.phase is PublicationPhase.DELTA_REVERIFICATION
    assert reverification.action_list[0].stable_key == "review"
    assert reverification.action_list[0].payload["status_name"] == "Todo"

    with_reverification = replace(
        with_relation,
        issue_list=[
            replace(item, status_name="Todo") if item.node_key == "review" else item
            for item in with_relation.issue_list
        ],
    )
    assert (
        TaskGraphDeltaReconciler(delta).plan(with_reverification).phase
        is PublicationPhase.DELTA_METADATA
    )

    with_metadata = replace(
        with_reverification,
        issue_list=[
            (
                replace(item, label_name_list=[*item.label_name_list, "agent:codex"])
                if item.node_key == "remediation"
                else item
            )
            for item in with_reverification.issue_list
        ],
    )
    activation = TaskGraphDeltaReconciler(delta).plan(with_metadata)
    assert activation.phase is PublicationPhase.DELTA_ACTIVATION
    assert activation.activation_ready
    assert activation.action_list[0].payload["status_name"] == "Todo"

    activated = replace(
        with_metadata,
        issue_list=[
            (
                replace(item, status_name="Todo")
                if item.node_key == "remediation"
                else item
            )
            for item in with_metadata.issue_list
        ],
    )
    complete = TaskGraphDeltaReconciler(delta).plan(activated)
    assert complete.phase is PublicationPhase.COMPLETE
    assert complete.activation_ready


@pytest.mark.parametrize(
    ("target_status", "declare_reverification"),
    [
        ("In Progress", False),
        ("Human Review", True),
        ("Rework", True),
        ("Merging", True),
        ("Done", True),
        ("Canceled", True),
    ],
)
def test_active_project_delta_never_bypasses_or_reopens_downstream_verification(
    target_status: str,
    declare_reverification: bool,
) -> None:
    """A new blocker safely stops only one declared running verification node."""

    graph = TaskGraph.from_payload(_graph_payload())
    payload = _delta_payload(graph)
    if not declare_reverification:
        payload["reverification_node_key_list"] = []
    delta = TaskGraphDelta.from_payload(payload)
    view = DeltaPublicationView.from_delta(delta)
    remote = _delta_receipt_add(
        delta,
        _remote(
            graph,
            document=True,
            issues=True,
            relations=True,
            activated=True,
            project_status="In Progress",
        ),
    )
    remediation = RemoteIssue(
        id="70000000-0000-4000-8000-000000000001",
        node_key="remediation",
        title=view.issue_list[0].title,
        description=view.issue_list[0].description,
        status_name="Backlog",
        label_name_list=[],
        assignee_id="",
        delegate_id="",
        blocker_key_list=[],
    )
    relation_ready = replace(
        remote,
        issue_list=[
            *[
                (
                    replace(
                        item,
                        blocker_key_list=[*item.blocker_key_list, "remediation"],
                        status_name=target_status,
                    )
                    if item.node_key == "review"
                    else item
                )
                for item in remote.issue_list
            ],
            remediation,
        ],
    )

    with pytest.raises(
        TaskGraphError, match="must already be Todo or explicitly return"
    ):
        TaskGraphDeltaReconciler(delta).plan(relation_ready)


def test_active_project_delta_may_reverify_only_review_or_acceptance() -> None:
    """Reverification cannot reset implementation, cleanup or human lifecycle state."""

    graph = TaskGraph.from_payload(_graph_payload())
    payload = _delta_payload(graph)
    payload["existing_node_key_list"] = [
        "implementation",
        "review",
        "acceptance",
        "cleanup",
    ]
    payload["reverification_node_key_list"] = ["implementation"]
    payload["blocker_edge_list"] = [
        {
            "blocker_node_key": "remediation",
            "blocked_node_key": "implementation",
        }
    ]
    delta = TaskGraphDelta.from_payload(payload)
    view = DeltaPublicationView.from_delta(delta)
    remote = _delta_receipt_add(
        delta,
        _remote(
            graph,
            document=True,
            issues=True,
            relations=True,
            activated=True,
            project_status="In Progress",
        ),
    )
    remediation = RemoteIssue(
        id="70000000-0000-4000-8000-000000000001",
        node_key="remediation",
        title=view.issue_list[0].title,
        description=view.issue_list[0].description,
        status_name="Backlog",
        label_name_list=[],
        assignee_id="",
        delegate_id="",
        blocker_key_list=[],
    )
    relation_ready = replace(
        remote,
        issue_list=[
            *[
                (
                    replace(
                        item,
                        blocker_key_list=[*item.blocker_key_list, "remediation"],
                        status_name="In Progress",
                    )
                    if item.node_key == "implementation"
                    else item
                )
                for item in remote.issue_list
            ],
            remediation,
        ],
    )

    with pytest.raises(TaskGraphError, match="only when it is review or acceptance"):
        TaskGraphDeltaReconciler(delta).plan(relation_ready)


def test_active_project_delta_rejects_unapproved_label_before_node_activation() -> None:
    """A staged delta node may carry only its exact approved activation labels."""

    graph = TaskGraph.from_payload(_graph_payload())
    delta = TaskGraphDelta.from_payload(_delta_payload(graph))
    view = DeltaPublicationView.from_delta(delta)
    remote = _remote(
        graph,
        document=True,
        issues=True,
        relations=True,
        activated=True,
        project_status="In Progress",
    )
    remote = _delta_receipt_add(delta, remote)
    remediation = RemoteIssue(
        id="70000000-0000-4000-8000-000000000001",
        node_key="remediation",
        title=view.issue_list[0].title,
        description=view.issue_list[0].description,
        status_name="Backlog",
        label_name_list=["task:implementation", "agent:codex", "foreign"],
        assignee_id=ASSIGNEE_ID,
        delegate_id="",
        blocker_key_list=[],
    )
    with_issue = replace(
        remote,
        issue_list=[
            *[
                (
                    replace(
                        item, blocker_key_list=[*item.blocker_key_list, "remediation"]
                    )
                    if item.node_key == "review"
                    else item
                )
                for item in remote.issue_list
            ],
            remediation,
        ],
    )

    with pytest.raises(
        TaskGraphError, match="outside its approved activation metadata"
    ):
        TaskGraphDeltaReconciler(delta).plan(with_issue)


def test_active_project_delta_requires_downstream_gates_and_exact_active_destination() -> (
    None
):
    """An additive node cannot bypass review/acceptance/cleanup or reopen a terminal Project."""

    graph = TaskGraph.from_payload(_graph_payload())
    payload = _delta_payload(graph)
    payload["blocker_edge_list"] = []
    payload["reverification_node_key_list"] = []
    delta = TaskGraphDelta.from_payload(payload)
    remote = _remote(
        graph,
        document=True,
        issues=True,
        relations=True,
        activated=True,
        project_status="In Progress",
    )
    view = DeltaPublicationView.from_delta(delta)
    remediation = RemoteIssue(
        id="70000000-0000-4000-8000-000000000001",
        node_key="remediation",
        title=view.issue_list[0].title,
        description=view.issue_list[0].description,
        status_name="Backlog",
        label_name_list=["task:implementation"],
        assignee_id=ASSIGNEE_ID,
        delegate_id="",
        blocker_key_list=[],
    )
    with_issue = replace(
        _delta_receipt_add(delta, remote), issue_list=[*remote.issue_list, remediation]
    )

    with pytest.raises(TaskGraphError, match="downstream path"):
        TaskGraphDeltaReconciler(delta).plan(with_issue)

    valid_delta = TaskGraphDelta.from_payload(_delta_payload(graph))
    terminal = replace(remote, status_name="Completed")
    with pytest.raises(TaskGraphError, match="active In Progress"):
        TaskGraphDeltaReconciler(valid_delta).plan(terminal)

    without_receipt = replace(remote, document_list=[])
    with pytest.raises(TaskGraphError, match="unique immutable import receipt"):
        TaskGraphDeltaReconciler(valid_delta).plan(without_receipt)

    corrupt_receipt = replace(
        remote,
        document_list=[
            replace(
                remote.document_list[0],
                content="# Linear Agent Tools Import Plan\nforeign",
            ),
        ],
    )
    with pytest.raises(TaskGraphError, match="lost its immutable source identity"):
        TaskGraphDeltaReconciler(valid_delta).plan(corrupt_receipt)

    with_delta_receipt = _delta_receipt_add(valid_delta, remote)
    delta_view = DeltaPublicationView.from_delta(valid_delta)
    duplicate_delta_receipt = replace(
        with_delta_receipt,
        document_list=[
            *with_delta_receipt.document_list,
            RemoteDocument(
                id="55555555-5555-4555-8555-555555555557",
                title=delta_view.import_document_title,
                content=delta_view.import_document_content,
            ),
        ],
    )
    with pytest.raises(TaskGraphError, match="duplicate receipts"):
        TaskGraphDeltaReconciler(valid_delta).plan(duplicate_delta_receipt)

    changed_delta_receipt = replace(
        with_delta_receipt,
        document_list=[
            (
                replace(item, content="foreign")
                if item.title == delta_view.import_document_title
                else item
            )
            for item in with_delta_receipt.document_list
        ],
    )
    with pytest.raises(TaskGraphError, match="differs from its exact approved content"):
        TaskGraphDeltaReconciler(valid_delta).plan(changed_delta_receipt)


def test_active_project_delta_preserves_unrelated_nodes_and_rejects_unsafe_progressed_metadata() -> (
    None
):
    """Unknown active-Project content is retained while provider-owned delta fields remain exact."""

    graph = TaskGraph.from_payload(_graph_payload())
    delta = TaskGraphDelta.from_payload(_delta_payload(graph))
    view = DeltaPublicationView.from_delta(delta)
    remote = _remote(
        graph,
        document=True,
        issues=True,
        relations=True,
        activated=True,
        project_status="In Progress",
    )
    remote = _delta_receipt_add(delta, remote)
    unrelated = RemoteIssue(
        id="70000000-0000-4000-8000-000000000002",
        node_key="unrelated",
        title="Unrelated",
        description="User-owned unrelated task",
        status_name="Todo",
        label_name_list=["user-label"],
        assignee_id=ASSIGNEE_ID,
        delegate_id="",
        blocker_key_list=[],
    )
    remediation = RemoteIssue(
        id="70000000-0000-4000-8000-000000000001",
        node_key="remediation",
        title=view.issue_list[0].title,
        description=view.issue_list[0].description,
        status_name="Backlog",
        label_name_list=["task:implementation"],
        assignee_id=ASSIGNEE_ID,
        delegate_id="",
        blocker_key_list=[],
    )
    with_nodes = replace(
        remote, issue_list=[*remote.issue_list, unrelated, remediation]
    )
    assert (
        TaskGraphDeltaReconciler(delta).plan(with_nodes).phase
        is PublicationPhase.DELTA_RELATIONS
    )

    relation_ready = replace(
        with_nodes,
        issue_list=[
            (
                replace(item, blocker_key_list=[*item.blocker_key_list, "remediation"])
                if item.node_key == "review"
                else (
                    replace(item, status_name="In Progress")
                    if item.node_key == "remediation"
                    else item
                )
            )
            for item in with_nodes.issue_list
        ],
    )
    with pytest.raises(TaskGraphError, match="incomplete activation metadata"):
        TaskGraphDeltaReconciler(delta).plan(relation_ready)


def test_active_project_delta_rejects_missing_relation_after_new_node_left_backlog() -> (
    None
):
    """A crashed publisher cannot repair topology after prematurely activating a new node."""

    graph = TaskGraph.from_payload(_graph_payload())
    delta = TaskGraphDelta.from_payload(_delta_payload(graph))
    view = DeltaPublicationView.from_delta(delta)
    remote = _remote(
        graph,
        document=True,
        issues=True,
        relations=True,
        activated=True,
        project_status="In Progress",
    )
    remote = _delta_receipt_add(delta, remote)
    remediation = RemoteIssue(
        id="70000000-0000-4000-8000-000000000001",
        node_key="remediation",
        title=view.issue_list[0].title,
        description=view.issue_list[0].description,
        status_name="Todo",
        label_name_list=["task:implementation", "agent:codex"],
        assignee_id=ASSIGNEE_ID,
        delegate_id="",
        blocker_key_list=[],
    )

    with pytest.raises(
        TaskGraphError, match="relation is absent after a new node left Backlog"
    ):
        TaskGraphDeltaReconciler(delta).plan(
            replace(remote, issue_list=[*remote.issue_list, remediation])
        )


@pytest.mark.parametrize("direction", ["incoming", "outgoing"])
def test_active_project_delta_rejects_unapproved_relations_involving_new_node(
    direction: str,
) -> None:
    """A stable-key collision cannot smuggle an undeclared relation into an approved delta."""

    graph = TaskGraph.from_payload(_graph_payload())
    delta = TaskGraphDelta.from_payload(_delta_payload(graph))
    view = DeltaPublicationView.from_delta(delta)
    remote = _remote(
        graph,
        document=True,
        issues=True,
        relations=True,
        activated=True,
        project_status="In Progress",
    )
    remote = _delta_receipt_add(delta, remote)
    unrelated = RemoteIssue(
        id="70000000-0000-4000-8000-000000000002",
        node_key="unrelated",
        title="Unrelated",
        description="User-owned unrelated task",
        status_name="Todo",
        label_name_list=["user-label"],
        assignee_id=ASSIGNEE_ID,
        delegate_id="",
        blocker_key_list=["remediation"] if direction == "outgoing" else [],
    )
    remediation = RemoteIssue(
        id="70000000-0000-4000-8000-000000000001",
        node_key="remediation",
        title=view.issue_list[0].title,
        description=view.issue_list[0].description,
        status_name="Backlog",
        label_name_list=["task:implementation"],
        assignee_id=ASSIGNEE_ID,
        delegate_id="",
        blocker_key_list=["unrelated"] if direction == "incoming" else [],
    )

    with pytest.raises(TaskGraphError, match="unapproved current"):
        TaskGraphDeltaReconciler(delta).plan(
            replace(remote, issue_list=[*remote.issue_list, unrelated, remediation]),
        )


def test_active_project_delta_rejects_nonsemantic_existing_node_key() -> None:
    """Direct model construction cannot bypass canonical stable-key syntax."""

    graph = TaskGraph.from_payload(_graph_payload())
    payload = _delta_payload(graph)
    payload["existing_node_key_list"][0] = "Review Bad"

    with pytest.raises(TaskGraphError, match="lowercase semantic slugs"):
        TaskGraphDelta.from_payload(payload)
