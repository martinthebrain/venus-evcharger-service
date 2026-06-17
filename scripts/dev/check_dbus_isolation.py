#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fail when production code touches Victron DBus outside the gateway adapter."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ADAPTER = REPO / "venus_evcharger_dbus_adapter.py"
ADAPTER_FILES = {
    ADAPTER,
    REPO / "venus_evcharger" / "dbus_adapter_process.py",
    REPO / "venus_evcharger" / "dbus_adapter_process_health.py",
    REPO / "venus_evcharger" / "dbus_adapter_process_introspection.py",
    REPO / "venus_evcharger" / "dbus_adapter_process_introspection_snapshot.py",
    REPO / "venus_evcharger" / "dbus_adapter_process_io.py",
    REPO / "venus_evcharger" / "dbus_adapter_process_loop.py",
    REPO / "venus_evcharger" / "dbus_adapter_process_runtime.py",
    REPO / "venus_evcharger" / "dbus_adapter_components.py",
    REPO / "venus_evcharger" / "dbus_adapter_components_rate.py",
    REPO / "venus_evcharger" / "dbus_adapter_components_resource.py",
    REPO / "venus_evcharger" / "dbus_adapter_components_scheduler.py",
    REPO / "venus_evcharger" / "dbus_adapter_read.py",
    REPO / "venus_evcharger" / "dbus_adapter_write.py",
    REPO / "venus_evcharger" / "dbus_adapter_write_health.py",
}
ROOT_FILES = (
    "venus_evcharger_service.py",
    "venus_evcharger_auto_input_helper.py",
    "venus_evcharger_observer.py",
    "venus_evchargerctl.py",
)
PRODUCTION_ROOTS = ("venus_evcharger", "scripts")
FORBIDDEN_IMPORT_ROOTS = {"dbus", "vedbus"}
FORBIDDEN_CALLS = {
    "SystemBus",
    "SessionBus",
    "Interface",
    "get_object",
    "GetValue",
    "SetValue",
    "Introspect",
    "add_signal_receiver",
}
FORBIDDEN_NAMES = {"VeDbusService"}


def _production_files() -> list[Path]:
    files = _root_production_files() + _package_production_files()
    return sorted(path for path in files if path not in ADAPTER_FILES and "__pycache__" not in path.parts)


def _root_production_files() -> list[Path]:
    files: list[Path] = []
    for relative in ROOT_FILES:
        path = REPO / relative
        if path.exists():
            files.append(path)
    return files


def _package_production_files() -> list[Path]:
    files: list[Path] = []
    for root in PRODUCTION_ROOTS:
        files.extend((REPO / root).rglob("*.py"))
    return files


def _absolute_import_forbidden(module: str) -> bool:
    root = module.split(".", 1)[0]
    return root in FORBIDDEN_IMPORT_ROOTS


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


class DbusIsolationVisitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.violations: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            if _absolute_import_forbidden(alias.name):
                self._add(node, f"forbidden import {alias.name}")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        module = node.module or ""
        if node.level == 0 and _absolute_import_forbidden(module):
            self._add(node, f"forbidden import from {module}")

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        name = _call_name(node.func)
        if name in FORBIDDEN_CALLS:
            self._add(node, f"forbidden DBus call {name}")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if node.id in FORBIDDEN_NAMES:
            self._add(node, f"forbidden DBus symbol {node.id}")

    def _add(self, node: ast.AST, message: str) -> None:
        relative = self.path.relative_to(REPO)
        self.violations.append(f"{relative}:{getattr(node, 'lineno', 0)}: {message}")


def _violations_for(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as error:
        return [f"{path.relative_to(REPO)}:{error.lineno or 0}: unable to parse: {error.msg}"]
    visitor = DbusIsolationVisitor(path)
    visitor.visit(tree)
    return visitor.violations


def main() -> int:
    violations: list[str] = []
    for path in _production_files():
        violations.extend(_violations_for(path))
    if violations:
        print("Direct DBus access is only allowed in venus_evcharger_dbus_adapter.py.", file=sys.stderr)
        for violation in violations:
            print(violation, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
