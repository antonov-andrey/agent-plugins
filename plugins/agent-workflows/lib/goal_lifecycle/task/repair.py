"""Operation-local deterministic-repair reporting for task lifecycle commands."""

from __future__ import annotations


class TaskRepairReport:
    """Collect each stable repair diagnostic at most once per command."""

    def __init__(self) -> None:
        """Initialize the task repair report dependencies."""

        self._item_list: list[str] = []
        self._item_set: set[str] = set()

    def record(self, value: str) -> None:
        """Record one non-empty single-line repair diagnostic once.

        Args:
            value: Candidate value.
        """

        if not value or "\n" in value or "\r" in value:
            raise ValueError("repair diagnostic must be non-empty single-line text")
        if value not in self._item_set:
            self._item_set.add(value)
            self._item_list.append(value)

    def payload_get(self) -> list[str]:
        """Return repairs in deterministic execution order.

        Returns:
            The repairs in deterministic execution order.
        """

        return list(self._item_list)

    def reset(self) -> None:
        """Start one independent command report."""

        self._item_list.clear()
        self._item_set.clear()
