"""Shared directed-graph traversal for complete imports and approved deltas."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar

RoleValue = TypeVar("RoleValue")


@dataclass(frozen=True, slots=True)
class RolePathState:
    """Identify one node and matched-role position during graph traversal."""

    node_key: str
    role_index: int


def cycle_node_key_get(downstream_node_key_set_by_blocker_key_map: dict[str, set[str]]) -> str:
    """Return one deterministic node participating in a directed cycle, or empty."""

    visiting_node_key_set: set[str] = set()
    visited_node_key_set: set[str] = set()

    def visit(node_key: str) -> str:
        if node_key in visiting_node_key_set:
            return node_key
        if node_key in visited_node_key_set:
            return ""
        visiting_node_key_set.add(node_key)
        for downstream_node_key in sorted(downstream_node_key_set_by_blocker_key_map[node_key]):
            cycle_node_key = visit(downstream_node_key)
            if cycle_node_key:
                return cycle_node_key
        visiting_node_key_set.remove(node_key)
        visited_node_key_set.add(node_key)
        return ""

    for node_key in sorted(downstream_node_key_set_by_blocker_key_map):
        cycle_node_key = visit(node_key)
        if cycle_node_key:
            return cycle_node_key
    return ""


def exist_ordered_role_path(
    start_node_key: str,
    expected_role_list: list[RoleValue],
    *,
    downstream_node_key_set_by_blocker_key_map: dict[str, set[str]],
    role_by_node_key_map: dict[str, RoleValue],
) -> bool:
    """Return whether one downstream path encounters every required role in order."""

    frontier_state_list = [RolePathState(node_key=start_node_key, role_index=0)]
    visited_state_set: set[RolePathState] = set()
    while frontier_state_list:
        state = frontier_state_list.pop()
        if state in visited_state_set:
            continue
        visited_state_set.add(state)
        for downstream_node_key in downstream_node_key_set_by_blocker_key_map[state.node_key]:
            next_role_index = state.role_index
            if role_by_node_key_map[downstream_node_key] is expected_role_list[state.role_index]:
                next_role_index += 1
                if next_role_index == len(expected_role_list):
                    return True
            frontier_state_list.append(RolePathState(node_key=downstream_node_key, role_index=next_role_index))
    return False


def exist_path(
    start_node_key: str,
    target_node_key: str,
    *,
    downstream_node_key_set_by_blocker_key_map: dict[str, set[str]],
) -> bool:
    """Return whether one blocker path reaches an exact downstream task."""

    frontier_node_key_list = [start_node_key]
    visited_node_key_set: set[str] = set()
    while frontier_node_key_list:
        node_key = frontier_node_key_list.pop()
        if node_key in visited_node_key_set:
            continue
        visited_node_key_set.add(node_key)
        for downstream_node_key in downstream_node_key_set_by_blocker_key_map[node_key]:
            if downstream_node_key == target_node_key:
                return True
            frontier_node_key_list.append(downstream_node_key)
    return False
