"""Tests for the canonical agent plugin marketplace metadata."""

import json
from pathlib import Path

MARKETPLACE_PATH = Path(__file__).resolve().parents[1] / ".agents" / "plugins" / "marketplace.json"


def test_marketplace_uses_canonical_identity() -> None:
    """Keep the marketplace identity aligned with the repository."""

    marketplace = json.loads(MARKETPLACE_PATH.read_text(encoding="utf-8"))

    assert marketplace["name"] == "agent-plugins"


def test_marketplace_exposes_only_canonical_plugins() -> None:
    """Keep the marketplace split free of compatibility plugin identities."""

    marketplace = json.loads(MARKETPLACE_PATH.read_text(encoding="utf-8"))

    assert [plugin["name"] for plugin in marketplace["plugins"]] == [
        "agent-workflows",
        "linear-agent-tools",
        "marketplace-agent-tools",
        "workflow-container-agent-tools",
    ]
