#!/usr/bin/env python3
"""Generate a complete Doxygen inventory for production Python callables."""

from __future__ import annotations

import argparse
import ast
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from doxygen_python_filter import native_or_generated_brief

_ROOT_MODULES = (
    "venus_evcharger_service.py",
    "venus_evcharger_dbus_adapter.py",
    "venus_evcharger_auto_input_helper.py",
    "venus_evcharger_generic_shelly_configuration.py",
    "venus_evchargerctl.py",
)


@dataclass(frozen=True)
class FunctionRecord:
    """Describe one production function included in generated documentation."""

    path: str
    qualified_name: str
    line: int
    brief: str
    native_docstring: bool
    nested: bool


def production_python_files(repository: Path) -> list[Path]:
    """Return every deployed Python source file in deterministic order."""
    candidates = list((repository / "venus_evcharger").rglob("*.py"))
    candidates.extend(repository / name for name in _ROOT_MODULES)
    operations = repository / "scripts" / "ops"
    if operations.is_dir():
        candidates.extend(operations.rglob("*.py"))
    return sorted(path for path in candidates if path.is_file())


def _qualified_functions(
    node: ast.AST,
    parents: tuple[str, ...] = (),
    inside_function: bool = False,
) -> Iterator[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str, bool]]:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualified = ".".join((*parents, child.name))
            yield child, qualified, inside_function
            yield from _qualified_functions(child, (*parents, child.name), True)
        elif isinstance(child, ast.ClassDef):
            yield from _qualified_functions(child, (*parents, child.name), inside_function)
        else:
            yield from _qualified_functions(child, parents, inside_function)


def scan_repository(repository: Path) -> tuple[list[Path], list[FunctionRecord]]:
    """Parse production sources and return their complete function inventory."""
    files = production_python_files(repository)
    records: list[FunctionRecord] = []
    for path in files:
        relative = path.relative_to(repository).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        for node, qualified_name, nested in _qualified_functions(tree):
            native_docstring = ast.get_docstring(node, clean=False) is not None
            records.append(
                FunctionRecord(
                    path=relative,
                    qualified_name=qualified_name,
                    line=node.lineno,
                    brief=native_or_generated_brief(node),
                    native_docstring=native_docstring,
                    nested=nested,
                )
            )
    return files, records


def _inventory_header(records: list[FunctionRecord]) -> list[str]:
    """Return the generated inventory heading and aggregate counters."""
    native_count = sum(record.native_docstring for record in records)
    nested_count = sum(record.nested for record in records)
    return [
        "# Complete Production Function Inventory",
        "",
        (
            "This page is generated from the deployed Python sources. It provides an "
            "English description for every function, method, and nested callable, "
            "including private implementation details."
        ),
        "",
        f"- Functions: **{len(records)}**",
        f"- Native source docstrings: **{native_count}**",
        f"- Build-time English briefs: **{len(records) - native_count}**",
        f"- Nested callables listed in this inventory only: **{nested_count}**",
        "",
    ]


def _append_record(
    lines: list[str],
    record: FunctionRecord,
    current_path: str,
) -> str:
    """Append one inventory record and return the active source path."""
    if record.path != current_path:
        current_path = record.path
        lines.extend((f"## `{current_path}`", ""))
    source_kind = "source docstring" if record.native_docstring else "generated brief"
    lines.append(f"- `{record.qualified_name}` (line {record.line}): {record.brief} _{source_kind}_")
    return current_path


def _render_markdown(records: list[FunctionRecord]) -> str:
    lines = _inventory_header(records)
    current_path = ""
    for record in records:
        current_path = _append_record(lines, record, current_path)
    lines.append("")
    return "\n".join(lines)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    """Generate the Markdown inventory and its machine-readable manifest."""
    args = _parse_args()
    repository = args.repository.resolve()
    files, records = scan_repository(repository)
    nested_count = sum(record.nested for record in records)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(_render_markdown(records), encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "source_file_count": len(files),
        "function_count": len(records),
        "doxygen_member_count": len(records) - nested_count,
        "nested_function_count": nested_count,
        "native_docstring_count": sum(record.native_docstring for record in records),
        "sources": [path.relative_to(repository).as_posix() for path in files],
    }
    args.manifest.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "Doxygen inventory: "
        f"{manifest['source_file_count']} files, "
        f"{manifest['function_count']} functions, "
        f"{manifest['native_docstring_count']} native docstrings"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
