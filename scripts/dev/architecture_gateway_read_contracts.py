#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Architecture checks for semantic gateway read boundaries."""

from __future__ import annotations

import ast
from pathlib import Path

SEMANTIC_READ_FUNCTIONS = {
    "venus_evcharger/inputs/helper/sources_pv_grid.py": ("pv_power", "grid_power"),
    "venus_evcharger/inputs/helper/sources.py": ("battery_snapshot",),
    "venus_evcharger/inputs/pv.py": ("get_pv_power",),
    "venus_evcharger/inputs/storage.py": ("get_grid_power", "get_battery_soc"),
}

SEMANTIC_READ_FORBIDDEN_MARKERS = (
    "_get_dbus_value(",
    "get_dbus_value(",
    "list_dbus_services(",
    "resolve_auto_pv_services(",
    "resolve_auto_battery_service(",
    "dbus_path_key(",
    "auto_pv_path",
    "auto_dc_pv_path",
    "auto_grid_l1_path",
    "auto_grid_l2_path",
    "auto_grid_l3_path",
    "auto_battery_soc_path",
)

SEMANTIC_READ_REQUIRED_MARKERS = {
    ("venus_evcharger/inputs/helper/sources_pv_grid.py", "pv_power"): (
        "PV_POWER_READ_KEY",
        ".semantic_value(",
    ),
    ("venus_evcharger/inputs/helper/sources_pv_grid.py", "grid_power"): (
        "GRID_POWER_READ_KEY",
        ".semantic_value(",
    ),
    ("venus_evcharger/inputs/helper/sources.py", "battery_snapshot"): (
        "BATTERY_SOC_READ_KEY",
        ".semantic_value(",
    ),
    ("venus_evcharger/inputs/pv.py", "get_pv_power"): (
        "PV_POWER_READ_KEY",
        "read_semantic_value(",
    ),
    ("venus_evcharger/inputs/storage.py", "get_grid_power"): (
        "GRID_POWER_READ_KEY",
        "read_semantic_value(",
    ),
    ("venus_evcharger/inputs/storage.py", "get_battery_soc"): (
        "BATTERY_SOC_READ_KEY",
        "read_semantic_value(",
    ),
}


def check_gateway_read_contracts(repo: Path) -> list[str]:
    """Return violations for semantic read functions and client refresh APIs."""
    return [
        *_semantic_read_contract_failures(repo),
        *_gateway_client_read_contract_failures(repo),
    ]


def _semantic_read_contract_failures(repo: Path) -> list[str]:
    return [
        failure
        for relative_path, function_names in SEMANTIC_READ_FUNCTIONS.items()
        for function_name in function_names
        for failure in _semantic_read_function_failures(repo, relative_path, function_name)
    ]


def _semantic_read_function_failures(repo: Path, relative_path: str, function_name: str) -> list[str]:
    source = _function_source(repo / relative_path, function_name)
    if source is None:
        return [f"{relative_path}: semantic read function {function_name} is missing"]
    line_number, function_source = source
    return _semantic_read_marker_failures(relative_path, function_name, line_number, function_source)


def _function_source(path: Path, function_name: str) -> tuple[int, str] | None:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return node.lineno, ast.get_source_segment(text, node) or ""
    return None


def _semantic_read_marker_failures(relative_path: str, function_name: str, line_number: int, source: str) -> list[str]:
    return [
        *_forbidden_marker_failures(relative_path, function_name, line_number, source),
        *_missing_marker_failures(relative_path, function_name, line_number, source),
    ]


def _forbidden_marker_failures(relative_path: str, function_name: str, line_number: int, source: str) -> list[str]:
    return [
        f"{relative_path}:{line_number}: {function_name} leaks raw DBus read detail {marker!r}"
        for marker in SEMANTIC_READ_FORBIDDEN_MARKERS
        if marker in source
    ]


def _missing_marker_failures(relative_path: str, function_name: str, line_number: int, source: str) -> list[str]:
    return [
        f"{relative_path}:{line_number}: {function_name} is missing semantic gateway read contract marker {marker!r}"
        for marker in SEMANTIC_READ_REQUIRED_MARKERS.get((relative_path, function_name), ())
        if marker not in source
    ]


def _gateway_client_read_contract_failures(repo: Path) -> list[str]:
    roots = (repo / "venus_evcharger", repo / "tests", repo / "scripts")
    return [
        failure
        for root in roots
        for path in sorted(root.rglob("*.py"))
        for failure in _gateway_client_read_contract_failures_for_file(repo, path)
    ]


def _gateway_client_read_contract_failures_for_file(repo: Path, path: Path) -> list[str]:
    relative_path = path.relative_to(repo)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        _request_read_raw_shape_failure(relative_path, node)
        for node in ast.walk(tree)
        if _is_raw_shape_request_read_call(node)
    ]


def _is_raw_shape_request_read_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and _is_request_read_call(node) and _request_read_uses_raw_shape(node)


def _is_request_read_call(node: ast.Call) -> bool:
    return _qualified_call_name(node.func).endswith(".request_read")


def _request_read_uses_raw_shape(node: ast.Call) -> bool:
    return len(node.args) > 1 or any(keyword.arg == "path" for keyword in node.keywords)


def _request_read_raw_shape_failure(relative_path: Path, node: ast.Call) -> str:
    return (
        f"{relative_path}:{node.lineno}: request_read accepts semantic GatewayReadKey only; "
        "use request_raw_value for explicit service/path refreshes"
    )


def _qualified_call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _qualified_call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""
