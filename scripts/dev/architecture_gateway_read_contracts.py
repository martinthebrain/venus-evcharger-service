#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Architecture checks for the semantic energy-read gateway boundary."""

from __future__ import annotations

import ast
from pathlib import Path

CONSUMER_ROOTS = (
    "venus_evcharger/backend",
    "venus_evcharger/bootstrap",
    "venus_evcharger/controllers",
    "venus_evcharger/inputs",
    "venus_evcharger/core",
    "venus_evcharger/ops",
    "venus_evcharger/ports",
    "venus_evcharger/publish",
    "venus_evcharger/service",
    "venus_evcharger/runtime",
    "venus_evcharger/update",
)

CONSUMER_FILES = ("venus_evcharger_auto_input_helper.py",)

FORBIDDEN_IMPORT_PREFIXES = (
    "dbus",
    "venus_evcharger.dbus_adapter",
    "venus_evcharger.dbus_introspection",
)

FORBIDDEN_CALLS = {
    "dbus_path_key",
    "gateway_value",
    "request_introspection",
    "request_raw_value",
    "request_read_key",
    "request_value",
}

FORBIDDEN_COMMAND_KINDS = {"refresh_value", "refresh_services", "introspect"}

FORBIDDEN_DBUS_DETAILS = (
    "com.victronenergy",
    "/Dc/Pv/Power",
    "/Ac/Grid/L1/Power",
    "/Ac/Grid/L2/Power",
    "/Ac/Grid/L3/Power",
    "DbusIntrospectionRequestPath",
    "DbusIntrospectionSnapshotAge",
    "DbusIntrospectionServiceCount",
    "DbusIntrospectionUnusablePathCount",
)

RETIRED_RAW_DIAGNOSTIC_FILES = (
    "venus_evcharger/dbus_introspection.py",
    "venus_evcharger/publish/dbus_diagnostics_introspection.py",
)

REQUIRED_PRODUCTION_MARKERS = {
    "venus_evcharger/dbus_gateway_client.py": (
        "def request_energy_refresh(",
        "def load_energy_inputs(",
        "def load_energy_topology(",
    ),
    "venus_evcharger/inputs/helper/energy_gateway.py": (
        "EnergyRefreshRequest(",
        ".load_energy_inputs(",
        ".load_energy_topology(",
    ),
    "venus_evcharger/dbus_adapter/process/introspection.py": (
        "ENERGY_REFRESH_COMMAND_KIND",
        "EnergyRefreshRequest.from_command(",
    ),
    "venus_evcharger/dbus_adapter/process/config.py": (
        '"AutoPvServicePrefix"',
        '"AutoDcPvService"',
        '"AutoGridService"',
        '"AutoBatteryServicePrefix"',
    ),
    "venus_evcharger/publish/gateway_diagnostics.py": (
        "GatewayDiagnosticsReader",
        "discovered_source_count",
        "unusable_source_count",
    ),
    "venus_evcharger/service/controller_owner.py": (
        "GatewayDiagnosticsFileReader",
        "gateway_diagnostics_path",
    ),
}


def check_gateway_read_contracts(repo: Path) -> list[str]:
    """Return violations of the transport-neutral read/discovery boundary."""
    return [
        *_consumer_boundary_failures(repo),
        *_removed_public_api_failures(repo),
        *_dedicated_request_file_failures(repo),
        *_retired_raw_diagnostic_file_failures(repo),
        *_required_marker_failures(repo),
    ]


def _consumer_paths(repo: Path) -> list[Path]:
    paths = [
        path
        for relative in CONSUMER_ROOTS
        for path in sorted((repo / relative).rglob("*.py"))
    ]
    paths.extend(repo / relative for relative in CONSUMER_FILES)
    return paths


def _consumer_boundary_failures(repo: Path) -> list[str]:
    return [
        failure
        for path in _consumer_paths(repo)
        for failure in _file_boundary_failures(repo, path)
    ]


def _file_boundary_failures(repo: Path, path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    relative = path.relative_to(repo)
    return [
        *_forbidden_import_failures(relative, tree),
        *_forbidden_call_failures(relative, tree),
        *_forbidden_literal_failures(relative, tree),
    ]


def _forbidden_import_failures(relative: Path, tree: ast.AST) -> list[str]:
    return [
        f"{relative}:{line_number}: consumer imports DBus-owned module {module!r}"
        for module, line_number in _imports_with_lines(tree)
        if _is_forbidden_import(module)
    ]


def _imports_with_lines(tree: ast.AST) -> tuple[tuple[str, int], ...]:
    return tuple(
        (module, node.lineno)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for module in _imported_modules(node)
    )


def _is_forbidden_import(module: str) -> bool:
    return any(
        module == prefix or module.startswith(f"{prefix}.")
        for prefix in FORBIDDEN_IMPORT_PREFIXES
    )


def _imported_modules(node: ast.Import | ast.ImportFrom) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    return (node.module or "",)


def _forbidden_call_failures(relative: Path, tree: ast.AST) -> list[str]:
    return [
        f"{relative}:{node.lineno}: consumer calls retired/raw gateway API {name!r}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for name in (_call_leaf_name(node.func),)
        if name in FORBIDDEN_CALLS
    ]


def _call_leaf_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _forbidden_literal_failures(relative: Path, tree: ast.AST) -> list[str]:
    return [
        failure
        for value, line_number in _string_literals_with_lines(tree)
        for failure in _literal_failures(relative, line_number, value)
    ]


def _string_literals_with_lines(tree: ast.AST) -> tuple[tuple[str, int], ...]:
    return tuple(
        (node.value, node.lineno)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )


def _literal_failures(relative: Path, line_number: int, value: str) -> list[str]:
    return [
        *_raw_command_failure(relative, line_number, value),
        *_dbus_detail_failures(relative, line_number, value),
    ]


def _raw_command_failure(relative: Path, line_number: int, value: str) -> list[str]:
    if value not in FORBIDDEN_COMMAND_KINDS:
        return []
    return [f"{relative}:{line_number}: consumer embeds raw gateway command {value!r}"]


def _dbus_detail_failures(relative: Path, line_number: int, value: str) -> list[str]:
    return [
        f"{relative}:{line_number}: consumer embeds DBus detail {marker!r}"
        for marker in FORBIDDEN_DBUS_DETAILS
        if marker in value
    ]


def _removed_public_api_failures(repo: Path) -> list[str]:
    paths = (
        repo / "venus_evcharger/dbus_gateway_client.py",
        repo / "venus_evcharger/dbus_gateway.py",
        repo / "venus_evcharger/dbus_gateway_core.py",
    )
    retired = ("request_read_key", "request_raw_value", "gateway_read_value", "GatewayReadKey")
    return [
        f"{path.relative_to(repo)}: retired public read API {name!r} remains"
        for path in paths
        for name in retired
        if name in path.read_text(encoding="utf-8")
    ]


def _dedicated_request_file_failures(repo: Path) -> list[str]:
    roots = (repo / "venus_evcharger", repo / "deploy")
    marker = "DbusIntrospectionRequestPath"
    return [
        f"{path.relative_to(repo)}: dedicated introspection request file is retired"
        for path in _files_containing(roots, marker)
    ]


def _files_containing(roots: tuple[Path, ...], marker: str) -> list[Path]:
    return [
        path
        for root in roots
        for path in _files_containing_under(root, marker)
    ]


def _files_containing_under(root: Path, marker: str) -> list[Path]:
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.suffix in {".py", ".ini", ".md"}
        and marker in path.read_text(encoding="utf-8")
    ]


def _retired_raw_diagnostic_file_failures(repo: Path) -> list[str]:
    return [
        f"{relative}: raw introspection diagnostics consumer is retired"
        for relative in RETIRED_RAW_DIAGNOSTIC_FILES
        if (repo / relative).exists()
    ]


def _required_marker_failures(repo: Path) -> list[str]:
    return [
        f"{relative}: semantic gateway boundary marker {marker!r} is missing"
        for relative, markers in REQUIRED_PRODUCTION_MARKERS.items()
        for marker in markers
        if marker not in (repo / relative).read_text(encoding="utf-8")
    ]
