#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Validate explicit suppression policy across project Python sources."""

from __future__ import annotations

import tokenize
from collections.abc import Callable
from io import StringIO
from pathlib import Path

ALLOWED_NOQA_CODES = {
    "E402",
    "F401",
    "F403",
    "N802",
    "S310",
    "S603",
}

NO_MUTATE_FILE_PREFIXES = (
    "venus_evcharger/dbus_adapter",
    "venus_evcharger/dbus_gateway",
    "venus_evcharger/ipc/energy_binary.py",
)

SuppressionChecker = Callable[[str, int, str, str], str | None]


def _suppression_scan_paths(repo: Path) -> list[Path]:
    roots = [repo / "venus_evcharger", repo / "tests", repo / "scripts" / "dev"]
    paths = [path for root in roots for path in sorted(root.rglob("*.py"))]
    paths.extend(
        repo / name
        for name in (
            "venus_evcharger_service.py",
            "venus_evcharger_auto_input_helper.py",
            "venus_evcharger_dbus_adapter.py",
        )
        if (repo / name).exists()
    )
    return sorted(paths)


def _line_allowed_no_cover(relative_path: str, line: str) -> bool:
    if "_protocol" in relative_path or "/protocols/" in relative_path:
        return True
    return any(
        marker in line
        for marker in (
            "TYPE_CHECKING",
            "__main__",
            "Protocol",
            "...",
            "Venus DBus/GLib",
            "initialize_dbus_service",
            "foreign DBus objects",
            "from ",
        )
    )


def _noqa_codes(line: str) -> set[str]:
    _prefix, _sep, suffix = line.partition("# noqa:")
    code_text = suffix.split("-", 1)[0].strip()
    return {code.strip() for code in code_text.split(",") if code.strip()}


def _line_allowed_noqa(line: str) -> bool:
    codes = _noqa_codes(line)
    return bool(codes) and codes <= ALLOWED_NOQA_CODES


def _line_allowed_no_mutate(relative_path: str) -> bool:
    return relative_path.startswith(NO_MUTATE_FILE_PREFIXES)


def _type_ignore_failure(relative_path: str, line_number: int, comment: str, _line: str) -> str | None:
    if "type: ignore" in comment:
        return f"{relative_path}:{line_number}: type ignore suppressions are forbidden"
    return None


def _noqa_failure(relative_path: str, line_number: int, comment: str, line: str) -> str | None:
    if "# noqa" in comment and not _line_allowed_noqa(comment):
        return f"{relative_path}:{line_number}: unapproved noqa suppression: {line.strip()}"
    return None


def _no_cover_failure(relative_path: str, line_number: int, comment: str, line: str) -> str | None:
    if "pragma: no cover" in comment and not _line_allowed_no_cover(relative_path, line):
        return f"{relative_path}:{line_number}: unapproved coverage suppression: {line.strip()}"
    return None


def _no_mutate_failure(relative_path: str, line_number: int, comment: str, _line: str) -> str | None:
    if "pragma: no mutate" in comment and not _line_allowed_no_mutate(relative_path):
        return f"{relative_path}:{line_number}: mutation suppression outside gateway/adapter/IPC boundary"
    return None


def _suppression_failure(relative_path: str, line_number: int, comment: str, line: str) -> str | None:
    checkers: tuple[SuppressionChecker, ...] = (
        _type_ignore_failure,
        _noqa_failure,
        _no_cover_failure,
        _no_mutate_failure,
    )
    for checker in checkers:
        failure = checker(relative_path, line_number, comment, line)
        if failure is not None:
            return failure
    return None


def check_suppression_contracts(repo: Path) -> list[str]:
    """Return every forbidden Python suppression below ``repo``."""
    failures: list[str] = []
    for path in _suppression_scan_paths(repo):
        relative_path = str(path.relative_to(repo))
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        for token in tokenize.generate_tokens(StringIO(text).readline):
            if token.type != tokenize.COMMENT:
                continue
            line_number = token.start[0]
            line = lines[line_number - 1]
            failure = _suppression_failure(relative_path, line_number, token.string, line)
            if failure is not None:
                failures.append(failure)
    return failures
