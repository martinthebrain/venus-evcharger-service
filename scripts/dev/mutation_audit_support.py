#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Support helpers for the focused mutmut audit runner."""

from __future__ import annotations

import ast
import fcntl
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

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
    counts = dict.fromkeys(RESULT_WORDS, 0)
    for word in RESULT_WORDS:
        phrase = "no tests" if word == "no_tests" else word
        counts[word] += sum(int(value) for value in re.findall(rf"\b(\d+)\s+{phrase}\b", text, flags=re.I))
        counts[word] += len(re.findall(rf":\s+{phrase}\b", text, flags=re.I))
    return counts


def survivor_names(text: str) -> list[str]:
    return mutant_names(text, "survived")


def mutant_names(text: str, status: str) -> list[str]:
    return [
        match.group(1).strip()
        for match in re.finditer(rf"^\s*([^:\s][^:]*):\s+{re.escape(status)}\b", text, flags=re.I | re.M)
    ]


def target_status(run_returncode: int, results_returncode: int, counts: dict[str, int], *, log_text: str = "") -> str:
    if run_returncode == TIMEOUT_RETURNCODE:
        return "timeout"
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


def verify_survivors(
    *,
    repo: Path,
    mutmut: list[str],
    target_path: str,
    survivor_names: list[str],
    log_path: Path,
    test_selection: Sequence[str],
    configure_target: Callable[[Path, str], AbstractContextManager[None]],
) -> int:
    """Return how many apparent survivors fail in a clean subprocess."""
    return verify_mutants(
        repo=repo,
        mutmut=mutmut,
        target_path=target_path,
        mutant_names=survivor_names,
        log_path=log_path,
        test_selection=test_selection,
        configure_target=configure_target,
        status_label="survived",
    )


def verify_mutants(
    *,
    repo: Path,
    mutmut: list[str],
    target_path: str,
    mutant_names: list[str],
    log_path: Path,
    test_selection: Sequence[str],
    configure_target: Callable[[Path, str], AbstractContextManager[None]],
    status_label: str,
) -> int:
    """Return how many named mutants fail selected tests in clean subprocesses."""
    if not mutant_names:
        return 0
    target_file = repo / target_path
    original = target_file.read_bytes()
    verified = 0
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\nVerifying {status_label} mutants in clean subprocesses\n")
        with configure_target(repo, target_path):
            for mutant in mutant_names:
                verified += int(
                    _mutant_fails_selected_tests(
                        repo=repo,
                        mutmut=mutmut,
                        target_file=target_file,
                        original=original,
                        mutant=mutant,
                        log=log,
                        test_selection=test_selection,
                    )
                )
    return verified


def _mutant_fails_selected_tests(
    *,
    repo: Path,
    mutmut: list[str],
    target_file: Path,
    original: bytes,
    mutant: str,
    log: TextIO,
    test_selection: Sequence[str],
) -> bool:
    try:
        if not _apply_mutant(repo=repo, mutmut=mutmut, mutant=mutant, log=log):
            return False
        failed = _run_survivor_tests(repo=repo, mutmut=mutmut, test_selection=test_selection, log=log)
        log.write(f"verification {'killed' if failed else 'kept'} {mutant}: rc={1 if failed else 0}\n")
        return failed
    finally:
        target_file.write_bytes(original)


def _apply_mutant(*, repo: Path, mutmut: list[str], mutant: str, log: TextIO) -> bool:
    result = subprocess.run(
        [*mutmut, "apply", mutant],
        cwd=repo,
        check=False,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if result.returncode != 0:
        log.write(f"verification apply failed for {mutant}: rc={result.returncode}\n")
        return False
    return True


def _run_survivor_tests(
    *,
    repo: Path,
    mutmut: list[str],
    test_selection: Sequence[str],
    log: TextIO,
) -> bool:
    result = subprocess.run(
        survivor_verification_test_command(mutmut, test_selection),
        cwd=repo,
        check=False,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return result.returncode != 0


def survivor_verification_test_command(mutmut: list[str], test_selection: Sequence[str]) -> list[str]:
    python = python_for_mutmut_command(mutmut)
    return [python, "-m", "pytest", "-q", "-k", "not socket", *test_selection]


def python_for_mutmut_command(mutmut: list[str]) -> str:
    python_module_command_min_parts = 3
    if len(mutmut) >= python_module_command_min_parts and mutmut[1:3] == ["-m", "mutmut"]:
        return mutmut[0]
    return sys.executable
