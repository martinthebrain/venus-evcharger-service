#!/usr/bin/env python3
"""Add English Doxygen briefs without changing deployed Python modules."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

_SPECIAL_BRIEFS = {
    "__aenter__": "Enter the asynchronous context.",
    "__aexit__": "Exit the asynchronous context.",
    "__call__": "Invoke the instance.",
    "__enter__": "Enter the runtime context.",
    "__exit__": "Exit the runtime context.",
    "__init__": "Initialize the instance.",
    "__iter__": "Return an iterator over the available values.",
    "__len__": "Return the number of available values.",
    "__new__": "Create a new instance.",
    "__next__": "Return the next available value.",
    "__repr__": "Return the developer-facing representation.",
    "__str__": "Return the human-readable representation.",
}

_ACTION_PREFIXES = frozenset(
    {
        "add",
        "apply",
        "build",
        "calculate",
        "check",
        "clear",
        "close",
        "collect",
        "compute",
        "configure",
        "connect",
        "consume",
        "convert",
        "create",
        "decode",
        "delete",
        "derive",
        "disconnect",
        "dispatch",
        "encode",
        "enqueue",
        "ensure",
        "execute",
        "format",
        "generate",
        "handle",
        "initialize",
        "load",
        "merge",
        "normalize",
        "open",
        "parse",
        "poll",
        "process",
        "publish",
        "read",
        "record",
        "refresh",
        "register",
        "remove",
        "render",
        "request",
        "reset",
        "resolve",
        "restore",
        "run",
        "save",
        "schedule",
        "send",
        "set",
        "start",
        "stop",
        "store",
        "update",
        "validate",
        "write",
    }
)
_BOOLEAN_PREFIXES = frozenset({"can", "has", "is", "needs", "should", "supports", "uses"})
_EMBEDDED_BOOLEAN_VERBS = frozenset({"needs", "supports", "uses"})
_DIRECT_RETURN_PREFIXES = frozenset({"get", "lookup", "select"})
_EMPTY_ACTION_OBJECTS = {
    "clear": "the current state",
    "close": "the resource",
    "load": "the configured value",
    "open": "the resource",
    "read": "the current value",
    "register": "the component",
    "request": "the operation",
    "reset": "the current state",
    "run": "the service operation",
    "start": "the component",
    "stop": "the component",
    "update": "the current state",
    "write": "the current value",
}
_BOOLEAN_TEMPLATES = {
    "has": "Return whether the object has {remainder}.",
    "needs": "Return whether the callable requires {remainder}.",
    "should": "Return whether {remainder} should occur.",
    "supports": "Return whether the callable supports {remainder}.",
    "uses": "Return whether the callable uses {remainder}.",
}


def readable_name(name: str) -> str:
    """Return a lower-case phrase derived from a Python identifier."""
    stripped = name.strip("_") or "operation"
    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", stripped)
    return " ".join(part for part in separated.lower().split("_") if part)


def english_brief(name: str, returns_bool: bool = False) -> str:
    """Return a concise English description for a Python callable."""
    special = _SPECIAL_BRIEFS.get(name)
    if special is not None:
        return special
    return _identifier_brief(name, returns_bool)


def _identifier_brief(name: str, returns_bool: bool) -> str:
    phrase = readable_name(name)
    words = phrase.split()
    first = words[0]
    remainder = " ".join(words[1:])
    if any(word in _EMBEDDED_BOOLEAN_VERBS for word in words[1:]):
        return f"Return whether {phrase}."
    if _uses_boolean_brief(first, returns_bool):
        return _boolean_brief(first, phrase, remainder)
    return _non_boolean_brief(first, phrase, remainder)


def _non_boolean_brief(first: str, phrase: str, remainder: str) -> str:
    """Return an action, value, or generic callable description."""
    if first in _DIRECT_RETURN_PREFIXES:
        return f"Return {remainder or 'the requested value'}."
    if first in _ACTION_PREFIXES:
        target = remainder or _EMPTY_ACTION_OBJECTS.get(first, "the operation")
        return f"{first.capitalize()} {target}."
    return f"Handle {phrase}."


def _uses_boolean_brief(first: str, returns_bool: bool) -> bool:
    return returns_bool or first in _BOOLEAN_PREFIXES


def _boolean_brief(first: str, phrase: str, remainder: str) -> str:
    if not remainder:
        return f"Return whether {phrase}."
    template = _BOOLEAN_TEMPLATES.get(first, "Return whether {remainder}.")
    return template.format(remainder=remainder)


def _returns_bool(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    annotation = node.returns
    return isinstance(annotation, ast.Name) and annotation.id == "bool"


def _documentation_line(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    brief = native_or_generated_brief(node)
    return f"## @brief {brief}\n"


def native_or_generated_brief(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Return the native summary or an identifier-derived fallback brief."""
    docstring = ast.get_docstring(node, clean=True)
    if docstring:
        return docstring.splitlines()[0].strip()
    return english_brief(node.name, returns_bool=_returns_bool(node))


def _parsed_tree(source: str, filename: str) -> ast.Module | None:
    try:
        return ast.parse(source, filename=filename)
    except SyntaxError:
        return None


def _function_nodes(
    tree: ast.Module,
) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _first_source_line(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    decorator_lines = [decorator.lineno for decorator in node.decorator_list]
    return min(decorator_lines, default=node.lineno)


def _line_indentation(lines: list[str], line_number: int) -> str:
    source_line = lines[line_number - 1] if line_number <= len(lines) else ""
    return source_line[: len(source_line) - len(source_line.lstrip())]


def _documentation_insertions(
    tree: ast.Module,
    lines: list[str],
) -> dict[int, list[str]]:
    insertions: dict[int, list[str]] = {}
    for node in _function_nodes(tree):
        first_line = _first_source_line(node)
        indentation = _line_indentation(lines, first_line)
        insertions.setdefault(first_line - 1, []).append(indentation + _documentation_line(node))
    return insertions


def _insert_documentation(
    lines: list[str],
    insertions: dict[int, list[str]],
) -> str:
    output: list[str] = []
    for index, line in enumerate(lines):
        output.extend(insertions.get(index, ()))
        output.append(line)
    output.extend(insertions.get(len(lines), ()))
    return "".join(output)


def filter_source(source: str, filename: str = "<unknown>") -> str:
    """Return source augmented with build-only Doxygen comments."""
    tree = _parsed_tree(source, filename)
    if tree is None:
        return source
    lines = source.splitlines(keepends=True)
    return _insert_documentation(lines, _documentation_insertions(tree, lines))


def main(argv: list[str]) -> int:
    """Filter the Python file passed by Doxygen and write it to stdout."""
    if len(argv) != 2:
        print("usage: doxygen_python_filter.py FILE", file=sys.stderr)
        return 2
    path = Path(argv[1])
    sys.stdout.write(filter_source(path.read_text(encoding="utf-8"), str(path)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
