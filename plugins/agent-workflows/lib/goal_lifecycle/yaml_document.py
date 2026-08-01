"""Strict YAML 1.2-compatible document boundary for closed lifecycle schemas."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import re
from typing import Any

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode
from yaml.tokens import AliasToken, AnchorToken, DirectiveToken, TagToken

from goal_lifecycle.error import GoalLifecycleError

_STANDARD_TAG_SET = {
    "tag:yaml.org,2002:bool",
    "tag:yaml.org,2002:float",
    "tag:yaml.org,2002:int",
    "tag:yaml.org,2002:map",
    "tag:yaml.org,2002:null",
    "tag:yaml.org,2002:seq",
    "tag:yaml.org,2002:str",
}


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe loader that rejects duplicate and merge keys."""


for first_character, resolver_list in tuple(_UniqueKeySafeLoader.yaml_implicit_resolvers.items()):
    _UniqueKeySafeLoader.yaml_implicit_resolvers[first_character] = [
        item for item in resolver_list if item[0] != "tag:yaml.org,2002:bool"
    ]
_UniqueKeySafeLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false)$", re.IGNORECASE),
    list("tTfF"),
)


def _mapping_construct(loader: _UniqueKeySafeLoader, node: MappingNode, deep: bool = False) -> dict[object, object]:
    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key == "<<":
            raise ConstructorError(None, None, "YAML merge keys are forbidden", key_node.start_mark)
        try:
            already_present = key in result
        except TypeError as error:
            raise ConstructorError(None, None, "YAML mapping key must be scalar", key_node.start_mark) from error
        if already_present:
            raise ConstructorError(None, None, f"duplicate YAML key: {key!r}", key_node.start_mark)
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _mapping_construct,
)


def _node_validate(node: Node, *, seen_identity_set: set[int]) -> None:
    if node.tag not in _STANDARD_TAG_SET:
        raise GoalLifecycleError(f"YAML custom tag is forbidden: {node.tag}")
    identity = id(node)
    if identity in seen_identity_set:
        raise GoalLifecycleError("YAML aliases are forbidden")
    seen_identity_set.add(identity)
    if isinstance(node, MappingNode):
        for key_node, value_node in node.value:
            _node_validate(key_node, seen_identity_set=seen_identity_set)
            _node_validate(value_node, seen_identity_set=seen_identity_set)
    elif isinstance(node, SequenceNode):
        for child in node.value:
            _node_validate(child, seen_identity_set=seen_identity_set)
    elif not isinstance(node, ScalarNode):
        raise GoalLifecycleError("YAML contains an unsupported node")


def yaml_document_load(path: Path) -> object:
    """Read one strict UTF-8 YAML document from a physical ordinary file."""

    if path.suffix != ".yaml":
        raise GoalLifecycleError(f"Machine-readable lifecycle file must use .yaml: {path}")
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise GoalLifecycleError(f"YAML owner must be one physical ordinary file: {path}")
    try:
        payload_bytes = path.read_bytes()
        payload_text = payload_bytes.decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise GoalLifecycleError(f"YAML document is unavailable or not UTF-8: {path}") from error
    if "\x00" in payload_text:
        raise GoalLifecycleError(f"YAML document contains NUL: {path}")
    try:
        token_list = list(yaml.scan(payload_text))
        if any(isinstance(token, (AliasToken, AnchorToken, DirectiveToken, TagToken)) for token in token_list):
            raise GoalLifecycleError(f"YAML aliases, anchors, directives, and tags are forbidden: {path}")
        node_list = list(yaml.compose_all(payload_text, Loader=_UniqueKeySafeLoader))
        if len(node_list) != 1 or node_list[0] is None:
            raise GoalLifecycleError(f"YAML owner must contain exactly one non-empty document: {path}")
        _node_validate(node_list[0], seen_identity_set=set())
        payload_list = list(yaml.load_all(payload_text, Loader=_UniqueKeySafeLoader))
    except (ConstructorError, yaml.YAMLError) as error:
        raise GoalLifecycleError(f"YAML document is malformed: {path}: {error}") from error
    if len(payload_list) != 1:
        raise GoalLifecycleError(f"YAML owner must contain exactly one document: {path}")
    return payload_list[0]


def yaml_document_bytes_get(payload: Mapping[str, Any]) -> bytes:
    """Serialize one canonical mapping as UTF-8 YAML with stable key order."""

    return yaml.safe_dump(
        dict(payload),
        allow_unicode=True,
        default_flow_style=False,
        explicit_end=False,
        sort_keys=False,
    ).encode("utf-8")
