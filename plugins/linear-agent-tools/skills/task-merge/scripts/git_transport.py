#!/usr/bin/env python3
"""Inspect or provision the exact provider-owned Git transport runtime."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
LIBRARY_ROOT = Path(__file__).resolve().parents[3] / "lib"
if str(LIBRARY_ROOT) not in sys.path:
    sys.path.insert(0, str(LIBRARY_ROOT))

from git_host.model import GitHubContractError
from git_host.transport_runtime import (
    git_transport_runtime_description_get,
    git_transport_runtime_get,
    git_transport_runtime_provision,
)


def _parser_get() -> argparse.ArgumentParser:
    """Build the closed Git transport runtime parser.

    Returns:
        Argument parser with inspect and provision operations.
    """

    parser = argparse.ArgumentParser(description="Inspect or provision the pinned Git merge transport runtime.")
    parser.add_argument("command", choices=("inspect", "provision"))
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one explicit host-runtime operation.

    Args:
        argv: Optional direct argument list.

    Returns:
        Zero on success or two for a rejected host/runtime contract.
    """

    args = _parser_get().parse_args(argv)
    try:
        runtime = git_transport_runtime_provision() if args.command == "provision" else git_transport_runtime_get()
        payload = git_transport_runtime_description_get(runtime)
    except GitHubContractError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
