"""Behavior tests for Linear configuration, dispatch and transition boundaries."""

from __future__ import annotations

from email.message import Message
from dataclasses import replace
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import urllib.error
import uuid

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = REPOSITORY_ROOT / "plugins" / "linear-agent-tools"
LIBRARY_ROOT = PLUGIN_ROOT / "lib"
if str(LIBRARY_ROOT) not in sys.path:
    sys.path.insert(0, str(LIBRARY_ROOT))

from json_contract import JsonContractError, json_load_strict
from linear_boundary.configuration.graphql import LinearWorkflowConfigurationGraphQL
from linear_boundary.configuration.catalog import (
    ISSUE_STATUS_DESIRED,
    ISSUE_STATUS_LEGACY_MERGING,
    ISSUE_STATUS_LEGACY_REVIEW,
    LABEL_DESIRED,
    PROJECT_STATUS_DESIRED,
)
from linear_boundary.configuration.model import (
    ConfigurationPlan,
    DestinationIdentity,
    GitStatusAutomation,
    LinearLabel,
    StatusDefinition,
    WorkflowConfigurationSnapshot,
)
from linear_boundary.configuration.reconciliation import WorkflowConfigurationReconciler
from linear_boundary.contract import LinearContractError
from linear_boundary.status import IssueStatusName, ProjectStatusName
from linear_boundary.task.model import TaskExecutionSnapshot, TransitionProof
from linear_boundary.task.workflow import TaskTransition
from linear_boundary.transport import (
    LinearAuthenticationError,
    LinearGraphQLTransport,
    LinearResponseError,
    LinearRetryPolicy,
    LinearTransportError,
)

WORKSPACE_ID = "11111111-1111-4111-8111-111111111111"
VIEWER_ID = "22222222-2222-4222-8222-222222222222"
TEAM_ID = "33333333-3333-4333-8333-333333333333"


@pytest.mark.parametrize(
    "payload",
    (
        '{"key":1,"key":2}',
        '{"value":NaN}',
        b"\xff",
    ),
)
def test_strict_json_boundary_rejects_ambiguous_or_nonstandard_payload(
    payload: str | bytes,
) -> None:
    """External JSON never accepts duplicate names, constants, or invalid UTF-8."""

    with pytest.raises(JsonContractError):
        json_load_strict(payload)


def _destination() -> DestinationIdentity:
    """Return one authorized deterministic destination.

    Returns:
        Authorized identity.
    """

    return DestinationIdentity(WORKSPACE_ID, VIEWER_ID, TEAM_ID, True, False, True)


def _transition_require(**payload: object) -> None:
    """Apply one transition while keeping table-driven tests concise."""

    TaskTransition(**payload).require()  # type: ignore[arg-type]


def _existing_status(item: StatusDefinition, index: int) -> StatusDefinition:
    """Attach one deterministic external ID to a desired status.

    Args:
        item: Desired definition.
        index: Deterministic UUID suffix.

    Returns:
        Existing status definition.
    """

    return StatusDefinition(
        id=f"00000000-0000-4000-8000-{index:012d}",
        name=item.name,
        category=item.category,
        color=item.color,
        description=item.description,
        position=item.position,
    )


def _existing_label(item: LinearLabel, index: int) -> LinearLabel:
    """Attach one deterministic external ID to a desired label.

    Args:
        item: Desired definition.
        index: Deterministic UUID suffix.

    Returns:
        Existing label definition.
    """

    return LinearLabel(
        id=f"10000000-0000-4000-8000-{index:012d}",
        name=item.name,
        color=item.color,
        description=item.description,
    )


def test_mcp_label_snapshot_accepts_nullable_foreign_description(
    tmp_path: Path,
) -> None:
    """Linear's nullable description does not reject an unrelated existing label."""

    script = PLUGIN_ROOT / "skills" / "workflow-configure" / "scripts" / "configure.py"
    spec = importlib.util.spec_from_file_location("linear_workflow_configure", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    snapshot = tmp_path / "labels.json"
    snapshot.write_text(
        json.dumps(
            [
                {
                    "id": "00000000-0000-4000-8000-000000000001",
                    "name": "Bug",
                    "color": "#EB5757",
                    "description": None,
                }
            ]
        ),
        encoding="utf-8",
    )

    assert module._labels_load(snapshot) == [
        LinearLabel(
            id="00000000-0000-4000-8000-000000000001",
            name="Bug",
            color="#EB5757",
            description="",
        )
    ]


def test_status_apply_precedes_still_missing_official_mcp_labels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A credential-gated status apply may leave the exact approved label delta for MCP."""

    script = PLUGIN_ROOT / "skills" / "workflow-configure" / "scripts" / "configure.py"
    spec = importlib.util.spec_from_file_location("linear_workflow_configure_apply", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    approved = (
        WorkflowConfigurationReconciler()
        .plan_get(WorkflowConfigurationSnapshot(_destination(), [], [], [], []))
        .status_identifier_allocate()
    )
    labels_path = tmp_path / "labels.json"
    labels_path.write_text("[]\n", encoding="utf-8")
    approved_path = tmp_path / "approved.json"
    approved_path.write_text(
        json.dumps({**approved.payload(), "plan_sha256": approved.fingerprint()}),
        encoding="utf-8",
    )
    call_list: list[ConfigurationPlan] = []

    class _Service:
        def __init__(self, _transport: object, _reconciler: object) -> None:
            pass

        def plan(self, **_argument_by_name: object) -> ConfigurationPlan:
            return approved

        def approved_configuration_apply(self, **argument_by_name: object) -> None:
            call_list.append(argument_by_name["approved_plan"])

    monkeypatch.setattr(module, "_credential_get", lambda: "secret-not-logged")
    monkeypatch.setattr(module, "LinearGraphQLTransport", lambda _credential: object())
    monkeypatch.setattr(module, "LinearWorkflowConfigurationGraphQL", _Service)

    result = module.main(
        [
            "apply",
            "--workspace-id",
            WORKSPACE_ID,
            "--viewer-id",
            VIEWER_ID,
            "--team-id",
            TEAM_ID,
            "--labels-input",
            str(labels_path),
            "--approved-plan-input",
            str(approved_path),
        ]
    )

    assert result == 0
    assert call_list == [approved]
    assert "secret-not-logged" not in capsys.readouterr().out


def _status_node(item: StatusDefinition, index: int) -> dict[str, object]:
    """Render one existing status as a GraphQL response node.

    Args:
        item: Desired status definition.
        index: Deterministic UUID suffix.

    Returns:
        GraphQL status object.
    """

    return {
        "id": f"20000000-0000-4000-8000-{index:012d}",
        "name": item.name,
        "type": item.category,
        "color": item.color,
        "description": item.description,
        "position": item.position,
    }


def _exact_status_node(item: StatusDefinition) -> dict[str, object]:
    """Render one status with its already approved external identity."""

    return {
        "id": item.id,
        "name": item.name,
        "type": item.category,
        "color": item.color,
        "description": item.description,
        "position": item.position,
    }


def _workflow_status_update_response(item: StatusDefinition) -> dict[str, object]:
    """Return one full successful in-place status mutation response."""

    return {
        "workflowStateUpdate": {
            "success": True,
            "workflowState": _exact_status_node(item),
        }
    }


def _legacy_issue_status_list_get() -> list[StatusDefinition]:
    """Return the exact pre-migration Review and Merging definitions."""

    return [
        (
            ISSUE_STATUS_LEGACY_REVIEW
            if item.name == "Review"
            else ISSUE_STATUS_LEGACY_MERGING if item.name == "Merging" else item
        )
        for item in ISSUE_STATUS_DESIRED
    ]


def _legacy_status_approved_plan_get() -> ConfigurationPlan:
    """Build the deterministic approved in-place lifecycle migration plan."""

    transport = _ScriptedTransport(
        [
            _workflow_response(_legacy_issue_status_list_get()),
            _git_status_automation_response([]),
            _project_status_response(PROJECT_STATUS_DESIRED),
        ]
    )
    return LinearWorkflowConfigurationGraphQL(transport, WorkflowConfigurationReconciler()).plan(
        expected_workspace_id=WORKSPACE_ID,
        expected_viewer_id=VIEWER_ID,
        expected_team_id=TEAM_ID,
        label_list=list(_existing_label(item, index) for index, item in enumerate(LABEL_DESIRED, 1)),
    )


def _workflow_response(
    status_list: list[StatusDefinition],
    *,
    has_next: bool = False,
    end_cursor: str | None = None,
) -> dict[str, object]:
    """Return one identity-bound workflow-status GraphQL page.

    Args:
        status_list: Page definitions.
        has_next: Pagination flag.
        end_cursor: Pagination cursor.

    Returns:
        GraphQL data object.
    """

    return {
        "viewer": {"id": VIEWER_ID, "admin": True, "guest": False, "active": True},
        "organization": {"id": WORKSPACE_ID},
        "team": {
            "id": TEAM_ID,
            "membership": {
                "id": "40000000-0000-4000-8000-000000000001",
                "archivedAt": None,
            },
            "states": {
                "nodes": [_status_node(item, index) for index, item in enumerate(status_list, 1)],
                "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor},
            },
        },
    }


def _project_status_response(
    status_list: list[StatusDefinition],
) -> dict[str, object]:
    """Return one complete workspace Project-status GraphQL page.

    Args:
        status_list: Complete Project statuses.

    Returns:
        GraphQL data object.
    """

    return {
        "organization": {"id": WORKSPACE_ID},
        "projectStatuses": {
            "nodes": [_status_node(item, index + 20) for index, item in enumerate(status_list, 1)],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        },
    }


def _git_status_automation(
    index: int,
    *,
    event: str = "merge",
    target_branch: bool = False,
    workflow_state: bool = True,
) -> GitStatusAutomation:
    """Return one deterministic existing Git status automation rule."""

    return GitStatusAutomation(
        id=f"30000000-0000-4000-8000-{index:012d}",
        event=event,
        workflow_state_id=(f"31000000-0000-4000-8000-{index:012d}" if workflow_state else ""),
        target_branch_id=(f"32000000-0000-4000-8000-{index:012d}" if target_branch else ""),
        target_branch_pattern=("main" if target_branch else ""),
        target_branch_is_regex=False,
        legacy_branch_pattern="",
    )


def _git_status_automation_response(
    automation_list: list[GitStatusAutomation],
    *,
    has_next: bool = False,
    end_cursor: str | None = None,
) -> dict[str, object]:
    """Return one complete team Git status automation GraphQL page."""

    return {
        "team": {
            "id": TEAM_ID,
            "gitAutomationStates": {
                "nodes": [
                    {
                        "id": item.id,
                        "event": item.event,
                        "branchPattern": item.legacy_branch_pattern or None,
                        "state": ({"id": item.workflow_state_id} if item.workflow_state_id else None),
                        "targetBranch": (
                            {
                                "id": item.target_branch_id,
                                "branchPattern": item.target_branch_pattern,
                                "isRegex": item.target_branch_is_regex,
                            }
                            if item.target_branch_id
                            else None
                        ),
                    }
                    for item in automation_list
                ],
                "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor},
            },
        }
    }


class _ScriptedTransport:
    """Return exact GraphQL data objects while recording typed operations."""

    def __init__(self, response_list: list[dict[str, object]]) -> None:
        self.response_list = response_list
        self.call_list: list[dict[str, object]] = []

    def execute(self, **argument_by_name: object) -> dict[str, object]:
        """Record one call and return its scripted data.

        Args:
            **argument_by_name: Typed transport arguments.

        Returns:
            Next scripted GraphQL data object.
        """

        self.call_list.append(argument_by_name)
        if not self.response_list:
            raise AssertionError("Unexpected GraphQL operation")
        return self.response_list.pop(0)


def test_configuration_plan_is_exact_and_idempotent() -> None:
    """Only missing definitions are planned and an exact read-back is empty."""

    partial = WorkflowConfigurationSnapshot(
        destination=_destination(),
        issue_status_list=list(_existing_status(item, index) for index, item in enumerate(ISSUE_STATUS_DESIRED[:3], 1)),
        project_status_list=[],
        label_list=[],
        git_status_automation_list=[_git_status_automation(1)],
    )

    plan = WorkflowConfigurationReconciler().plan_get(partial)

    assert [item.name for item in plan.issue_status_create_list] == [item.name for item in ISSUE_STATUS_DESIRED[3:]]
    assert plan.issue_status_update_list == []
    assert plan.project_status_create_list == list(PROJECT_STATUS_DESIRED)
    assert plan.label_create_list == list(LABEL_DESIRED)
    assert plan.git_status_automation_delete_list == [_git_status_automation(1)]
    assert plan.can_mutate()

    current = WorkflowConfigurationSnapshot(
        destination=_destination(),
        issue_status_list=list(_existing_status(item, index) for index, item in enumerate(ISSUE_STATUS_DESIRED, 1)),
        project_status_list=list(
            _existing_status(item, index + 20) for index, item in enumerate(PROJECT_STATUS_DESIRED, 1)
        ),
        label_list=list(_existing_label(item, index) for index, item in enumerate(LABEL_DESIRED, 1)),
        git_status_automation_list=[],
    )

    assert WorkflowConfigurationReconciler().plan_get(current).is_current()


def test_configuration_plan_roundtrip_and_fresh_subset_guard() -> None:
    """One approved fingerprint authorizes only an exact remaining subset."""

    approved = (
        WorkflowConfigurationReconciler()
        .plan_get(
            WorkflowConfigurationSnapshot(
                _destination(),
                [],
                [],
                [],
                [_git_status_automation(1), _git_status_automation(2, target_branch=True)],
            )
        )
        .status_identifier_allocate()
    )
    parsed = ConfigurationPlan.from_payload(approved.payload())

    assert parsed == approved
    assert parsed.fingerprint() == approved.fingerprint()
    approved.status_identifier_require()
    assert all(
        uuid.UUID(item.id).version == 4
        for item in (
            *approved.issue_status_create_list,
            *approved.project_status_create_list,
        )
    )
    assert approved.status_identifier_allocate() == approved
    current = ConfigurationPlan(
        destination=approved.destination,
        issue_status_create_list=[replace(item, id="") for item in approved.issue_status_create_list[1:]],
        issue_status_update_list=[],
        project_status_create_list=[replace(item, id="") for item in approved.project_status_create_list],
        label_create_list=[],
        git_status_automation_delete_list=approved.git_status_automation_delete_list[:1],
        conflict_list=[],
    )
    current.subset_require(approved)

    changed = replace(current.issue_status_create_list[0], color="#000000")
    with pytest.raises(LinearContractError, match="changed after approval"):
        replace(
            current,
            issue_status_create_list=[
                changed,
                *current.issue_status_create_list[1:],
            ],
        ).subset_require(approved)
    with pytest.raises(LinearContractError, match="destination changed"):
        replace(
            current,
            destination=replace(
                current.destination,
                workspace_id="44444444-4444-4444-8444-444444444444",
            ),
        ).subset_require(approved)
    with pytest.raises(LinearContractError, match="Git status automation plan changed"):
        replace(
            current,
            git_status_automation_delete_list=[replace(current.git_status_automation_delete_list[0], event="review")],
        ).subset_require(approved)


def test_configuration_migrates_exact_legacy_review_status_without_replacing_identity() -> None:
    """Only the recognized legacy definition becomes an in-place Review update."""

    legacy_review = _existing_status(ISSUE_STATUS_LEGACY_REVIEW, 4)
    current_issue_status_list = [
        _existing_status(item, index) for index, item in enumerate(ISSUE_STATUS_DESIRED, 1) if item.name != "Review"
    ]
    current_issue_status_list.append(legacy_review)

    plan = WorkflowConfigurationReconciler().plan_get(
        WorkflowConfigurationSnapshot(_destination(), current_issue_status_list, [], [], [])
    )

    assert plan.issue_status_create_list == []
    assert plan.issue_status_update_list == [replace(ISSUE_STATUS_DESIRED[3], id=legacy_review.id)]
    assert plan.issue_status_update_list[0].id == legacy_review.id
    assert plan.can_mutate()
    plan.subset_require(plan)
    with pytest.raises(LinearContractError, match="status update plan changed"):
        replace(
            plan,
            issue_status_update_list=[replace(plan.issue_status_update_list[0], color="#000000")],
        ).subset_require(plan)

    changed_legacy = replace(legacy_review, description="foreign lifecycle")
    conflict = WorkflowConfigurationReconciler().plan_get(
        WorkflowConfigurationSnapshot(_destination(), [changed_legacy], [], [], [])
    )
    assert not conflict.can_mutate()
    assert conflict.issue_status_update_list == []
    assert ("issue-status", "Review", "legacy status is not the exact provider definition") in {
        (item.kind, item.name, item.reason) for item in conflict.conflict_list
    }

    current_review = _existing_status(ISSUE_STATUS_DESIRED[3], 5)
    duplicate = WorkflowConfigurationReconciler().plan_get(
        WorkflowConfigurationSnapshot(_destination(), [legacy_review, current_review], [], [], [])
    )
    assert not duplicate.can_mutate()
    assert ("issue-status", "Review", "current and legacy review statuses coexist") in {
        (item.kind, item.name, item.reason) for item in duplicate.conflict_list
    }

    legacy_merging = _existing_status(ISSUE_STATUS_LEGACY_MERGING, 6)
    merging_status_list = [
        _existing_status(item, index) for index, item in enumerate(ISSUE_STATUS_DESIRED, 1) if item.name != "Merging"
    ]
    merging_status_list.append(legacy_merging)
    merging_plan = WorkflowConfigurationReconciler().plan_get(
        WorkflowConfigurationSnapshot(_destination(), merging_status_list, [], [], [])
    )
    assert merging_plan.issue_status_update_list == [
        replace(next(item for item in ISSUE_STATUS_DESIRED if item.name == "Merging"), id=legacy_merging.id)
    ]


def test_configuration_rejects_wrong_category_and_foreign_label() -> None:
    """Same-name foreign or semantically wrong objects are never overwritten."""

    wrong_status = _existing_status(ISSUE_STATUS_DESIRED[0], 1)
    wrong_status = StatusDefinition(
        wrong_status.id,
        wrong_status.name,
        "started",
        wrong_status.color,
        wrong_status.description,
        wrong_status.position,
    )
    foreign_label = _existing_label(LABEL_DESIRED[0], 2)
    foreign_label = LinearLabel(foreign_label.id, foreign_label.name, foreign_label.color, "foreign owner")
    plan = WorkflowConfigurationReconciler().plan_get(
        WorkflowConfigurationSnapshot(_destination(), [wrong_status], [], [foreign_label], [])
    )

    assert not plan.can_mutate()
    assert {(item.kind, item.name) for item in plan.conflict_list} == {
        ("issue-status", "Backlog"),
        ("label", "task:implementation"),
    }

    wrong_color = replace(
        _existing_label(LABEL_DESIRED[0], 3),
        color="#000000",
    )
    color_plan = WorkflowConfigurationReconciler().plan_get(
        WorkflowConfigurationSnapshot(_destination(), [], [], [wrong_color], [])
    )
    assert ("label", "task:implementation") in {(item.kind, item.name) for item in color_plan.conflict_list}

    wrong_case_status = replace(_existing_status(ISSUE_STATUS_DESIRED[1], 4), name="todo")
    wrong_case_label = replace(_existing_label(LABEL_DESIRED[0], 5), name="TASK:IMPLEMENTATION")
    casing_plan = WorkflowConfigurationReconciler().plan_get(
        WorkflowConfigurationSnapshot(_destination(), [wrong_case_status], [], [wrong_case_label], [])
    )
    assert {(item.kind, item.name, item.reason) for item in casing_plan.conflict_list} == {
        ("issue-status", "Todo", "same name uses different casing"),
        ("label", "task:implementation", "same name uses different casing"),
    }


def test_dispatchability_distinguishes_codex_review_from_final_human_boundary() -> None:
    """Review dispatches implementation only; final acceptance remains human-owned."""

    ready = TaskExecutionSnapshot(
        issue_status=IssueStatusName.TODO,
        project_status=ProjectStatusName.IN_PROGRESS,
        role_label="task:implementation",
        delivery_kind="code",
        label_name_list=["task:implementation", "agent:codex"],
        assignee_id=VIEWER_ID,
        delegate_id="",
        execution_identity_id=VIEWER_ID,
        unresolved_blocker_count=0,
        issue_contract_complete=True,
    )

    assert ready.can_dispatch()
    assert replace(ready, issue_status=IssueStatusName.REVIEW).can_dispatch()
    assert replace(ready, issue_status=IssueStatusName.MERGING).can_dispatch()
    assert not replace(ready, unresolved_blocker_count=1).can_dispatch()
    assert not replace(ready, project_status=ProjectStatusName.PLANNED).can_dispatch()
    assert not replace(
        ready,
        issue_status=IssueStatusName.REVIEW,
        role_label="task:acceptance",
        delivery_kind="evidence",
        label_name_list=["task:acceptance", "agent:codex"],
    ).can_dispatch()
    assert not replace(
        ready,
        issue_status=IssueStatusName.MERGING,
        role_label="task:review",
        delivery_kind="evidence",
        label_name_list=["task:review", "agent:codex"],
    ).can_dispatch()

    with pytest.raises(LinearContractError, match="exactly one"):
        replace(ready, delegate_id=VIEWER_ID)


def test_task_state_cli_accepts_legacy_review_text_as_current_semantic_review(tmp_path: Path) -> None:
    """An active Project remains operable on either side of status migration."""

    tool = LIBRARY_ROOT / "linear_boundary" / "tool" / "task.py"
    input_path = tmp_path / "dispatch.json"
    payload = {
        "schema_version": 1,
        "issue_status": "Human Review",
        "project_status": "In Progress",
        "role_label": "task:implementation",
        "delivery_kind": "code",
        "label_name_list": ["task:implementation", "agent:codex"],
        "assignee_id": VIEWER_ID,
        "delegate_id": "",
        "execution_identity_id": VIEWER_ID,
        "unresolved_blocker_count": 0,
        "issue_contract_complete": True,
    }
    input_path.write_text(json.dumps(payload), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(tool), "dispatch", "--input", str(input_path)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout)["dispatchable"] is True


def test_transition_requires_fresh_rework_and_complete_implementation_handoff() -> None:
    """Rework adopts workspace while implementation Review requires direct evidence."""

    with pytest.raises(LinearContractError, match="fresh thread"):
        _transition_require(
            current=IssueStatusName.REWORK,
            target=IssueStatusName.IN_PROGRESS,
            project_status=ProjectStatusName.IN_PROGRESS,
            role_label="task:implementation",
            delivery_kind="code",
            proof=TransitionProof(workspace_preserved=True),
            dispatchable=True,
        )
    _transition_require(
        current=IssueStatusName.REWORK,
        target=IssueStatusName.IN_PROGRESS,
        project_status=ProjectStatusName.IN_PROGRESS,
        role_label="task:implementation",
        delivery_kind="code",
        proof=TransitionProof(
            fresh_thread=True,
            workspace_preserved=True,
            attempt_cleanup_complete=True,
        ),
        dispatchable=True,
    )
    with pytest.raises(LinearContractError, match="nested attempt-resource cleanup"):
        _transition_require(
            current=IssueStatusName.REWORK,
            target=IssueStatusName.IN_PROGRESS,
            project_status=ProjectStatusName.IN_PROGRESS,
            role_label="task:implementation",
            delivery_kind="code",
            proof=TransitionProof(fresh_thread=True, workspace_preserved=True),
            dispatchable=True,
        )
    complete = TransitionProof(
        result_ready=True,
        verification_ready=True,
        publication_ready=True,
        required_ci_ready=True,
        evidence_ready=True,
        handoff_ready=True,
        attempt_cleanup_complete=True,
    )
    _transition_require(
        current=IssueStatusName.IN_PROGRESS,
        target=IssueStatusName.REVIEW,
        project_status=ProjectStatusName.IN_PROGRESS,
        role_label="task:implementation",
        delivery_kind="code",
        proof=complete,
        dispatchable=False,
    )
    with pytest.raises(LinearContractError, match="semantic handoff"):
        _transition_require(
            current=IssueStatusName.IN_PROGRESS,
            target=IssueStatusName.REVIEW,
            project_status=ProjectStatusName.IN_PROGRESS,
            role_label="task:implementation",
            delivery_kind="code",
            proof=replace(complete, handoff_ready=False),
            dispatchable=False,
        )


def test_independent_review_owns_code_merging_and_rework_gate() -> None:
    """Zero findings merge while findings rework without a human PR decision."""

    review_passed = TransitionProof(
        review_complete=True,
        reviewed_state_current=True,
        evidence_ready=True,
        handoff_ready=True,
        attempt_cleanup_complete=True,
    )
    _transition_require(
        current=IssueStatusName.REVIEW,
        target=IssueStatusName.MERGING,
        project_status=ProjectStatusName.IN_PROGRESS,
        role_label="task:implementation",
        delivery_kind="code",
        proof=review_passed,
        dispatchable=False,
    )
    _transition_require(
        current=IssueStatusName.REVIEW,
        target=IssueStatusName.REWORK,
        project_status=ProjectStatusName.IN_PROGRESS,
        role_label="task:implementation",
        delivery_kind="code",
        proof=TransitionProof(
            review_finding_ready=True,
            evidence_ready=True,
            handoff_ready=True,
            attempt_cleanup_complete=True,
        ),
        dispatchable=False,
    )
    _transition_require(
        current=IssueStatusName.REVIEW,
        target=IssueStatusName.REWORK,
        project_status=ProjectStatusName.IN_PROGRESS,
        role_label="task:implementation",
        delivery_kind="code",
        proof=TransitionProof(
            reviewed_state_changed=True,
            evidence_ready=True,
            handoff_ready=True,
            attempt_cleanup_complete=True,
        ),
        dispatchable=False,
    )
    with pytest.raises(LinearContractError, match="review handoff"):
        _transition_require(
            current=IssueStatusName.REVIEW,
            target=IssueStatusName.MERGING,
            project_status=ProjectStatusName.IN_PROGRESS,
            role_label="task:implementation",
            delivery_kind="code",
            proof=replace(review_passed, handoff_ready=False),
            dispatchable=False,
        )
    with pytest.raises(LinearContractError, match="zero-finding independent review"):
        _transition_require(
            current=IssueStatusName.REVIEW,
            target=IssueStatusName.MERGING,
            project_status=ProjectStatusName.IN_PROGRESS,
            role_label="task:implementation",
            delivery_kind="code",
            proof=TransitionProof(
                human_decision=True,
                reviewed_state_current=True,
                evidence_ready=True,
                handoff_ready=True,
            ),
            dispatchable=False,
        )


def test_evidence_review_and_acceptance_keep_only_final_human_boundary() -> None:
    """Evidence implementation is agent-reviewed while acceptance waits for a human."""

    ready = TransitionProof(
        result_ready=True,
        verification_ready=True,
        evidence_ready=True,
        handoff_ready=True,
        local_phase_baseline_readback_ready=True,
        attempt_cleanup_complete=True,
    )
    _transition_require(
        current=IssueStatusName.IN_PROGRESS,
        target=IssueStatusName.REVIEW,
        project_status=ProjectStatusName.IN_PROGRESS,
        role_label="task:implementation",
        delivery_kind="evidence",
        proof=ready,
        dispatchable=False,
    )
    with pytest.raises(LinearContractError, match="local phase baseline"):
        _transition_require(
            current=IssueStatusName.IN_PROGRESS,
            target=IssueStatusName.REVIEW,
            project_status=ProjectStatusName.IN_PROGRESS,
            role_label="task:acceptance",
            delivery_kind="evidence",
            proof=replace(ready, local_phase_baseline_readback_ready=False),
            dispatchable=False,
        )
    _transition_require(
        current=IssueStatusName.REVIEW,
        target=IssueStatusName.DONE,
        project_status=ProjectStatusName.IN_PROGRESS,
        role_label="task:implementation",
        delivery_kind="evidence",
        proof=TransitionProof(
            review_complete=True,
            reviewed_state_current=True,
            evidence_ready=True,
            handoff_ready=True,
            attempt_cleanup_complete=True,
        ),
        dispatchable=False,
    )
    _transition_require(
        current=IssueStatusName.IN_PROGRESS,
        target=IssueStatusName.REVIEW,
        project_status=ProjectStatusName.IN_PROGRESS,
        role_label="task:acceptance",
        delivery_kind="evidence",
        proof=ready,
        dispatchable=False,
    )
    _transition_require(
        current=IssueStatusName.REVIEW,
        target=IssueStatusName.DONE,
        project_status=ProjectStatusName.IN_PROGRESS,
        role_label="task:acceptance",
        delivery_kind="evidence",
        proof=TransitionProof(
            human_decision=True,
            reviewed_state_current=True,
            evidence_ready=True,
            handoff_ready=True,
            attempt_cleanup_complete=True,
        ),
        dispatchable=False,
    )
    _transition_require(
        current=IssueStatusName.REVIEW,
        target=IssueStatusName.REWORK,
        project_status=ProjectStatusName.IN_PROGRESS,
        role_label="task:acceptance",
        delivery_kind="evidence",
        proof=TransitionProof(
            human_decision=True,
            evidence_ready=True,
            handoff_ready=True,
            attempt_cleanup_complete=True,
        ),
        dispatchable=False,
    )


def test_post_merge_review_completes_directly_or_returns_with_remediation() -> None:
    """Graph review has an agent-owned zero-finding boundary and no hidden fix."""

    _transition_require(
        current=IssueStatusName.IN_PROGRESS,
        target=IssueStatusName.DONE,
        project_status=ProjectStatusName.IN_PROGRESS,
        role_label="task:review",
        delivery_kind="evidence",
        proof=TransitionProof(
            review_complete=True,
            evidence_ready=True,
            handoff_ready=True,
            attempt_cleanup_complete=True,
        ),
        dispatchable=False,
    )
    _transition_require(
        current=IssueStatusName.IN_PROGRESS,
        target=IssueStatusName.TODO,
        project_status=ProjectStatusName.IN_PROGRESS,
        role_label="task:review",
        delivery_kind="evidence",
        proof=TransitionProof(
            remediation_blocker_ready=True,
            evidence_ready=True,
            handoff_ready=True,
            attempt_cleanup_complete=True,
        ),
        dispatchable=False,
    )
    with pytest.raises(LinearContractError, match="review or acceptance"):
        _transition_require(
            current=IssueStatusName.IN_PROGRESS,
            target=IssueStatusName.TODO,
            project_status=ProjectStatusName.IN_PROGRESS,
            role_label="task:implementation",
            delivery_kind="code",
            proof=TransitionProof(remediation_blocker_ready=True, evidence_ready=True, handoff_ready=True),
            dispatchable=False,
        )


def test_merge_returns_changed_reviewed_identity_to_rework() -> None:
    """Merge never fixes a PR whose independently reviewed base or head changed."""

    _transition_require(
        current=IssueStatusName.MERGING,
        target=IssueStatusName.REWORK,
        project_status=ProjectStatusName.IN_PROGRESS,
        role_label="task:implementation",
        delivery_kind="code",
        proof=TransitionProof(
            reviewed_state_changed=True,
            evidence_ready=True,
            handoff_ready=True,
            attempt_cleanup_complete=True,
        ),
        dispatchable=False,
    )
    with pytest.raises(LinearContractError, match="reviewed PR identity changed"):
        _transition_require(
            current=IssueStatusName.MERGING,
            target=IssueStatusName.REWORK,
            project_status=ProjectStatusName.IN_PROGRESS,
            role_label="task:implementation",
            delivery_kind="code",
            proof=TransitionProof(),
            dispatchable=False,
        )


def test_atomic_merge_lease_rejection_requires_cleanup_before_rework() -> None:
    """A preflight-to-mutation ref race is stale review state, never a merge retry."""

    proof = TransitionProof(
        reviewed_state_changed=True,
        evidence_ready=True,
        handoff_ready=True,
        attempt_cleanup_complete=True,
    )
    _transition_require(
        current=IssueStatusName.MERGING,
        target=IssueStatusName.REWORK,
        project_status=ProjectStatusName.IN_PROGRESS,
        role_label="task:implementation",
        delivery_kind="code",
        proof=proof,
        dispatchable=False,
    )
    with pytest.raises(LinearContractError, match="nested attempt-resource cleanup"):
        _transition_require(
            current=IssueStatusName.MERGING,
            target=IssueStatusName.REWORK,
            project_status=ProjectStatusName.IN_PROGRESS,
            role_label="task:implementation",
            delivery_kind="code",
            proof=replace(proof, attempt_cleanup_complete=False),
            dispatchable=False,
        )


@pytest.mark.parametrize(
    ("current", "target", "role_label", "delivery_kind", "proof"),
    (
        (
            IssueStatusName.REVIEW,
            IssueStatusName.MERGING,
            "task:implementation",
            "code",
            TransitionProof(
                review_complete=True,
                reviewed_state_current=True,
                evidence_ready=True,
                handoff_ready=True,
            ),
        ),
        (
            IssueStatusName.REVIEW,
            IssueStatusName.REWORK,
            "task:implementation",
            "code",
            TransitionProof(review_finding_ready=True, evidence_ready=True, handoff_ready=True),
        ),
        (
            IssueStatusName.REVIEW,
            IssueStatusName.REWORK,
            "task:implementation",
            "code",
            TransitionProof(reviewed_state_changed=True, evidence_ready=True, handoff_ready=True),
        ),
        (
            IssueStatusName.IN_PROGRESS,
            IssueStatusName.REVIEW,
            "task:acceptance",
            "evidence",
            TransitionProof(
                result_ready=True,
                verification_ready=True,
                evidence_ready=True,
                handoff_ready=True,
                local_phase_baseline_readback_ready=True,
            ),
        ),
        (
            IssueStatusName.IN_PROGRESS,
            IssueStatusName.TODO,
            "task:acceptance",
            "evidence",
            TransitionProof(remediation_blocker_ready=True, evidence_ready=True, handoff_ready=True),
        ),
        (
            IssueStatusName.MERGING,
            IssueStatusName.DONE,
            "task:implementation",
            "code",
            TransitionProof(
                reviewed_state_current=True,
                merge_complete=True,
                evidence_ready=True,
                handoff_ready=True,
            ),
        ),
        (
            IssueStatusName.MERGING,
            IssueStatusName.REWORK,
            "task:implementation",
            "code",
            TransitionProof(reviewed_state_changed=True, evidence_ready=True, handoff_ready=True),
        ),
    ),
)
def test_review_accept_merge_transitions_require_prior_attempt_cleanup(
    current: IssueStatusName,
    target: IssueStatusName,
    role_label: str,
    delivery_kind: str,
    proof: TransitionProof,
) -> None:
    """Success, finding and stale transitions cannot precede nested cleanup."""

    with pytest.raises(LinearContractError, match="nested attempt-resource cleanup"):
        _transition_require(
            current=current,
            target=target,
            project_status=ProjectStatusName.IN_PROGRESS,
            role_label=role_label,
            delivery_kind=delivery_kind,
            proof=proof,
            dispatchable=False,
        )


def test_merge_and_cleanup_completion_require_semantic_handoffs() -> None:
    """Merge and cleanup cannot reach terminal state before evidence readback."""

    merge_complete = TransitionProof(
        reviewed_state_current=True,
        merge_complete=True,
        evidence_ready=True,
        handoff_ready=True,
        attempt_cleanup_complete=True,
    )
    _transition_require(
        current=IssueStatusName.MERGING,
        target=IssueStatusName.DONE,
        project_status=ProjectStatusName.IN_PROGRESS,
        role_label="task:implementation",
        delivery_kind="code",
        proof=merge_complete,
        dispatchable=False,
    )
    with pytest.raises(LinearContractError, match="semantic handoff"):
        _transition_require(
            current=IssueStatusName.MERGING,
            target=IssueStatusName.DONE,
            project_status=ProjectStatusName.IN_PROGRESS,
            role_label="task:implementation",
            delivery_kind="code",
            proof=replace(merge_complete, handoff_ready=False),
            dispatchable=False,
        )

    cleanup_complete = TransitionProof(
        cleanup_complete=True,
        evidence_ready=True,
        handoff_ready=True,
        attempt_cleanup_complete=True,
    )
    _transition_require(
        current=IssueStatusName.IN_PROGRESS,
        target=IssueStatusName.DONE,
        project_status=ProjectStatusName.IN_PROGRESS,
        role_label="task:cleanup",
        delivery_kind="cleanup",
        proof=cleanup_complete,
        dispatchable=False,
    )
    with pytest.raises(LinearContractError, match="Direct completion"):
        _transition_require(
            current=IssueStatusName.IN_PROGRESS,
            target=IssueStatusName.DONE,
            project_status=ProjectStatusName.IN_PROGRESS,
            role_label="task:cleanup",
            delivery_kind="cleanup",
            proof=replace(cleanup_complete, evidence_ready=False),
            dispatchable=False,
        )


def test_transition_rejects_lifecycle_progress_after_project_stop() -> None:
    """Project-first cancellation prevents a racing task from publishing Review."""

    with pytest.raises(LinearContractError, match="active Project"):
        _transition_require(
            current=IssueStatusName.IN_PROGRESS,
            target=IssueStatusName.REVIEW,
            project_status=ProjectStatusName.CANCELED,
            role_label="task:implementation",
            delivery_kind="code",
            proof=TransitionProof(
                result_ready=True,
                verification_ready=True,
                publication_ready=True,
                required_ci_ready=True,
                evidence_ready=True,
                handoff_ready=True,
            ),
            dispatchable=False,
        )
    _transition_require(
        current=IssueStatusName.IN_PROGRESS,
        target=IssueStatusName.CANCELED,
        project_status=ProjectStatusName.CANCELED,
        role_label="task:implementation",
        delivery_kind="code",
        proof=TransitionProof(human_decision=True, attempt_cleanup_complete=True),
        dispatchable=False,
    )
    with pytest.raises(LinearContractError, match="nested attempt-resource cleanup"):
        _transition_require(
            current=IssueStatusName.IN_PROGRESS,
            target=IssueStatusName.CANCELED,
            project_status=ProjectStatusName.CANCELED,
            role_label="task:implementation",
            delivery_kind="code",
            proof=TransitionProof(human_decision=True),
            dispatchable=False,
        )
    with pytest.raises(LinearContractError, match="completed Project"):
        _transition_require(
            current=IssueStatusName.IN_PROGRESS,
            target=IssueStatusName.CANCELED,
            project_status=ProjectStatusName.COMPLETED,
            role_label="task:implementation",
            delivery_kind="code",
            proof=TransitionProof(human_decision=True),
            dispatchable=False,
        )


class _Response:
    """Provide a context-managed fake HTTP response."""

    def __init__(self, payload: object) -> None:
        self.headers = Message()
        self._body = json.dumps(payload).encode()

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        del exc_type, exc_value, traceback

    def read(self) -> bytes:
        return self._body


def test_transport_redacts_credential_and_retries_only_safe_operation() -> None:
    """A rate-limited read uses guidance while credential and raw errors stay hidden."""

    response_list: list[object] = [
        urllib.error.HTTPError(
            LinearGraphQLTransport.ENDPOINT,
            429,
            "secret provider message",
            {"Retry-After": "2"},
            io.BytesIO(b"secret response"),
        ),
        _Response({"data": {"viewer": {"id": VIEWER_ID}}}),
    ]
    delay_list: list[float] = []

    def opener(_request: object, *, timeout: int) -> object:
        assert timeout == 30
        value = response_list.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    transport = LinearGraphQLTransport(
        "linear-secret-token",
        retry=LinearRetryPolicy(attempt_count=2),
        opener=opener,
        sleeper=delay_list.append,
        random_source=lambda: 0.5,
    )

    result = transport.execute(
        operation_name="ReadViewer",
        document="query ReadViewer { viewer { id } }",
        variables={},
        repeat_safe=True,
    )

    assert result == {"viewer": {"id": VIEWER_ID}}
    assert delay_list == [2.0]
    assert "linear-secret-token" not in repr(transport)


def test_transport_parses_http_400_graphql_rate_limit_and_uses_reset_guidance() -> None:
    """Linear's documented HTTP-400 rate-limit response remains safely retryable for reads."""

    response_list: list[object] = [
        urllib.error.HTTPError(
            LinearGraphQLTransport.ENDPOINT,
            400,
            "provider detail",
            {"X-RateLimit-Requests-Reset": "1030000"},
            io.BytesIO(
                json.dumps(
                    {
                        "errors": [
                            {
                                "message": "sensitive",
                                "extensions": {"code": "RATELIMITED"},
                            }
                        ]
                    }
                ).encode()
            ),
        ),
        _Response({"data": {"viewer": {"id": VIEWER_ID}}}),
    ]
    delay_list: list[float] = []

    def opener(_request: object, *, timeout: int) -> object:
        assert timeout == 30
        value = response_list.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    transport = LinearGraphQLTransport(
        "secret",
        retry=LinearRetryPolicy(attempt_count=2, maximum_delay_seconds=20),
        opener=opener,
        sleeper=delay_list.append,
        clock=lambda: 1000.0,
    )

    result = transport.execute(
        operation_name="ReadViewer",
        document="query ReadViewer { viewer { id } }",
        variables={},
        repeat_safe=True,
    )

    assert result == {"viewer": {"id": VIEWER_ID}}
    assert delay_list == [20.0]


def test_transport_retries_transient_server_failure_only_for_safe_operation() -> None:
    """A repeat-safe read recovers from a standard transient provider 500 response."""

    response_list: list[object] = [
        urllib.error.HTTPError(
            LinearGraphQLTransport.ENDPOINT,
            500,
            "provider detail",
            {},
            io.BytesIO(b"sensitive body"),
        ),
        _Response({"data": {"viewer": {"id": VIEWER_ID}}}),
    ]

    def opener(_request: object, *, timeout: int) -> object:
        assert timeout == 30
        value = response_list.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    result = LinearGraphQLTransport(
        "secret",
        retry=LinearRetryPolicy(attempt_count=2, initial_delay_seconds=0, maximum_delay_seconds=0),
        opener=opener,
        sleeper=lambda _delay: None,
    ).execute(
        operation_name="ReadViewer",
        document="query ReadViewer { viewer { id } }",
        variables={},
        repeat_safe=True,
    )

    assert result == {"viewer": {"id": VIEWER_ID}}


def test_transport_classifies_auth_and_graphql_errors_without_raw_payload() -> None:
    """Authentication and HTTP-200 GraphQL failures remain distinct and redacted."""

    def auth_opener(_request: object, *, timeout: int) -> object:
        del timeout
        raise urllib.error.HTTPError(
            LinearGraphQLTransport.ENDPOINT,
            401,
            "raw token message",
            {},
            io.BytesIO(b"raw body"),
        )

    with pytest.raises(LinearAuthenticationError, match="credential") as auth_error:
        LinearGraphQLTransport("secret", opener=auth_opener).execute(
            operation_name="Viewer",
            document="query Viewer { viewer { id } }",
            variables={},
            repeat_safe=True,
        )
    assert "raw" not in str(auth_error.value)

    transport = LinearGraphQLTransport(
        "secret",
        opener=lambda _request, timeout: _Response(
            {
                "data": None,
                "errors": [{"message": "sensitive", "extensions": {"code": "BAD_USER_INPUT"}}],
            }
        ),
    )
    with pytest.raises(LinearResponseError) as response_error:
        transport.execute(
            operation_name="Mutation",
            document="mutation Mutation { noop }",
            variables={},
            repeat_safe=False,
        )
    assert "sensitive" not in str(response_error.value)


def test_graphql_configuration_fully_paginates_and_guards_exact_destination() -> None:
    """Status reads bind every page to the approved admin workspace and team."""

    transport = _ScriptedTransport(
        [
            _workflow_response(ISSUE_STATUS_DESIRED[:4], has_next=True, end_cursor="next-page"),
            _workflow_response(ISSUE_STATUS_DESIRED[4:]),
            _git_status_automation_response(
                [_git_status_automation(1, event="start")],
                has_next=True,
                end_cursor="next-automation-page",
            ),
            _git_status_automation_response([_git_status_automation(2, target_branch=True, workflow_state=False)]),
            _project_status_response(PROJECT_STATUS_DESIRED),
        ]
    )
    service = LinearWorkflowConfigurationGraphQL(transport, WorkflowConfigurationReconciler())

    current = service.read(
        expected_workspace_id=WORKSPACE_ID,
        expected_viewer_id=VIEWER_ID,
        expected_team_id=TEAM_ID,
    )

    assert [item.name for item in current.issue_status_list] == [item.name for item in ISSUE_STATUS_DESIRED]
    assert [item.name for item in current.project_status_list] == [item.name for item in PROJECT_STATUS_DESIRED]
    assert current.git_status_automation_list == [
        _git_status_automation(1, event="start"),
        _git_status_automation(2, target_branch=True, workflow_state=False),
    ]
    assert transport.call_list[1]["variables"]["after"] == "next-page"
    assert transport.call_list[3]["variables"]["after"] == "next-automation-page"
    assert all(item["variables"]["viewerId"] == VIEWER_ID for item in transport.call_list[:2])
    assert "membership(userId: $viewerId)" in transport.call_list[0]["document"]
    assert "gitAutomationStates(first: 100" in transport.call_list[2]["document"]
    assert "projectStatuses(first: 100" in transport.call_list[4]["document"]
    assert all(item["repeat_safe"] is True for item in transport.call_list)


def test_graphql_configuration_plan_can_discover_workspace_but_binds_its_fingerprint() -> None:
    """The first read-only plan discovers one workspace and makes it approved-plan state."""

    transport = _ScriptedTransport(
        [
            _workflow_response(ISSUE_STATUS_DESIRED),
            _git_status_automation_response([]),
            _project_status_response(PROJECT_STATUS_DESIRED),
        ]
    )
    service = LinearWorkflowConfigurationGraphQL(transport, WorkflowConfigurationReconciler())

    plan = service.plan(
        expected_workspace_id=None,
        expected_viewer_id=VIEWER_ID,
        expected_team_id=TEAM_ID,
        label_list=list(_existing_label(item, index) for index, item in enumerate(LABEL_DESIRED, 1)),
    )

    assert plan.destination == _destination()
    assert plan.is_current()
    assert ConfigurationPlan.from_payload(plan.payload()) == plan


def test_graphql_configuration_rereads_approved_destination_before_status_mutation() -> None:
    """Apply creates only statuses still missing from a freshly guarded approved plan."""

    partial_issue_status_list = ISSUE_STATUS_DESIRED[:-1]
    partial_project_status_list = PROJECT_STATUS_DESIRED[:-1]
    current_snapshot = WorkflowConfigurationSnapshot(
        destination=_destination(),
        issue_status_list=list(
            _existing_status(item, index) for index, item in enumerate(partial_issue_status_list, 1)
        ),
        project_status_list=list(
            _existing_status(item, index + 20) for index, item in enumerate(partial_project_status_list, 1)
        ),
        label_list=list(_existing_label(item, index) for index, item in enumerate(LABEL_DESIRED, 1)),
        git_status_automation_list=[],
    )
    approved = WorkflowConfigurationReconciler().plan_get(current_snapshot).status_identifier_allocate()
    transport = _ScriptedTransport(
        [
            _workflow_response(partial_issue_status_list),
            _git_status_automation_response([]),
            _project_status_response(partial_project_status_list),
            {"workflowStateCreate": {"success": True}},
            {"projectStatusCreate": {"success": True}},
            _workflow_response(ISSUE_STATUS_DESIRED),
            _git_status_automation_response([]),
            _project_status_response(PROJECT_STATUS_DESIRED),
        ]
    )
    service = LinearWorkflowConfigurationGraphQL(transport, WorkflowConfigurationReconciler())

    service.approved_configuration_apply(
        expected_workspace_id=WORKSPACE_ID,
        expected_viewer_id=VIEWER_ID,
        expected_team_id=TEAM_ID,
        approved_plan=approved,
    )

    operation_list = [item["operation_name"] for item in transport.call_list]
    assert operation_list == [
        "LinearAgentWorkflowConfiguration",
        "LinearAgentGitStatusAutomations",
        "LinearAgentProjectStatuses",
        "LinearAgentWorkflowStateCreate",
        "LinearAgentProjectStatusCreate",
        "LinearAgentWorkflowConfiguration",
        "LinearAgentGitStatusAutomations",
        "LinearAgentProjectStatuses",
    ]
    create_call = transport.call_list[3]
    assert create_call["repeat_safe"] is False
    assert create_call["variables"]["input"]["name"] == "Canceled"
    assert create_call["variables"]["input"]["id"] == approved.issue_status_create_list[0].id
    assert uuid.UUID(create_call["variables"]["input"]["id"]).version == 4
    project_create_call = transport.call_list[4]
    assert project_create_call["repeat_safe"] is False
    assert project_create_call["variables"]["input"]["name"] == "Canceled"
    assert project_create_call["variables"]["input"]["id"] == approved.project_status_create_list[0].id
    assert "status { id name type color description position }" in project_create_call["document"]


def test_graphql_configuration_updates_legacy_lifecycle_in_place_and_reads_back_identity() -> None:
    """Native update mutations preserve review and Merging status identities."""

    legacy_issue_status_list = [
        (
            ISSUE_STATUS_LEGACY_REVIEW
            if item.name == "Review"
            else ISSUE_STATUS_LEGACY_MERGING if item.name == "Merging" else item
        )
        for item in ISSUE_STATUS_DESIRED
    ]
    planning_transport = _ScriptedTransport(
        [
            _workflow_response(legacy_issue_status_list),
            _git_status_automation_response([]),
            _project_status_response(PROJECT_STATUS_DESIRED),
        ]
    )
    approved = LinearWorkflowConfigurationGraphQL(planning_transport, WorkflowConfigurationReconciler()).plan(
        expected_workspace_id=WORKSPACE_ID,
        expected_viewer_id=VIEWER_ID,
        expected_team_id=TEAM_ID,
        label_list=list(_existing_label(item, index) for index, item in enumerate(LABEL_DESIRED, 1)),
    )
    assert [item.name for item in approved.issue_status_update_list] == ["Review", "Merging"]
    review_update, merging_update = approved.issue_status_update_list

    transport = _ScriptedTransport(
        [
            _workflow_response(legacy_issue_status_list),
            _git_status_automation_response([]),
            _project_status_response(PROJECT_STATUS_DESIRED),
            _workflow_status_update_response(review_update),
            _workflow_status_update_response(merging_update),
            _workflow_response(ISSUE_STATUS_DESIRED),
            _git_status_automation_response([]),
            _project_status_response(PROJECT_STATUS_DESIRED),
        ]
    )

    LinearWorkflowConfigurationGraphQL(transport, WorkflowConfigurationReconciler()).approved_configuration_apply(
        expected_workspace_id=WORKSPACE_ID,
        expected_viewer_id=VIEWER_ID,
        expected_team_id=TEAM_ID,
        approved_plan=approved,
    )

    review_call, merging_call = transport.call_list[3:5]
    assert review_call["operation_name"] == "LinearAgentWorkflowStateUpdate"
    assert review_call["repeat_safe"] is False
    assert review_call["variables"]["id"] == review_update.id
    assert review_call["variables"]["input"]["name"] == "Review"
    assert "type" not in review_call["variables"]["input"]
    assert "workflowStateUpdate(id: $id" in review_call["document"]
    assert merging_call["operation_name"] == "LinearAgentWorkflowStateUpdate"
    assert merging_call["variables"]["id"] == merging_update.id
    assert merging_call["variables"]["input"]["description"] == (
        "Independently reviewed pull request heads are being merged"
    )
    assert not any(item["operation_name"] == "LinearAgentWorkflowStateCreate" for item in transport.call_list)


@pytest.mark.parametrize(
    ("field_name", "replacement", "remove_field", "message"),
    (
        ("position", None, True, "response has another shape"),
        ("id", "90000000-0000-4000-8000-000000000001", False, "full approved definition"),
        ("name", "Human Review", False, "full approved definition"),
        ("type", "completed", False, "full approved definition"),
        ("color", "#FFFFFF", False, "full approved definition"),
        ("description", "Waiting for human approval", False, "full approved definition"),
        ("position", 999.0, False, "full approved definition"),
    ),
)
def test_graphql_status_migration_rejects_partial_or_altered_mutation_response(
    field_name: str,
    replacement: object,
    remove_field: bool,
    message: str,
) -> None:
    """Mutation success is valid only with the complete approved status definition."""

    approved = _legacy_status_approved_plan_get()
    review_update = approved.issue_status_update_list[0]
    response_node = _exact_status_node(review_update)
    if remove_field:
        del response_node[field_name]
    else:
        response_node[field_name] = replacement
    transport = _ScriptedTransport(
        [
            _workflow_response(_legacy_issue_status_list_get()),
            _git_status_automation_response([]),
            _project_status_response(PROJECT_STATUS_DESIRED),
            {"workflowStateUpdate": {"success": True, "workflowState": response_node}},
        ]
    )

    with pytest.raises(LinearTransportError, match=message):
        LinearWorkflowConfigurationGraphQL(
            transport,
            WorkflowConfigurationReconciler(),
        ).approved_configuration_apply(
            expected_workspace_id=WORKSPACE_ID,
            expected_viewer_id=VIEWER_ID,
            expected_team_id=TEAM_ID,
            approved_plan=approved,
        )


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    (
        ("id", "90000000-0000-4000-8000-000000000002"),
        ("name", "Human Review"),
        ("type", "completed"),
        ("color", "#FFFFFF"),
        ("description", "Waiting for human approval"),
        ("position", 999.0),
    ),
)
def test_graphql_status_migration_rejects_altered_final_readback(
    field_name: str,
    replacement: object,
) -> None:
    """Final readback preserves the legacy ID and every approved definition field."""

    approved = _legacy_status_approved_plan_get()
    review_update, merging_update = approved.issue_status_update_list
    final_workflow_response = _workflow_response(ISSUE_STATUS_DESIRED)
    team = final_workflow_response["team"]
    assert isinstance(team, dict)
    states = team["states"]
    assert isinstance(states, dict)
    node_list = states["nodes"]
    assert isinstance(node_list, list)
    review_node = next(item for item in node_list if isinstance(item, dict) and item.get("name") == "Review")
    review_node[field_name] = replacement
    transport = _ScriptedTransport(
        [
            _workflow_response(_legacy_issue_status_list_get()),
            _git_status_automation_response([]),
            _project_status_response(PROJECT_STATUS_DESIRED),
            _workflow_status_update_response(review_update),
            _workflow_status_update_response(merging_update),
            final_workflow_response,
            _git_status_automation_response([]),
            _project_status_response(PROJECT_STATUS_DESIRED),
        ]
    )

    with pytest.raises(LinearContractError, match="preserved approved identity"):
        LinearWorkflowConfigurationGraphQL(
            transport,
            WorkflowConfigurationReconciler(),
        ).approved_configuration_apply(
            expected_workspace_id=WORKSPACE_ID,
            expected_viewer_id=VIEWER_ID,
            expected_team_id=TEAM_ID,
            approved_plan=approved,
        )


def test_graphql_configuration_deletes_every_exact_git_status_automation_before_readback() -> None:
    """Provider-owned task statuses cannot be changed by default or branch Git rules."""

    automation_list = [
        _git_status_automation(1, event="start"),
        _git_status_automation(2, event="merge", target_branch=True, workflow_state=False),
    ]
    current_snapshot = WorkflowConfigurationSnapshot(
        destination=_destination(),
        issue_status_list=list(_existing_status(item, index) for index, item in enumerate(ISSUE_STATUS_DESIRED, 1)),
        project_status_list=list(
            _existing_status(item, index + 20) for index, item in enumerate(PROJECT_STATUS_DESIRED, 1)
        ),
        label_list=list(_existing_label(item, index) for index, item in enumerate(LABEL_DESIRED, 1)),
        git_status_automation_list=automation_list,
    )
    approved = WorkflowConfigurationReconciler().plan_get(current_snapshot).status_identifier_allocate()
    assert approved.git_status_automation_delete_list == automation_list
    transport = _ScriptedTransport(
        [
            _workflow_response(ISSUE_STATUS_DESIRED),
            _git_status_automation_response(automation_list),
            _project_status_response(PROJECT_STATUS_DESIRED),
            {
                "gitAutomationStateDelete": {
                    "entityId": automation_list[0].id,
                    "success": True,
                }
            },
            {
                "gitAutomationStateDelete": {
                    "entityId": automation_list[1].id,
                    "success": True,
                }
            },
            _workflow_response(ISSUE_STATUS_DESIRED),
            _git_status_automation_response([]),
            _project_status_response(PROJECT_STATUS_DESIRED),
        ]
    )
    service = LinearWorkflowConfigurationGraphQL(transport, WorkflowConfigurationReconciler())

    service.approved_configuration_apply(
        expected_workspace_id=WORKSPACE_ID,
        expected_viewer_id=VIEWER_ID,
        expected_team_id=TEAM_ID,
        approved_plan=approved,
    )

    delete_call_list = [
        item for item in transport.call_list if item["operation_name"] == "LinearAgentGitStatusAutomationDelete"
    ]
    assert [item["variables"]["id"] for item in delete_call_list] == [item.id for item in automation_list]
    assert all(item["repeat_safe"] is False for item in delete_call_list)
    assert all("gitAutomationStateDelete" in item["document"] for item in delete_call_list)
