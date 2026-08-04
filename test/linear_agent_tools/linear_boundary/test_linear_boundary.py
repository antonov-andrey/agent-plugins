"""Behavior tests for Linear configuration, dispatch and transition boundaries."""

from __future__ import annotations

from email.message import Message
from dataclasses import asdict, replace
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

from linear_boundary.graphql import LinearWorkflowConfigurationGraphQL
from linear_boundary.model import (
    ISSUE_STATUS_DESIRED,
    LABEL_DESIRED,
    PROJECT_STATUS_DESIRED,
    ConfigurationPlan,
    DestinationIdentity,
    IssueStatusName,
    LinearContractError,
    LinearLabel,
    ProjectStatusName,
    StatusDefinition,
    TaskExecutionSnapshot,
    TransitionProof,
    WorkflowConfigurationSnapshot,
    configuration_plan_build,
    configuration_plan_status_identifiers_allocate,
    configuration_plan_status_identifiers_require,
    configuration_plan_subset_require,
    transition_require,
)
from linear_boundary.transport import (
    LinearAuthenticationError,
    LinearGraphQLTransport,
    LinearResponseError,
    RetryPolicy,
)

WORKSPACE_ID = "11111111-1111-4111-8111-111111111111"
VIEWER_ID = "22222222-2222-4222-8222-222222222222"
TEAM_ID = "33333333-3333-4333-8333-333333333333"


def _destination() -> DestinationIdentity:
    """Return one authorized deterministic destination.

    Returns:
        Authorized identity.
    """

    return DestinationIdentity(WORKSPACE_ID, VIEWER_ID, TEAM_ID, True, False, True)


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

    assert module._labels_load(snapshot) == (
        LinearLabel(
            id="00000000-0000-4000-8000-000000000001",
            name="Bug",
            color="#EB5757",
            description="",
        ),
    )


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
    approved = configuration_plan_status_identifiers_allocate(
        configuration_plan_build(WorkflowConfigurationSnapshot(_destination(), (), (), ()))
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
        def __init__(self, _transport: object) -> None:
            pass

        def plan(self, **_argument_by_name: object) -> ConfigurationPlan:
            return approved

        def missing_statuses_create(self, **argument_by_name: object) -> None:
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


def _workflow_response(
    status_list: tuple[StatusDefinition, ...],
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
    status_list: tuple[StatusDefinition, ...],
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
        issue_status_list=tuple(
            _existing_status(item, index) for index, item in enumerate(ISSUE_STATUS_DESIRED[:3], 1)
        ),
        project_status_list=(),
        label_list=(),
    )

    plan = configuration_plan_build(partial)

    assert [item.name for item in plan.issue_status_create_list] == [item.name for item in ISSUE_STATUS_DESIRED[3:]]
    assert plan.project_status_create_list == PROJECT_STATUS_DESIRED
    assert plan.label_create_list == LABEL_DESIRED
    assert plan.mutation_allowed()

    current = WorkflowConfigurationSnapshot(
        destination=_destination(),
        issue_status_list=tuple(_existing_status(item, index) for index, item in enumerate(ISSUE_STATUS_DESIRED, 1)),
        project_status_list=tuple(
            _existing_status(item, index + 20) for index, item in enumerate(PROJECT_STATUS_DESIRED, 1)
        ),
        label_list=tuple(_existing_label(item, index) for index, item in enumerate(LABEL_DESIRED, 1)),
    )

    assert configuration_plan_build(current).is_current()


def test_configuration_plan_roundtrip_and_fresh_subset_guard() -> None:
    """One approved fingerprint authorizes only an exact remaining subset."""

    approved = configuration_plan_status_identifiers_allocate(
        configuration_plan_build(WorkflowConfigurationSnapshot(_destination(), (), (), ()))
    )
    parsed = ConfigurationPlan.from_payload(approved.payload())

    assert parsed == approved
    assert parsed.fingerprint() == approved.fingerprint()
    configuration_plan_status_identifiers_require(approved)
    assert all(
        uuid.UUID(item.id).version == 4
        for item in (
            *approved.issue_status_create_list,
            *approved.project_status_create_list,
        )
    )
    assert configuration_plan_status_identifiers_allocate(approved) == approved
    current = ConfigurationPlan(
        destination=approved.destination,
        issue_status_create_list=tuple(replace(item, id="") for item in approved.issue_status_create_list[1:]),
        project_status_create_list=tuple(replace(item, id="") for item in approved.project_status_create_list),
        label_create_list=(),
        conflict_list=(),
    )
    configuration_plan_subset_require(current, approved)

    changed = replace(current.issue_status_create_list[0], color="#000000")
    with pytest.raises(LinearContractError, match="changed after approval"):
        configuration_plan_subset_require(
            replace(
                current,
                issue_status_create_list=(
                    changed,
                    *current.issue_status_create_list[1:],
                ),
            ),
            approved,
        )
    with pytest.raises(LinearContractError, match="destination changed"):
        configuration_plan_subset_require(
            replace(
                current,
                destination=replace(
                    current.destination,
                    workspace_id="44444444-4444-4444-8444-444444444444",
                ),
            ),
            approved,
        )


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
    plan = configuration_plan_build(
        WorkflowConfigurationSnapshot(_destination(), (wrong_status,), (), (foreign_label,))
    )

    assert not plan.mutation_allowed()
    assert {(item.kind, item.name) for item in plan.conflict_list} == {
        ("issue-status", "Backlog"),
        ("label", "task:implementation"),
    }

    wrong_color = replace(
        _existing_label(LABEL_DESIRED[0], 3),
        color="#000000",
    )
    color_plan = configuration_plan_build(WorkflowConfigurationSnapshot(_destination(), (), (), (wrong_color,)))
    assert ("label", "task:implementation") in {(item.kind, item.name) for item in color_plan.conflict_list}

    wrong_case_status = replace(_existing_status(ISSUE_STATUS_DESIRED[1], 4), name="todo")
    wrong_case_label = replace(_existing_label(LABEL_DESIRED[0], 5), name="TASK:IMPLEMENTATION")
    casing_plan = configuration_plan_build(
        WorkflowConfigurationSnapshot(_destination(), (wrong_case_status,), (), (wrong_case_label,))
    )
    assert {(item.kind, item.name, item.reason) for item in casing_plan.conflict_list} == {
        ("issue-status", "Todo", "same name uses different casing"),
        ("label", "task:implementation", "same name uses different casing"),
    }


def test_dispatchability_uses_exact_status_project_label_identity_and_blockers() -> None:
    """Todo is not a hidden blocked state and Human Review never dispatches."""

    ready = TaskExecutionSnapshot(
        issue_status=IssueStatusName.TODO,
        project_status=ProjectStatusName.IN_PROGRESS,
        role_label="task:implementation",
        delivery_kind="code",
        label_name_list=("task:implementation", "agent:codex"),
        assignee_id=VIEWER_ID,
        delegate_id="",
        execution_identity_id=VIEWER_ID,
        unresolved_blocker_count=0,
        issue_contract_complete=True,
    )

    assert ready.dispatchable()
    assert not replace(ready, unresolved_blocker_count=1).dispatchable()
    assert not replace(ready, issue_status=IssueStatusName.HUMAN_REVIEW).dispatchable()
    assert not replace(ready, project_status=ProjectStatusName.PLANNED).dispatchable()
    assert replace(ready, assignee_id="", delegate_id=VIEWER_ID).dispatchable()
    assert replace(ready, issue_status=IssueStatusName.MERGING).dispatchable()
    assert not replace(
        ready,
        issue_status=IssueStatusName.MERGING,
        role_label="task:review",
        delivery_kind="evidence",
        label_name_list=("task:review", "agent:codex"),
    ).dispatchable()

    with pytest.raises(LinearContractError, match="exactly one"):
        replace(ready, delegate_id=VIEWER_ID)

    with pytest.raises(LinearContractError, match="exact single Linear role label"):
        replace(ready, label_name_list=("task:human", "agent:codex"))


def test_task_state_cli_exposes_closed_dispatch_and_transition_gates(
    tmp_path: Path,
) -> None:
    """Every manual role skill can invoke the same deterministic state boundary."""

    tool = LIBRARY_ROOT / "linear_boundary" / "tool" / "task.py"
    dispatch_input = tmp_path / "dispatch.json"
    dispatch_payload = {
        "schema_version": 1,
        "issue_status": "Todo",
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
    dispatch_input.write_text(json.dumps(dispatch_payload), encoding="utf-8")

    ready = subprocess.run(
        [sys.executable, str(tool), "dispatch", "--input", str(dispatch_input)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert ready.returncode == 0
    assert json.loads(ready.stdout)["dispatchable"] is True

    dispatch_payload["unresolved_blocker_count"] = 1
    dispatch_input.write_text(json.dumps(dispatch_payload), encoding="utf-8")
    blocked = subprocess.run(
        [sys.executable, str(tool), "dispatch", "--input", str(dispatch_input)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert blocked.returncode == 1
    assert json.loads(blocked.stdout)["blocker_list"] == ["unresolved-blockers"]

    transition_input = tmp_path / "transition.json"
    transition_input.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "current_status": "Todo",
                "target_status": "In Progress",
                "project_status": "In Progress",
                "role_label": "task:implementation",
                "delivery_kind": "code",
                "dispatchable": True,
                "proof": {**asdict(TransitionProof()), "fresh_thread": True},
            }
        ),
        encoding="utf-8",
    )
    transition = subprocess.run(
        [sys.executable, str(tool), "transition", "--input", str(transition_input)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert transition.returncode == 0
    assert json.loads(transition.stdout)["transition_allowed"] is True


def test_transition_rejects_incompatible_role_delivery_pair_before_activation() -> None:
    """A role label cannot activate using another role's delivery contract."""

    with pytest.raises(LinearContractError, match="role and delivery kind are incompatible"):
        transition_require(
            current=IssueStatusName.BACKLOG,
            target=IssueStatusName.TODO,
            project_status=ProjectStatusName.PLANNED,
            role_label="task:human",
            delivery_kind="code",
            proof=TransitionProof(task_definition_ready=True),
            dispatchable=False,
        )


def test_transition_contract_requires_fresh_rework_and_exact_human_candidate() -> None:
    """Rework adopts state and merge approval cannot survive mutation."""

    with pytest.raises(LinearContractError, match="fresh thread"):
        transition_require(
            current=IssueStatusName.REWORK,
            target=IssueStatusName.IN_PROGRESS,
            project_status=ProjectStatusName.IN_PROGRESS,
            role_label="task:implementation",
            delivery_kind="code",
            proof=TransitionProof(workspace_preserved=True),
            dispatchable=True,
        )
    transition_require(
        current=IssueStatusName.REWORK,
        target=IssueStatusName.IN_PROGRESS,
        project_status=ProjectStatusName.IN_PROGRESS,
        role_label="task:implementation",
        delivery_kind="code",
        proof=TransitionProof(fresh_thread=True, workspace_preserved=True),
        dispatchable=True,
    )
    with pytest.raises(LinearContractError, match="unchanged code candidate"):
        transition_require(
            current=IssueStatusName.HUMAN_REVIEW,
            target=IssueStatusName.MERGING,
            project_status=ProjectStatusName.IN_PROGRESS,
            role_label="task:implementation",
            delivery_kind="code",
            proof=TransitionProof(human_decision=True, candidate_unchanged=False),
            dispatchable=False,
        )


def test_transition_contract_uses_delivery_specific_evidence_and_remediation_paths() -> None:
    """Evidence tasks need no fake PR/CI and findings use the exact remediation path."""

    transition_require(
        current=IssueStatusName.BACKLOG,
        target=IssueStatusName.TODO,
        project_status=ProjectStatusName.PLANNED,
        role_label="task:review",
        delivery_kind="evidence",
        proof=TransitionProof(task_definition_ready=True),
        dispatchable=False,
    )
    transition_require(
        current=IssueStatusName.IN_PROGRESS,
        target=IssueStatusName.HUMAN_REVIEW,
        project_status=ProjectStatusName.IN_PROGRESS,
        role_label="task:review",
        delivery_kind="evidence",
        proof=TransitionProof(
            result_ready=True,
            verification_ready=True,
            evidence_ready=True,
            candidate_fingerprint_ready=True,
        ),
        dispatchable=False,
    )
    transition_require(
        current=IssueStatusName.IN_PROGRESS,
        target=IssueStatusName.TODO,
        project_status=ProjectStatusName.IN_PROGRESS,
        role_label="task:acceptance",
        delivery_kind="evidence",
        proof=TransitionProof(remediation_blocker_ready=True),
        dispatchable=False,
    )
    with pytest.raises(LinearContractError, match="review or acceptance"):
        transition_require(
            current=IssueStatusName.IN_PROGRESS,
            target=IssueStatusName.TODO,
            project_status=ProjectStatusName.IN_PROGRESS,
            role_label="task:implementation",
            delivery_kind="code",
            proof=TransitionProof(remediation_blocker_ready=True),
            dispatchable=False,
        )


def test_transition_contract_returns_mutated_merge_candidate_to_rework() -> None:
    """A merge runner never changes an approved candidate in place."""

    transition_require(
        current=IssueStatusName.MERGING,
        target=IssueStatusName.REWORK,
        project_status=ProjectStatusName.IN_PROGRESS,
        role_label="task:implementation",
        delivery_kind="code",
        proof=TransitionProof(candidate_mutated=True),
        dispatchable=False,
    )
    with pytest.raises(LinearContractError, match="proven candidate mutation"):
        transition_require(
            current=IssueStatusName.MERGING,
            target=IssueStatusName.REWORK,
            project_status=ProjectStatusName.IN_PROGRESS,
            role_label="task:implementation",
            delivery_kind="code",
            proof=TransitionProof(),
            dispatchable=False,
        )


def test_transition_contract_rejects_lifecycle_progress_after_project_stop() -> None:
    """Project-first cancellation prevents a racing task from publishing a later state."""

    with pytest.raises(LinearContractError, match="active Project"):
        transition_require(
            current=IssueStatusName.IN_PROGRESS,
            target=IssueStatusName.HUMAN_REVIEW,
            project_status=ProjectStatusName.CANCELED,
            role_label="task:implementation",
            delivery_kind="code",
            proof=TransitionProof(
                result_ready=True,
                verification_ready=True,
                publication_ready=True,
                required_ci_ready=True,
                evidence_ready=True,
                candidate_fingerprint_ready=True,
            ),
            dispatchable=False,
        )

    transition_require(
        current=IssueStatusName.IN_PROGRESS,
        target=IssueStatusName.CANCELED,
        project_status=ProjectStatusName.CANCELED,
        role_label="task:implementation",
        delivery_kind="code",
        proof=TransitionProof(human_decision=True),
        dispatchable=False,
    )

    with pytest.raises(LinearContractError, match="completed Project"):
        transition_require(
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
        retry=RetryPolicy(attempt_count=2),
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
        retry=RetryPolicy(attempt_count=2, maximum_delay_seconds=20),
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
        retry=RetryPolicy(attempt_count=2, initial_delay_seconds=0, maximum_delay_seconds=0),
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
            _project_status_response(PROJECT_STATUS_DESIRED),
        ]
    )
    service = LinearWorkflowConfigurationGraphQL(transport)

    current = service.read(
        expected_workspace_id=WORKSPACE_ID,
        expected_viewer_id=VIEWER_ID,
        expected_team_id=TEAM_ID,
    )

    assert [item.name for item in current.issue_status_list] == [item.name for item in ISSUE_STATUS_DESIRED]
    assert [item.name for item in current.project_status_list] == [item.name for item in PROJECT_STATUS_DESIRED]
    assert transport.call_list[1]["variables"]["after"] == "next-page"
    assert all(item["variables"]["viewerId"] == VIEWER_ID for item in transport.call_list[:2])
    assert "membership(userId: $viewerId)" in transport.call_list[0]["document"]
    assert "projectStatuses(first: 100" in transport.call_list[2]["document"]
    assert all(item["repeat_safe"] is True for item in transport.call_list)


def test_graphql_configuration_plan_can_discover_workspace_but_binds_its_fingerprint() -> None:
    """The first read-only plan discovers one workspace and makes it approved-plan state."""

    transport = _ScriptedTransport(
        [
            _workflow_response(ISSUE_STATUS_DESIRED),
            _project_status_response(PROJECT_STATUS_DESIRED),
        ]
    )
    service = LinearWorkflowConfigurationGraphQL(transport)

    plan = service.plan(
        expected_workspace_id=None,
        expected_viewer_id=VIEWER_ID,
        expected_team_id=TEAM_ID,
        label_list=tuple(_existing_label(item, index) for index, item in enumerate(LABEL_DESIRED, 1)),
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
        issue_status_list=tuple(
            _existing_status(item, index) for index, item in enumerate(partial_issue_status_list, 1)
        ),
        project_status_list=tuple(
            _existing_status(item, index + 20) for index, item in enumerate(partial_project_status_list, 1)
        ),
        label_list=tuple(_existing_label(item, index) for index, item in enumerate(LABEL_DESIRED, 1)),
    )
    approved = configuration_plan_status_identifiers_allocate(configuration_plan_build(current_snapshot))
    transport = _ScriptedTransport(
        [
            _workflow_response(partial_issue_status_list),
            _project_status_response(partial_project_status_list),
            {"workflowStateCreate": {"success": True}},
            {"projectStatusCreate": {"success": True}},
            _workflow_response(ISSUE_STATUS_DESIRED),
            _project_status_response(PROJECT_STATUS_DESIRED),
        ]
    )
    service = LinearWorkflowConfigurationGraphQL(transport)

    service.missing_statuses_create(
        expected_workspace_id=WORKSPACE_ID,
        expected_viewer_id=VIEWER_ID,
        expected_team_id=TEAM_ID,
        approved_plan=approved,
    )

    operation_list = [item["operation_name"] for item in transport.call_list]
    assert operation_list == [
        "LinearAgentWorkflowConfiguration",
        "LinearAgentProjectStatuses",
        "LinearAgentWorkflowStateCreate",
        "LinearAgentProjectStatusCreate",
        "LinearAgentWorkflowConfiguration",
        "LinearAgentProjectStatuses",
    ]
    create_call = transport.call_list[2]
    assert create_call["repeat_safe"] is False
    assert create_call["variables"]["input"]["name"] == "Canceled"
    assert create_call["variables"]["input"]["id"] == approved.issue_status_create_list[0].id
    assert uuid.UUID(create_call["variables"]["input"]["id"]).version == 4
    project_create_call = transport.call_list[3]
    assert project_create_call["repeat_safe"] is False
    assert project_create_call["variables"]["input"]["name"] == "Canceled"
    assert project_create_call["variables"]["input"]["id"] == approved.project_status_create_list[0].id
    assert "status { id name type color description position }" in project_create_call["document"]
