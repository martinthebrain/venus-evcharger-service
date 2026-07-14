#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Support helpers for the focused mutmut audit runner."""

from __future__ import annotations

import ast
import fcntl
import json
import re
import subprocess
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

RESULT_WORDS = ("killed", "survived", "timeout", "suspicious", "skipped", "no_tests")
ATTENTION_WORDS = ("survived", "timeout", "suspicious", "no_tests")
TIMEOUT_RETURNCODE = 124
OK_STATUSES = frozenset(("ok", "not_applicable"))
LOCK_FILENAME = ".mutmut-audit.lock"


@dataclass(frozen=True)
class MutationTarget:
    path: str


@dataclass(frozen=True)
class MutationCommandContext:
    repo: Path
    mutmut: list[str]


@dataclass(frozen=True)
class TargetArtifacts:
    log_path: Path
    results_path: Path


@dataclass(frozen=True)
class TargetMutmutRun:
    run_result: subprocess.CompletedProcess[str]
    results_result: subprocess.CompletedProcess[str]
    result_text: str
    log_text: str


def not_applicable_mutation_target(repo: Path, target_path: str) -> str | None:
    """Return why a target is outside mutation-testing scope, if applicable."""
    path = repo / target_path
    if is_constant_only_module(path):
        return "constant-only module; mutation coverage belongs to consuming runtime modules"
    return None


def generated_no_mutants(repo: Path, target_path: str) -> bool:
    """Return whether mutmut produced metadata but no mutant keys for a target."""
    meta_path = repo / "mutants" / f"{target_path}.meta"
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    mutant_maps = (
        data.get("exit_code_by_key"),
        data.get("type_check_error_by_key"),
        data.get("durations_by_key"),
        data.get("estimated_durations_by_key"),
    )
    return all(isinstance(item, dict) and not item for item in mutant_maps)


def is_constant_only_module(path: Path) -> bool:
    """Return whether a Python file contains imports and assignments only."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return False
    return any(is_constant_assignment(node) for node in tree.body) and all(
        is_constant_only_node(node) for node in tree.body
    )


def is_constant_only_node(node: ast.stmt) -> bool:
    return (
        isinstance(node, (ast.Import, ast.ImportFrom))
        or is_module_docstring(node)
        or is_constant_assignment(node)
    )


def is_constant_assignment(node: ast.stmt) -> bool:
    return isinstance(node, (ast.Assign, ast.AnnAssign)) or node.__class__.__name__ == "TypeAlias"


def is_module_docstring(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def write_not_applicable_result(log_path: Path, results_path: Path, target_path: str, reason: str) -> None:
    text = f"Skipping mutation audit for {target_path}: {reason}\n"
    log_path.write_text(text, encoding="utf-8")
    results_path.write_text(text, encoding="utf-8")


@contextmanager
def mutmut_audit_lock(repo: Path):
    """Prevent concurrent mutmut runs from sharing and corrupting one cache."""
    with (repo / LOCK_FILENAME).open("w", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("another mutation audit is already running in this repository") from error
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def parse_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    normalized_text = text.lower()
    for word in RESULT_WORDS:
        phrase = "no tests" if word == "no_tests" else word
        numbered = sum(int(value) for value in re.findall(rf"\b(\d+)\s+{phrase}\b", normalized_text))
        labelled = len(re.findall(rf":\s+{phrase}\b", normalized_text))
        counts[word] = numbered + labelled
    return counts


def mutant_names(text: str, status: str) -> list[str]:
    mutant_pattern = (
        r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\."
        r"x(?:_|ǁ)[^:\s]+__mutmut_\d+"
    )
    return [
        match.group(1)
        for match in re.finditer(
            rf"^\s*({mutant_pattern}):\s+{re.escape(status)}\b",
            text,
            flags=re.I | re.M,
        )
    ]


def target_status(run_returncode: int, results_returncode: int, counts: dict[str, int], *, log_text: str) -> str:
    if run_returncode == TIMEOUT_RETURNCODE:
        return "timeout"
    if counts["no_tests"]:
        return "needs_attention"
    if no_mutant_test_mapping(run_returncode, results_returncode, counts, log_text):
        return "needs_attention"
    if command_failed(run_returncode, results_returncode):
        return "error"
    return status_from_counts(counts)


def no_mutant_test_mapping(
    run_returncode: int,
    results_returncode: int,
    counts: dict[str, int],
    log_text: str,
) -> bool:
    no_results = results_returncode == 0 and not any(counts.values())
    return (
        run_returncode == 1
        and no_results
        and "could not find any test case for any mutant" in log_text
    )


def command_failed(run_returncode: int, results_returncode: int) -> bool:
    return run_returncode != 0 or results_returncode != 0


def status_from_counts(counts: dict[str, int]) -> str:
    return "needs_attention" if any(counts[word] for word in ATTENTION_WORDS) else "ok"


def exit_code(statuses: Sequence[str], *, no_fail: bool) -> int:
    if no_fail:
        return 0
    return 1 if any(status not in OK_STATUSES for status in statuses) else 0


def slug(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", path).strip("_")


def shellish(command: list[str]) -> str:
    return " ".join(quote(part) for part in command)


def quote(part: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:=+-]+", part):
        return part
    return "'" + part.replace("'", "'\"'\"'") + "'"
