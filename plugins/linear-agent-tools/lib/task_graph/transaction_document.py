"""Read provider-owned resource identities from visible Linear transaction documents."""

from __future__ import annotations

from collections.abc import Iterable
import re
from typing import Protocol

from json_contract import JsonContractError, json_load_strict
from task_graph.model import TaskGraphError

_RESOURCE_KEY_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_DOCUMENT_MARKER_BY_TITLE_PREFIX = {
    "Linear task graph import ": "# Linear Agent Tools Import Plan\n",
    "Linear task graph delta ": "# Linear Agent Tools Delta Plan\n",
}
_JSON_START = "\n```json\n"
_JSON_END = "\n```\n"


class TransactionDocument(Protocol):
    """Expose only fields required from one Project document snapshot."""

    title: str
    content: str


class TransactionDocumentReader:
    """Read provider-owned state from one complete Project document collection."""

    def __init__(self, document_list: Iterable[TransactionDocument]) -> None:
        """Bind a detached complete document collection for subsequent reads."""

        self._document_list = tuple(document_list)

    def accepted_resource_key_set_get(self, *, excluded_title: str) -> frozenset[str]:
        """Return all resource keys reserved by earlier provider transactions.

        Args:
            excluded_title: Current delta receipt title, when already persisted.

        Returns:
            Unique accepted resource-key set.
        """

        resource_key_set: set[str] = set()
        for document in self._document_list:
            if document.title == excluded_title:
                continue
            marker = next(
                (
                    value
                    for title_prefix, value in _DOCUMENT_MARKER_BY_TITLE_PREFIX.items()
                    if document.title.startswith(title_prefix)
                ),
                None,
            )
            if marker is None:
                continue
            payload = self._transaction_payload_get(document, marker=marker)
            node_list = payload.get("node_list")
            if not isinstance(node_list, list):
                raise TaskGraphError(
                    "Linear transaction document omits its normalized node list"
                )
            for node in node_list:
                if not isinstance(node, dict) or not isinstance(
                    node.get("resource_list"), list
                ):
                    raise TaskGraphError(
                        "Linear transaction document has malformed resource ownership"
                    )
                for resource in node["resource_list"]:
                    key = resource.get("key") if isinstance(resource, dict) else None
                    if (
                        not isinstance(key, str)
                        or _RESOURCE_KEY_PATTERN.fullmatch(key) is None
                    ):
                        raise TaskGraphError(
                            "Linear transaction document has malformed resource identity"
                        )
                    if key in resource_key_set:
                        raise TaskGraphError(
                            "Linear transaction documents repeat one accepted resource key"
                        )
                    resource_key_set.add(key)
        return frozenset(resource_key_set)

    @staticmethod
    def _transaction_payload_get(
        document: TransactionDocument, *, marker: str
    ) -> dict[str, object]:
        """Decode the one canonical JSON block from a provider transaction document."""

        if not document.content.startswith(marker):
            raise TaskGraphError(
                "Linear transaction document title collides with foreign content"
            )
        start = document.content.find(_JSON_START)
        end = (
            document.content.find(_JSON_END, start + len(_JSON_START))
            if start >= 0
            else -1
        )
        if (
            start < 0
            or end < 0
            or document.content.find(_JSON_START, start + len(_JSON_START)) >= 0
        ):
            raise TaskGraphError(
                "Linear transaction document has no unique normalized payload"
            )
        encoded = document.content[start + len(_JSON_START) : end]
        try:
            payload = json_load_strict(encoded)
        except JsonContractError as error:
            raise TaskGraphError(
                "Linear transaction document contains malformed normalized JSON"
            ) from error
        if not isinstance(payload, dict):
            raise TaskGraphError(
                "Linear transaction document normalized payload must be an object"
            )
        return payload
