# SPDX-License-Identifier: GPL-3.0-or-later
"""Mutmut configuration helpers for the optional mutation audit."""

from __future__ import annotations

import contextlib
import re
from collections.abc import Iterator
from pathlib import Path

if __package__:
    from .mutation_audit_config_selections_bootstrap import FOCUSED_TEST_SELECTIONS_BOOTSTRAP
    from .mutation_audit_config_selections_core import FOCUSED_TEST_SELECTIONS_CORE
    from .mutation_audit_config_selections_tail import FOCUSED_TEST_SELECTIONS_TAIL
    from .mutation_audit_targets import DEFAULT_TEST_SELECTION
else:
    from mutation_audit_config_selections_bootstrap import FOCUSED_TEST_SELECTIONS_BOOTSTRAP
    from mutation_audit_config_selections_core import FOCUSED_TEST_SELECTIONS_CORE
    from mutation_audit_config_selections_tail import FOCUSED_TEST_SELECTIONS_TAIL
    from mutation_audit_targets import DEFAULT_TEST_SELECTION

_FOCUSED_TEST_SELECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    *FOCUSED_TEST_SELECTIONS_CORE,
    *FOCUSED_TEST_SELECTIONS_BOOTSTRAP,
    *FOCUSED_TEST_SELECTIONS_TAIL,
)
_TOOL_MUTMUT_SECTION = re.compile(r"(?ms)^\[tool\.mutmut\]\n.*?(?=^\[|\Z)")


@contextlib.contextmanager
def mutmut_config_for_target(repo: Path, target_path: str) -> Iterator[None]:
    pyproject = repo / "pyproject.toml"
    original = pyproject.read_text(encoding="utf-8")
    _write_text_atomically(pyproject, pyproject_with_mutmut_config(original, target_path))
    try:
        yield
    finally:
        current = pyproject.read_text(encoding="utf-8")
        _write_text_atomically(pyproject, restore_tool_mutmut_section(current, original))


def _write_text_atomically(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.mutation-audit.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def pyproject_with_mutmut_config(original: str, target_path: str) -> str:
    return f"{strip_tool_mutmut_section(original)}\n{mutmut_config_toml(target_path)}"


def strip_tool_mutmut_section(content: str) -> str:
    return _TOOL_MUTMUT_SECTION.sub("", content).strip() + "\n"


def restore_tool_mutmut_section(current: str, original: str) -> str:
    """Restore the owned mutmut section while preserving concurrent edits elsewhere."""
    restored = strip_tool_mutmut_section(current)
    original_match = _TOOL_MUTMUT_SECTION.search(original)
    if original_match is None:
        return restored
    return f"{restored}\n{original_match.group(0).rstrip()}\n"


def mutmut_config_toml(target_path: str) -> str:
    test_selection = test_selection_for_target(target_path)
    also_copy = [
        "venus_evcharger_auto_input_helper.py",
        "venus_evcharger_dbus_adapter.py",
        "venus_evcharger_service.py",
        "CONTROL_API.md",
        "deploy/venus",
    ]
    if "/" not in target_path:
        also_copy.insert(0, "venus_evcharger")
    lines = [
        "[tool.mutmut]",
        f'source_paths = ["{source_path_for_target(target_path)}"]',
        f'only_mutate = ["{target_path}"]',
        "also_copy = [",
    ]
    lines.extend(f'    "{path}",' for path in also_copy)
    lines.extend([
        "]",
        'pytest_add_cli_args = ["-k", "not socket"]',
        "pytest_add_cli_args_test_selection = [",
    ])
    lines.extend(f'    "{path}",' for path in test_selection)
    return "\n".join([*lines, "]", ""])


def source_path_for_target(target_path: str) -> str:
    if target_path.startswith("scripts/dev/"):
        return "scripts/dev"
    top_level, _separator, _remainder = target_path.partition("/")
    return top_level


def test_selection_for_target(target_path: str) -> tuple[str, ...]:
    for prefix, selection in _FOCUSED_TEST_SELECTIONS:
        if target_path.startswith(prefix):
            return selection
    return DEFAULT_TEST_SELECTION


def focused_test_selections() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Return the immutable target-to-test map for repository contract checks."""
    return _FOCUSED_TEST_SELECTIONS
