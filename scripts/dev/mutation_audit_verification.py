# SPDX-License-Identifier: GPL-3.0-or-later
"""Verification helpers for apparent mutmut survivors."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import TextIO


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
                    mutant_fails_selected_tests(
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


def mutant_fails_selected_tests(
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
        if not apply_mutant(repo=repo, mutmut=mutmut, mutant=mutant, log=log):
            return False
        failed = run_survivor_tests(repo=repo, mutmut=mutmut, test_selection=test_selection, log=log)
        log.write(f"verification {'killed' if failed else 'kept'} {mutant}: rc={1 if failed else 0}\n")
        return failed
    finally:
        target_file.write_bytes(original)


def apply_mutant(*, repo: Path, mutmut: list[str], mutant: str, log: TextIO) -> bool:
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


def run_survivor_tests(
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
