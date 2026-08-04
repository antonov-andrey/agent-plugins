"""Render approved task graphs into Linear-visible publication content."""

from __future__ import annotations

from dataclasses import dataclass
import json
from urllib.parse import quote, urlsplit

from task_graph.delta import TaskGraphDelta
from task_graph.model import SourceIdentity, TaskBlockerEdge, TaskGraph, TaskNode


def linear_markdown_link(value: str) -> str:
    """Render one URL in the canonical Markdown form returned by Linear."""

    parsed = urlsplit(value)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        encoded_target = quote(value, safe=":/?#[]@!$&'()*+,;=%")
        return f"[{value}](<{encoded_target}>)"
    return value


@dataclass(frozen=True, slots=True)
class IssuePublication:
    """Contain the exact provider-owned fields for one staged Linear issue."""

    node_key: str
    title: str
    description: str
    status_name: str
    label_name_list: list[str]
    assignee_id: str
    delegate_id: str

    def __post_init__(self) -> None:
        """Detach the provider-owned label list from caller mutation."""

        object.__setattr__(self, "label_name_list", list(self.label_name_list))

    @classmethod
    def from_node(
        cls,
        *,
        source: SourceIdentity,
        source_fingerprint: str,
        node: TaskNode,
        provider_identity_detail_list: list[str],
    ) -> "IssuePublication":
        """Render one issue from the shared role-conditional template.

        Args:
            source: Immutable original source identity.
            source_fingerprint: Exact original source fingerprint.
            node: Exact task node.
            provider_identity_detail_list: Extra visible provider-owned identity lines.

        Returns:
            Complete staged issue publication.
        """

        assignment_kind = "assignee" if node.assignee_id else "delegate"
        assignment_id = node.assignee_id or node.delegate_id
        section_list = [
            f"# {node.title}",
            "",
            "## Provider Identity",
            "",
            "* Provider: `linear-agent-tools/v1`",
            f"* Source fingerprint: `{source_fingerprint}`",
            f"* Node key: `{node.node_key}`",
            f"* Full source key: `{source_fingerprint}:{node.node_key}`",
            f"* Role: `{node.role}`",
            f"* Delivery kind: `{node.delivery_kind}`",
            f"* Execution assignment: `{assignment_kind}` `{assignment_id}`",
            *provider_identity_detail_list,
            "",
            "## Outcome",
            "",
            node.outcome,
            "",
            "## Source",
            "",
            f"* Canonical source: {linear_markdown_link(source.canonical_url)}",
            f"* Revision: `{source.revision}`",
            "* Relevant sections:",
            *[f"  * {item}" for item in node.source_section_list],
            "",
            "## Scope",
            "",
            *[f"* {item}" for item in node.scope_list],
        ]
        if node.non_goal_list:
            section_list.extend(("", "## Non-goals", "", *[f"* {item}" for item in node.non_goal_list]))
        if node.repository_list:
            repository_heading = (
                "## Ordered Merge Plan And Partial Recovery"
                if node.delivery_kind == "code" and len(node.repository_list) > 1
                else "## Repositories And Base Branches"
            )
            section_list.extend(
                (
                    "",
                    repository_heading,
                    "",
                    *[
                        f"{index}. `{item.origin_url}` at `{item.base_branch}` using `{item.merge_method}`"
                        for index, item in enumerate(node.repository_list, 1)
                    ],
                )
            )
            if node.partial_merge_recovery:
                section_list.extend(("", "Partial-merge recovery:", "", node.partial_merge_recovery))
        section_list.extend(
            (
                "",
                "## Required Contracts And Skills",
                "",
                *[f"* Contract: {linear_markdown_link(item)}" for item in node.required_contract_list],
                *[f"* Skill: `{item}`" for item in node.required_skill_list],
                "",
                "## Blockers",
                "",
                *([f"* `{item}`" for item in node.blocker_key_list] or ["* None"]),
            )
        )
        if node.resource_list:
            section_list.extend(("", "## Resource Ownership And Lifetime", ""))
            for resource in node.resource_list:
                section_list.append(
                    f"* `{resource.key}`: owner `{resource.owner_identity}`, lifetime `{resource.lifetime}`, "
                    f"repository `{resource.repository_url}`, "
                    f"cleanup argv `{json.dumps(resource.cleanup_argument_list, ensure_ascii=False)}`, "
                    f"downstream consumers `{json.dumps(resource.consumer_node_key_list, ensure_ascii=False)}`, "
                    f"approval fingerprint `{resource.fingerprint()}`"
                )
        section_list.extend(("", "## Verification Plan", ""))
        for verification in node.verification_list:
            section_list.append(
                f"* `{verification.key}` ({verification.kind}): "
                f"repository `{verification.repository_url}`, working directory `{verification.working_directory}`, "
                f"`{json.dumps(verification.command_argument_list, ensure_ascii=False)}`; "
                f"dependencies `{json.dumps(verification.dependency_path_list, ensure_ascii=False)}`; "
                f"environment identity required `{str(verification.environment_identity_required).lower()}`"
            )
        section_list.extend(
            (
                "",
                "## Human Decision Boundary",
                "",
                node.human_decision_boundary,
                "",
                "## Evidence And Links",
                "",
                "Agent attempts append concise comments with commits, receipts, PRs, CI and telemetry. "
                "Raw logs, prompts and credentials are excluded.",
            )
        )
        return cls(
            node_key=node.node_key,
            title=node.title,
            description="\n".join(section_list),
            status_name="Backlog",
            label_name_list=[node.role],
            assignee_id=node.assignee_id,
            delegate_id=node.delegate_id,
        )

    def payload(self) -> dict[str, object]:
        """Return one canonical JSON-ready staged issue.

        Returns:
            Issue object.
        """

        return {
            "assignee_id": self.assignee_id,
            "delegate_id": self.delegate_id,
            "description": self.description,
            "label_name_list": list(self.label_name_list),
            "node_key": self.node_key,
            "status_name": self.status_name,
            "title": self.title,
        }


@dataclass(frozen=True, slots=True)
class GraphPublicationView:
    """Contain complete visible content for one Project import transaction."""

    project_key: str
    team_id: str
    project_name: str
    project_description: str
    project_status_name: str
    source_fingerprint: str
    graph_fingerprint: str
    import_document_title: str
    import_document_content: str
    issue_list: list[IssuePublication]
    blocker_edge_list: list[TaskBlockerEdge]

    def __post_init__(self) -> None:
        """Detach rendered collections from caller mutation."""

        object.__setattr__(self, "issue_list", list(self.issue_list))
        object.__setattr__(self, "blocker_edge_list", list(self.blocker_edge_list))

    @classmethod
    def from_graph(cls, graph: TaskGraph) -> "GraphPublicationView":
        """Render the exact staged Project document, issues and relations.

        Args:
            graph: Approved typed graph.

        Returns:
            Complete publication view.
        """

        source_fingerprint = graph.source_fingerprint()
        graph_fingerprint = graph.graph_fingerprint()
        project_description = project_description_build(project_key=graph.project_key(), source=graph.source)
        normalized_json = json.dumps(
            graph.normalized_payload(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        document = "\n".join(
            (
                "# Linear Agent Tools Import Plan",
                "",
                "This visible document is the transaction envelope for the initial graph import.",
                "Linear issues and blocker relations are the operational graph after activation.",
                "",
                "* Provider: `linear-agent-tools/v1`",
                f"* Project key: `{graph.project_key()}`",
                f"* Source fingerprint: `{source_fingerprint}`",
                f"* Graph fingerprint: `{graph_fingerprint}`",
                f"* Source: {linear_markdown_link(graph.source.canonical_url)}",
                f"* Revision: `{graph.source.revision}`",
                "",
                "```json",
                normalized_json,
                "```",
            )
        )
        issue_list = [
            IssuePublication.from_node(
                source=graph.source,
                source_fingerprint=source_fingerprint,
                node=node,
                provider_identity_detail_list=[],
            )
            for node in sorted(graph.node_list, key=lambda item: item.node_key)
        ]
        blocker_edge_list = sorted(
            [
                TaskBlockerEdge(blocker_node_key=blocker_key, blocked_node_key=node.node_key)
                for node in graph.node_list
                for blocker_key in node.blocker_key_list
            ],
            key=lambda item: (item.blocker_node_key, item.blocked_node_key),
        )
        return cls(
            project_key=graph.project_key(),
            team_id=graph.team_id,
            project_name=graph.project_name,
            project_description=project_description,
            project_status_name="Planned",
            source_fingerprint=source_fingerprint,
            graph_fingerprint=graph_fingerprint,
            import_document_title=f"Linear task graph import {source_fingerprint}",
            import_document_content=document,
            issue_list=issue_list,
            blocker_edge_list=blocker_edge_list,
        )

    def payload(self) -> dict[str, object]:
        """Return one canonical JSON-ready rendered view.

        Returns:
            The rendered publication object.
        """

        return {
            "schema_version": 1,
            "blocker_edge_list": [item.payload() for item in self.blocker_edge_list],
            "graph_fingerprint": self.graph_fingerprint,
            "import_document_content": self.import_document_content,
            "import_document_title": self.import_document_title,
            "issue_list": [item.payload() for item in self.issue_list],
            "project_key": self.project_key,
            "team_id": self.team_id,
            "project_name": self.project_name,
            "project_description": self.project_description,
            "project_status_name": self.project_status_name,
            "source_fingerprint": self.source_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class DeltaPublicationView:
    """Contain provider-owned fields for one approved active-Project delta."""

    project_id: str
    project_key: str
    source_fingerprint: str
    delta_fingerprint: str
    import_document_title: str
    import_document_content: str
    issue_list: list[IssuePublication]
    blocker_edge_list: list[TaskBlockerEdge]
    reverification_node_key_list: list[str]

    def __post_init__(self) -> None:
        """Detach rendered collections from caller mutation."""

        object.__setattr__(self, "issue_list", list(self.issue_list))
        object.__setattr__(self, "blocker_edge_list", list(self.blocker_edge_list))
        object.__setattr__(
            self,
            "reverification_node_key_list",
            list(self.reverification_node_key_list),
        )

    @classmethod
    def from_delta(cls, delta: TaskGraphDelta) -> "DeltaPublicationView":
        """Render staged issues and additive relations for one active Project delta.

        Args:
            delta: Exact approved delta.

        Returns:
            Complete visible delta publication view.
        """

        source_fingerprint = delta.source.fingerprint()
        delta_fingerprint = delta.fingerprint()
        normalized_json = json.dumps(
            delta.normalized_payload(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        document = "\n".join(
            (
                "# Linear Agent Tools Delta Plan",
                "",
                "This immutable document is the transaction receipt for one approved active-Project delta.",
                "Linear issues and blocker relations remain the operational graph.",
                "",
                "* Provider: `linear-agent-tools/v1`",
                f"* Project key: `{delta.project_key}`",
                f"* Source fingerprint: `{source_fingerprint}`",
                f"* Delta fingerprint: `{delta_fingerprint}`",
                f"* Delta provenance kind: `{delta.provenance.kind}`",
                f"* Delta provenance: {linear_markdown_link(delta.provenance.canonical_url)}",
                f"* Delta revision: `{delta.provenance.revision}`",
                "",
                "```json",
                normalized_json,
                "```",
            )
        )
        provider_identity_detail_list = [
            f"* Delta fingerprint: `{delta_fingerprint}`",
            f"* Delta provenance kind: `{delta.provenance.kind}`",
            f"* Delta provenance: {linear_markdown_link(delta.provenance.canonical_url)}",
            f"* Delta revision: `{delta.provenance.revision}`",
            f"* Approved decision: {delta.provenance.decision}",
        ]
        return cls(
            project_id=delta.project_id,
            project_key=delta.project_key,
            source_fingerprint=source_fingerprint,
            delta_fingerprint=delta_fingerprint,
            import_document_title=f"Linear task graph delta {delta_fingerprint}",
            import_document_content=document,
            issue_list=[
                IssuePublication.from_node(
                    source=delta.source,
                    source_fingerprint=source_fingerprint,
                    node=node,
                    provider_identity_detail_list=provider_identity_detail_list,
                )
                for node in sorted(delta.node_list, key=lambda item: item.node_key)
            ],
            blocker_edge_list=sorted(
                delta.blocker_edge_list,
                key=lambda item: (item.blocker_node_key, item.blocked_node_key),
            ),
            reverification_node_key_list=sorted(delta.reverification_node_key_list),
        )

    def payload(self) -> dict[str, object]:
        """Return one canonical JSON-ready rendered delta.

        Returns:
            Rendered delta object.
        """

        return {
            "schema_version": 1,
            "blocker_edge_list": [item.payload() for item in self.blocker_edge_list],
            "delta_fingerprint": self.delta_fingerprint,
            "import_document_content": self.import_document_content,
            "import_document_title": self.import_document_title,
            "issue_list": [item.payload() for item in self.issue_list],
            "project_id": self.project_id,
            "project_key": self.project_key,
            "reverification_node_key_list": list(self.reverification_node_key_list),
            "source_fingerprint": self.source_fingerprint,
        }


def project_description_build(*, project_key: str, source: SourceIdentity) -> str:
    """Render the immutable provider identity used for exact Project lookup."""

    return "\n".join(
        (
            "<!-- linear-agent-tools-project:v1 -->",
            "",
            "This Project is owned by one source-independent Linear task graph.",
            "",
            f"* Project key: `{project_key}`",
            f"* Source fingerprint: `{source.fingerprint()}`",
            f"* Canonical source: {linear_markdown_link(source.canonical_url)}",
            f"* Source revision: `{source.revision}`",
        )
    )
