#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Keep GX relay and ESS code behind semantic gateway operations."""

from __future__ import annotations

import ast
from pathlib import Path

_TARGET_DIRECTORIES = (
    "venus_evcharger/backend",
    "venus_evcharger/update",
)

_FORBIDDEN_NAMES = {
    "DbusCacheStore",
    "GatewayClient",
    "dbus_path_key",
    "gateway_paths",
    "request_raw_value",
    "set_remote_value",
}

_FORBIDDEN_COMMAND_KINDS = {"set_value", "refresh_value"}
_FORBIDDEN_ATTRIBUTE_SUFFIXES = {
    "auto_battery_discharge_balance_victron_bias_path",
    "auto_battery_discharge_balance_victron_bias_service",
}


def check_gateway_operation_contracts(repo: Path) -> list[str]:
    """Return raw DBus/gateway violations in backend and update code."""
    failures: list[str] = []
    for directory in _TARGET_DIRECTORIES:
        for path in sorted((repo / directory).rglob("*.py")):
            relative_path = str(path.relative_to(repo))
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            failures.extend(_tree_failures(relative_path, tree))
    return failures


def _tree_failures(relative_path: str, tree: ast.AST) -> list[str]:
    failures: list[str] = []
    for node in ast.walk(tree):
        failure = _node_failure(relative_path, node)
        if failure is not None:
            failures.append(failure)
    return failures


def _node_failure(relative_path: str, node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return _name_failure(relative_path, node)
    if isinstance(node, ast.Attribute):
        return _attribute_failure(relative_path, node)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return _string_failure(relative_path, node, node.value)
    return None


def _name_failure(relative_path: str, node: ast.Name) -> str | None:
    return _failure(relative_path, node, f"raw gateway symbol {node.id!r}") if node.id in _FORBIDDEN_NAMES else None


def _attribute_failure(relative_path: str, node: ast.Attribute) -> str | None:
    if node.attr in _FORBIDDEN_NAMES:
        return _failure(relative_path, node, f"raw gateway call {node.attr!r}")
    if node.attr in _FORBIDDEN_ATTRIBUTE_SUFFIXES:
        return _failure(relative_path, node, f"adapter target attribute {node.attr!r}")
    return None


def _string_failure(relative_path: str, node: ast.Constant, value: str) -> str | None:
    if value in _FORBIDDEN_COMMAND_KINDS:
        return _failure(relative_path, node, f"raw command kind {value!r}")
    if value.startswith("com.victronenergy."):
        return _failure(relative_path, node, f"DBus service name {value!r}")
    if value.startswith(("/Settings/", "/Relay/")):
        return _failure(relative_path, node, f"DBus path {value!r}")
    return None


def _failure(relative_path: str, node: ast.AST, detail: str) -> str:
    return f"{relative_path}:{getattr(node, 'lineno', 0)}: semantic gateway boundary contains {detail}"


if __name__ == "__main__":
    repository = Path(__file__).resolve().parents[2]
    violations = check_gateway_operation_contracts(repository)
    for violation in violations:
        print(violation)
    raise SystemExit(bool(violations))
