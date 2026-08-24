#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fail when production code touches Victron DBus outside the gateway adapter."""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ADAPTER = REPO / "venus_evcharger_dbus_adapter.py"
ADAPTER_PACKAGE = REPO / "venus_evcharger" / "dbus_adapter"
CONNECTION_MANAGER = ADAPTER_PACKAGE / "connection.py"
DBUS_ERROR_CLASSIFIER = ADAPTER_PACKAGE / "dbus_errors.py"
PUBLICATION_REGISTRY = ADAPTER_PACKAGE / "publication" / "registry.py"
PROCESS_LOOP = ADAPTER_PACKAGE / "process" / "loop.py"
ROOT_FILES = (
    "venus_evcharger_service.py",
    "venus_evcharger_auto_input_helper.py",
    "venus_evchargerctl.py",
)
PRODUCTION_ROOTS = ("venus_evcharger", "scripts")
SHELL_ROOTS = ("scripts", "deploy")
DOCUMENTATION_ROOTS = ("docs", "examples")
RUST_OBSERVER_ROOT = REPO / "rust" / "forensic-observer"
FORBIDDEN_IMPORT_ROOTS = {"dbus", "vedbus"}
FORBIDDEN_CALLS = {
    "SystemBus",
    "SessionBus",
    "Interface",
    "get_object",
    "call_async",
    "GetValue",
    "SetValue",
    "ListNames",
    "Introspect",
    "add_signal_receiver",
}
FORBIDDEN_NAMES = {"VeDbusService"}
GATEWAY_IMPORT_OWNERS = {
    "dbus": frozenset({CONNECTION_MANAGER, DBUS_ERROR_CLASSIFIER}),
    "dbus.mainloop.glib": frozenset({PROCESS_LOOP}),
    "vedbus": frozenset({PUBLICATION_REGISTRY}),
}
GATEWAY_CALL_OWNERS = {
    "SystemBus": frozenset({CONNECTION_MANAGER}),
    "SessionBus": frozenset(),
    "Interface": frozenset(),
    "get_object": frozenset(),
    "call_async": frozenset({CONNECTION_MANAGER}),
    "GetValue": frozenset(),
    "SetValue": frozenset(),
    "ListNames": frozenset(),
    "Introspect": frozenset(),
    "add_signal_receiver": frozenset(),
    "VeDbusService": frozenset({PUBLICATION_REGISTRY}),
}
FORBIDDEN_CLI_EXECUTABLES = {"dbus", "dbus-send", "gdbus", "busctl", "dbus-monitor"}
MIN_COMMAND_ITEMS = 2
FORBIDDEN_CLI_SUBCOMMANDS = {
    "dbus": {"-y", "-s", "--system", "--session"},
    "dbus-send": {"--system", "--session"},
    "gdbus": {"call", "introspect", "monitor", "wait", "emit"},
    "busctl": {"call", "get-property", "set-property", "introspect", "list", "monitor"},
}
FORBIDDEN_CLI_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.-])(?:"
    r"dbus\s+-(?:y|s)\b|"
    r"dbus-send\s+(?:--system|--session|--)|"
    r"gdbus\s+(?:call|introspect|monitor|wait|emit)\b|"
    r"busctl\s+(?:call|get-property|set-property|introspect|list|monitor)\b|"
    r"dbus-monitor(?:\s|$)"
    r")",
)
RUST_DBUS_PATTERN = re.compile(
    r"(?:\b(?:dbus|zbus|vedbus)::|"
    r"\b(?:extern\s+crate|use)\s+(?:dbus|zbus|vedbus)\b|"
    r"Command::new\(\s*\"(?:dbus|dbus-send|gdbus|busctl|dbus-monitor)\"|"
    r"^\s*(?:dbus|zbus|vedbus)\s*=)",
)


def _production_files() -> list[Path]:
    files = _root_production_files() + _package_production_files()
    return sorted(path for path in files if not _adapter_owned(path) and "__pycache__" not in path.parts)


def _gateway_files() -> list[Path]:
    files = [ADAPTER]
    files.extend(ADAPTER_PACKAGE.rglob("*.py"))
    return sorted(path for path in files if path.is_file() and "__pycache__" not in path.parts)


def _adapter_owned(path: Path) -> bool:
    return path == ADAPTER or ADAPTER_PACKAGE in path.parents


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


def _scanned_text_files() -> list[Path]:
    files = _direct_matching_files(REPO, ("*.sh", "*.md"))
    files.extend(_matching_roots(SHELL_ROOTS, ("*.sh", "*.md")))
    files.extend((REPO / "deploy").rglob("run"))
    files.extend(_matching_roots(DOCUMENTATION_ROOTS, ("*.md",)))
    return sorted({path for path in files if path.is_file() and "__pycache__" not in path.parts})


def _matching_roots(roots: tuple[str, ...], patterns: tuple[str, ...]) -> list[Path]:
    return [path for root in roots for path in _recursive_matching_files(REPO / root, patterns)]


def _direct_matching_files(root: Path, patterns: tuple[str, ...]) -> list[Path]:
    return [path for pattern in patterns for path in root.glob(pattern)]


def _recursive_matching_files(root: Path, patterns: tuple[str, ...]) -> list[Path]:
    return [path for pattern in patterns for path in root.rglob(pattern)]


def _absolute_import_forbidden(module: str) -> bool:
    root, _separator, _remainder = module.partition(".")
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
        if node.level != 0 or node.module is None:
            return
        module = node.module
        if _absolute_import_forbidden(module):
            self._add(node, f"forbidden import from {module}")

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        name = _call_name(node.func)
        if name in FORBIDDEN_CALLS:
            self._add(node, f"forbidden DBus call {name}")
        if name in {"which", "find_executable"} and node.args and _forbidden_cli_literal(node.args[0]):
            self._add(node, "forbidden DBus CLI lookup")
        self.generic_visit(node)

    def visit_List(self, node: ast.List) -> None:  # noqa: N802
        self._check_command_sequence(node, node.elts)
        self.generic_visit(node)

    def visit_Tuple(self, node: ast.Tuple) -> None:  # noqa: N802
        self._check_command_sequence(node, node.elts)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if node.id in FORBIDDEN_NAMES:
            self._add(node, f"forbidden DBus symbol {node.id}")

    def _add(self, node: ast.AST, message: str) -> None:
        self.violations.append(_violation(self.path, node, message))

    def _check_command_sequence(self, node: ast.AST, items: list[ast.expr]) -> None:
        if _forbidden_cli_sequence(items):
            self._add(node, "forbidden DBus CLI command")


class DbusGatewayOwnershipVisitor(ast.NodeVisitor):
    """Enforce one explicit owner for every gateway DBus transport primitive."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.violations: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            self._check_import(node, alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        if node.level != 0 or node.module is None:
            return
        self._check_import(node, node.module)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        name = _call_name(node.func)
        owners = GATEWAY_CALL_OWNERS.get(name)
        if owners is not None and self.path not in owners:
            self._add(node, f"gateway DBus call {name} belongs to another module")
        self.generic_visit(node)

    def _check_import(self, node: ast.AST, module: str) -> None:
        owners = GATEWAY_IMPORT_OWNERS.get(module)
        if owners is not None and self.path not in owners:
            self._add(node, f"gateway DBus import {module} belongs to another module")

    def _add(self, node: ast.AST, message: str) -> None:
        self.violations.append(_violation(self.path, node, message))


def _violation(path: Path, node: ast.AST, message: str) -> str:
    relative = path.relative_to(REPO)
    line_number = getattr(node, "lineno", 0)
    return f"{relative}:{line_number}: {message}"


def _forbidden_cli_literal(node: ast.AST) -> bool:
    return _cli_literal_name(node) in FORBIDDEN_CLI_EXECUTABLES


def _cli_literal_name(node: ast.AST) -> str:
    if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
        return ""
    return Path(node.value).name


def _forbidden_cli_sequence(items: list[ast.expr]) -> bool:
    if not items:
        return False
    executable = _cli_literal_name(items[0])
    return _forbidden_cli_command(executable, _command_argument(items))


def _forbidden_cli_command(executable: str, argument: str) -> bool:
    if executable not in FORBIDDEN_CLI_EXECUTABLES:
        return False
    if executable == "dbus-monitor":
        return True
    if executable == "dbus-send":
        return argument.startswith("--")
    return argument in FORBIDDEN_CLI_SUBCOMMANDS[executable]


def _command_argument(items: list[ast.expr]) -> str:
    if len(items) < MIN_COMMAND_ITEMS:
        return ""
    return _literal_text(items[1])


def _literal_text(node: ast.AST) -> str:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else ""


def _violations_for(path: Path) -> list[str]:
    try:
        tree = _parse_python(path)
    except SyntaxError as error:
        return [_syntax_violation(path, error)]
    visitor = DbusIsolationVisitor(path)
    visitor.visit(tree)
    return visitor.violations + _cli_violations(path)


def _gateway_ownership_violations(path: Path) -> list[str]:
    try:
        tree = _parse_python(path)
    except SyntaxError as error:
        return [_syntax_violation(path, error)]
    visitor = DbusGatewayOwnershipVisitor(path)
    visitor.visit(tree)
    return visitor.violations


def _cli_violations(path: Path) -> list[str]:
    if path.resolve() == Path(__file__).resolve():
        return []
    violations: list[str] = []
    for line_number, line in enumerate(_read_text(path).splitlines(), start=1):
        match = FORBIDDEN_CLI_PATTERN.search(line)
        if match is not None:
            relative = path.relative_to(REPO)
            violations.append(f"{relative}:{line_number}: forbidden DBus CLI {match.group(0)}")
    return violations


def _parse_python(path: Path) -> ast.Module:
    return ast.parse(_read_text(path))


def _read_text(path: Path) -> str:
    return path.read_bytes().decode()


def _syntax_violation(path: Path, error: SyntaxError) -> str:
    relative = path.relative_to(REPO)
    line_number = error.lineno if error.lineno is not None else 0
    return f"{relative}:{line_number}: unable to parse: {error.msg}"


def _production_violations() -> list[str]:
    return _collect_violations(_production_files(), _violations_for)


def _gateway_violations() -> list[str]:
    return _collect_violations(_gateway_files(), _gateway_ownership_violations)


def _text_violations() -> list[str]:
    return _collect_violations(_scanned_text_files(), _cli_violations)


def _rust_observer_violations() -> list[str]:
    paths = sorted((RUST_OBSERVER_ROOT / "src").rglob("*.rs"))
    paths.append(RUST_OBSERVER_ROOT / "Cargo.toml")
    violations: list[str] = []
    for path in paths:
        if not path.is_file():
            continue
        for line_number, line in enumerate(_read_text(path).splitlines(), start=1):
            if RUST_DBUS_PATTERN.search(line) is not None:
                relative = path.relative_to(REPO)
                violations.append(f"{relative}:{line_number}: Rust observer must not access DBus")
    return violations


def _collect_violations(paths: Iterable[Path], inspect: Callable[[Path], list[str]]) -> list[str]:
    return [violation for path in paths for violation in inspect(path)]


def _all_violations() -> list[str]:
    return (
        _production_violations()
        + _gateway_violations()
        + _text_violations()
        + _rust_observer_violations()
    )


def _report_violations(violations: list[str]) -> int:
    if violations:
        print("Direct DBus access is only allowed in dedicated DBus gateway adapter modules.", file=sys.stderr)
        for violation in violations:
            print(violation, file=sys.stderr)
        return 1
    return 0


def main() -> int:
    return _report_violations(_all_violations())


if __name__ == "__main__":
    raise SystemExit(main())
