#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Validate explicit suppression policy across project Python sources."""

from __future__ import annotations

import ast
import re
import tokenize
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

_NOQA_COMMENT = re.compile(
    r"^#\s*noqa\s*:\s*([A-Z]\d{3}(?:\s*,\s*[A-Z]\d{3})*)"
    r"(?:\s*-\s*\S.*)?$"
)
_NOQA_MARKER = re.compile(r"^#\s*noqa\b", re.IGNORECASE)
_TYPE_IGNORE_MARKER = re.compile(r"\btype\s*:\s*ignore\b", re.IGNORECASE)
_NO_COVER_MARKER = re.compile(r"\bpragma\s*:\s*no\s+cover\b", re.IGNORECASE)
_NO_MUTATE_MARKER = re.compile(r"\bpragma\s*:\s*no\s+mutate\b", re.IGNORECASE)
_NO_BRANCH_MARKER = re.compile(r"\bpragma\s*:\s*no\s+branch\b", re.IGNORECASE)
_FUNCTION_NAME = re.compile(r"\bdef\s+([A-Za-z_]\w*)\s*\(")
_SUPPORT_STAR_IMPORT = re.compile(
    r"^\s*from\s+tests\.[A-Za-z_]\w*_support\s+import\s+\*\s+#\s*noqa\b"
)

_IMPORT_ORDER_NOQA_PATH = "tests/venus_evcharger_helpers_support.py"
_API_NOQA_METHODS = {
    "scripts/dev/check_dbus_isolation.py": frozenset(
        {"visit_Import", "visit_ImportFrom", "visit_Call", "visit_List", "visit_Tuple", "visit_Name"}
    ),
    "scripts/dev/mock_shelly_rpc.py": frozenset({"do_GET"}),
    "venus_evcharger/control/http_api.py": frozenset({"do_GET", "do_POST"}),
    "venus_evcharger/inputs/helper/glib_runtime.py": frozenset({"MainLoop"}),
    "tests/test_auto_input_helper_glib_runtime_contracts.py": frozenset({"MainLoop"}),
    "tests/test_gateway_semantic_operations.py": frozenset({"GetValue", "SetValue"}),
    "tests/test_generic_shelly_gateway_configuration.py": frozenset(
        {"GetValue", "SetValue", "Introspect"}
    ),
    "tests/support/dbus_gateway_adapter_harness.py": frozenset(
        {"GetValue", "SetValue", "ListNames", "Introspect"}
    ),
}
_SECURITY_NOQA_MARKERS = {
    ("scripts/dev/pi_gateway_release_gate_shelly.py", "S310"): "urlopen(",
    ("tests/test_mock_shelly_rpc.py", "S310"): "urlopen(",
    ("venus_evcharger/ops/forensic_observer_probe.py", "S310"): "urlopen(",
}
_DOCUMENTED_NO_COVER_CALLABLES = {
    "venus_evcharger/dbus_adapter/process/loop.py": {
        "DbusAdapterLoop.run": (
            "Blocking Venus DBus/GLib process loop; exercised by the Pi release gate."
        ),
    },
}


@dataclass(frozen=True)
class _SourceContext:
    relative_path: str
    no_cover_lines: frozenset[int]


SuppressionChecker = Callable[[_SourceContext, int, str, str], str | None]
FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


def _suppression_scan_paths(repo: Path) -> list[Path]:
    roots = (repo / "venus_evcharger", repo / "tests", repo / "scripts")
    paths = [path for root in roots for path in root.rglob("*.py")]
    paths.extend(repo.glob("*.py"))
    return sorted(set(paths))


def _base_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _base_name(node.value)
    return ""


def _is_protocol_class(node: ast.ClassDef) -> bool:
    return any(_base_name(base) == "Protocol" for base in node.bases)


def _is_ellipsis_body(node: FunctionNode) -> bool:
    if len(node.body) != 1 or not isinstance(node.body[0], ast.Expr):
        return False
    value = node.body[0].value
    return isinstance(value, ast.Constant) and value.value is Ellipsis


def _line_range(node: ast.stmt) -> range:
    start = node.lineno
    return range(start, (node.end_lineno or start) + 1)


def _protocol_no_cover_lines(node: ast.ClassDef) -> set[int]:
    lines = {node.lineno}
    methods = (
        item
        for item in node.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    for method in methods:
        if _is_ellipsis_body(method):
            lines.update(_line_range(method))
    return lines


def _is_type_checking_test(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "TYPE_CHECKING"
    return isinstance(node, ast.Attribute) and node.attr == "TYPE_CHECKING"


def _type_checking_import_lines(node: ast.If) -> set[int]:
    imports = (item for item in node.body if isinstance(item, (ast.Import, ast.ImportFrom)))
    return {line for item in imports for line in _line_range(item)}


def _single_comparison(node: ast.Compare) -> tuple[ast.cmpop, ast.expr] | None:
    if len(node.ops) != 1:
        return None
    return node.ops[0], node.comparators[0]


def _is_dunder_name(node: ast.expr) -> bool:
    return isinstance(node, ast.Name) and node.id == "__name__"


def _is_main_literal(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value == "__main__"


def _is_main_comparison(node: ast.Compare) -> bool:
    comparison = _single_comparison(node)
    if comparison is None:
        return False
    operation, right = comparison
    return all(
        (
            _is_dunder_name(node.left),
            isinstance(operation, ast.Eq),
            _is_main_literal(right),
        )
    )


def _documented_exceptions(relative_path: str) -> dict[str, str]:
    documented = _DOCUMENTED_NO_COVER_CALLABLES.get(relative_path, {})
    if not all(reason.strip() for reason in documented.values()):
        return {}
    return documented


def _top_level_classes(tree: ast.Module) -> list[ast.ClassDef]:
    return [node for node in tree.body if isinstance(node, ast.ClassDef)]


def _class_documented_callable_lines(
    node: ast.ClassDef,
    documented: dict[str, str],
) -> set[int]:
    return {
        item.lineno
        for item in node.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        and f"{node.name}.{item.name}" in documented
    }


def _documented_callable_lines(relative_path: str, tree: ast.Module) -> set[int]:
    documented = _documented_exceptions(relative_path)
    if not documented:
        return set()
    lines: set[int] = set()
    for class_node in _top_level_classes(tree):
        lines.update(_class_documented_callable_lines(class_node, documented))
    return lines


def _protocol_lines(tree: ast.Module) -> set[int]:
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and _is_protocol_class(node):
            lines.update(_protocol_no_cover_lines(node))
    return lines


def _type_checking_lines(tree: ast.Module) -> set[int]:
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_type_checking_test(node.test):
            lines.update(_type_checking_import_lines(node))
    return lines


def _is_main_guard(node: ast.If) -> bool:
    return isinstance(node.test, ast.Compare) and _is_main_comparison(node.test)


def _main_guard_lines(tree: ast.Module) -> set[int]:
    return {
        node.lineno
        for node in tree.body
        if isinstance(node, ast.If) and _is_main_guard(node)
    }


def _parse_module(source: str) -> ast.Module | None:
    try:
        return ast.parse(source)
    except SyntaxError:
        return None


def _allowed_no_cover_lines(relative_path: str, source: str) -> frozenset[int]:
    tree = _parse_module(source)
    if tree is None:
        return frozenset()
    lines = _protocol_lines(tree)
    lines.update(_type_checking_lines(tree))
    lines.update(_main_guard_lines(tree))
    lines.update(_documented_callable_lines(relative_path, tree))
    return frozenset(lines)


def _source_context(relative_path: str, source: str) -> _SourceContext:
    return _SourceContext(
        relative_path=relative_path,
        no_cover_lines=_allowed_no_cover_lines(relative_path, source),
    )


def _noqa_codes(comment: str) -> frozenset[str]:
    match = _NOQA_COMMENT.fullmatch(comment)
    if match is None:
        return frozenset()
    return frozenset(code.strip() for code in match.group(1).split(","))


def _method_name(line: str) -> str:
    match = _FUNCTION_NAME.search(line)
    return match.group(1) if match is not None else ""


def _allowed_import_noqa(relative_path: str, codes: frozenset[str], line: str) -> bool:
    if codes == frozenset({"E402"}):
        stripped = line.lstrip()
        return relative_path == _IMPORT_ORDER_NOQA_PATH and stripped.startswith(("import ", "from "))
    return (
        codes == frozenset({"F401", "F403"})
        and relative_path.startswith("tests/")
        and _SUPPORT_STAR_IMPORT.search(line) is not None
    )


def _allowed_api_noqa(relative_path: str, codes: frozenset[str], line: str) -> bool:
    allowed_methods = _API_NOQA_METHODS.get(relative_path, frozenset())
    return codes == frozenset({"N802"}) and _method_name(line) in allowed_methods


def _allowed_security_noqa(relative_path: str, codes: frozenset[str], line: str) -> bool:
    if len(codes) != 1:
        return False
    code = next(iter(codes))
    marker = _SECURITY_NOQA_MARKERS.get((relative_path, code))
    return marker is not None and marker in line


def _line_allowed_noqa(relative_path: str, comment: str, line: str) -> bool:
    codes = _noqa_codes(comment)
    return any(
        checker(relative_path, codes, line)
        for checker in (_allowed_import_noqa, _allowed_api_noqa, _allowed_security_noqa)
    )


def _type_ignore_failure(
    context: _SourceContext,
    line_number: int,
    comment: str,
    _line: str,
) -> str | None:
    if _TYPE_IGNORE_MARKER.search(comment):
        return f"{context.relative_path}:{line_number}: type ignore suppressions are forbidden"
    return None


def _noqa_failure(
    context: _SourceContext,
    line_number: int,
    comment: str,
    line: str,
) -> str | None:
    if _NOQA_MARKER.search(comment) and not _line_allowed_noqa(context.relative_path, comment, line):
        return f"{context.relative_path}:{line_number}: unapproved noqa suppression: {line.strip()}"
    return None


def _no_cover_failure(
    context: _SourceContext,
    line_number: int,
    comment: str,
    line: str,
) -> str | None:
    if _NO_COVER_MARKER.search(comment) and line_number not in context.no_cover_lines:
        return f"{context.relative_path}:{line_number}: unapproved coverage suppression: {line.strip()}"
    return None


def _no_mutate_failure(
    context: _SourceContext,
    line_number: int,
    comment: str,
    _line: str,
) -> str | None:
    if _NO_MUTATE_MARKER.search(comment):
        return f"{context.relative_path}:{line_number}: mutation suppressions are forbidden"
    return None


def _no_branch_failure(
    context: _SourceContext,
    line_number: int,
    comment: str,
    _line: str,
) -> str | None:
    if _NO_BRANCH_MARKER.search(comment):
        return f"{context.relative_path}:{line_number}: branch coverage suppressions are forbidden"
    return None


def _suppression_failure(
    context: _SourceContext,
    line_number: int,
    comment: str,
    line: str,
) -> str | None:
    checkers: tuple[SuppressionChecker, ...] = (
        _type_ignore_failure,
        _noqa_failure,
        _no_cover_failure,
        _no_mutate_failure,
        _no_branch_failure,
    )
    for checker in checkers:
        failure = checker(context, line_number, comment, line)
        if failure is not None:
            return failure
    return None


def _comment_tokens(source: str) -> Iterable[tokenize.TokenInfo]:
    return (
        token
        for token in tokenize.generate_tokens(StringIO(source).readline)
        if token.type == tokenize.COMMENT
    )


def _source_failures(relative_path: str, source: str) -> list[str]:
    context = _source_context(relative_path, source)
    lines = source.splitlines()
    failures: list[str] = []
    for token in _comment_tokens(source):
        line_number = token.start[0]
        failure = _suppression_failure(
            context,
            line_number,
            token.string,
            lines[line_number - 1],
        )
        if failure is not None:
            failures.append(failure)
    return failures


def check_suppression_contracts(repo: Path) -> list[str]:
    """Return every forbidden Python suppression below ``repo``."""
    failures: list[str] = []
    for path in _suppression_scan_paths(repo):
        relative_path = path.relative_to(repo).as_posix()
        source = path.read_text(encoding="utf-8")
        failures.extend(_source_failures(relative_path, source))
    return failures
