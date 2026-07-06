# SPDX-License-Identifier: GPL-3.0-or-later
"""CLI and environment discovery helpers for the mutation audit runner."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

if __package__:
    from . import mutation_audit_support as audit_support
    from .mutation_audit_targets import DEFAULT_TARGETS
else:
    import mutation_audit_support as audit_support
    from mutation_audit_targets import DEFAULT_TARGETS


def parse_args(argv: list[str] | None, *, description: str | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("targets", nargs="*", help="Target module path(s). Defaults to the curated gateway list.")
    parser.add_argument("--list-targets", action="store_true", help="Print selected targets and exit.")
    parser.add_argument("--out-dir", help="Output directory for logs. Defaults to build/mutation-audit/<timestamp>.")
    parser.add_argument("--timeout-s", type=float, default=1800.0, help="Timeout per mutated module.")
    parser.add_argument("--reuse-cache", action="store_true", help="Do not clear .mutmut-cache between target modules.")
    parser.add_argument(
        "--verify-survivors",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Re-apply surviving mutants and run the selected tests in a clean subprocess.",
    )
    parser.add_argument("--no-fail", action="store_true", help="Always exit 0 after writing logs and summary.")
    return parser.parse_args(argv)


def repo_dir(anchor: Path | None = None) -> Path:
    return (anchor or Path(__file__)).resolve().parents[2]


def mutmut_command(repo: Path) -> list[str] | None:
    local_python = repo / ".venv-mutmut" / "bin" / "python"
    if local_python.exists():
        return [str(local_python), "-m", "mutmut"]
    executable = shutil.which("mutmut")
    if executable is not None:
        return [executable]
    if current_python_has_mutmut():
        return [sys.executable, "-m", "mutmut"]
    return None


def current_python_has_mutmut() -> bool:
    probe = subprocess.run(
        [sys.executable, "-c", "import mutmut"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return probe.returncode == 0


def selected_targets(paths: list[str]) -> list[audit_support.MutationTarget]:
    selected = paths or list(DEFAULT_TARGETS)
    return [audit_support.MutationTarget(path=path) for path in selected]


def output_dir(repo: Path, requested: str | None) -> Path:
    if requested:
        return Path(requested).expanduser().resolve()
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    return repo / "build" / "mutation-audit" / timestamp


def print_targets(targets: list[audit_support.MutationTarget]) -> int:
    for target in targets:
        print(target.path)
    return 0
