#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Require an explicit export contract for F401-exempt test facades."""

from __future__ import annotations

import ast
from pathlib import Path


FACADE_PATTERNS = ("*_common.py", "*_support.py")


def _declares_all(path: Path) -> bool:
    tree = _module_tree(path)
    return any(
        isinstance(node, (ast.Assign, ast.AnnAssign))
        and any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in _assignment_targets(node)
        )
        for node in tree.body
    )


def _module_tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _contains_test_case(path: Path) -> bool:
    return any(
        isinstance(node, ast.ClassDef)
        and any(_is_test_case_base(base) for base in node.bases)
        for node in _module_tree(path).body
    )


def _is_test_case_base(base: ast.expr) -> bool:
    return (isinstance(base, ast.Name) and base.id == "TestCase") or (
        isinstance(base, ast.Attribute) and base.attr == "TestCase"
    )


def _assignment_targets(node: ast.Assign | ast.AnnAssign) -> tuple[ast.expr, ...]:
    if isinstance(node, ast.Assign):
        return tuple(node.targets)
    return (node.target,)


def _facade_paths(tests_dir: Path) -> tuple[Path, ...]:
    matches = {path for pattern in FACADE_PATTERNS for path in tests_dir.glob(pattern)}
    return tuple(sorted(path for path in matches if not _contains_test_case(path)))


def _missing_export_contracts(repo_dir: Path, facades: tuple[Path, ...]) -> tuple[Path, ...]:
    return tuple(path.relative_to(repo_dir) for path in facades if not _declares_all(path))


def _report_missing(missing: tuple[Path, ...]) -> int:
    print("F401-exempt test facades must declare __all__:")
    for path in missing:
        print(f"  {path}")
    return 1


def main() -> int:
    """Validate every F401-exempt test facade."""
    repo_dir = Path(__file__).resolve().parents[2]
    facades = _facade_paths(repo_dir / "tests")
    missing = _missing_export_contracts(repo_dir, facades)
    if missing:
        return _report_missing(missing)
    print(f"Test facade contracts passed for {len(facades)} modules.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
