# SPDX-License-Identifier: GPL-3.0-or-later
"""Subprocess execution helpers for mutation audit runs."""

from __future__ import annotations

import subprocess
from pathlib import Path

if __package__:
    from . import mutation_audit_support as audit_support
else:
    import mutation_audit_support as audit_support


def run_logged(
    command: list[str],
    *,
    cwd: Path,
    log_path: Path,
    timeout_s: float,
) -> subprocess.CompletedProcess[str]:
    with log_path.open("w", encoding="utf-8") as log:
        log.write(f"$ {audit_support.shellish(command)}\n\n")
        log.flush()
        try:
            process = subprocess.run(
                command,
                cwd=cwd,
                check=False,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as error:
            log.write(f"\nTIMEOUT after {timeout_s:.1f}s\n{error}\n")
            return subprocess.CompletedProcess(command, audit_support.TIMEOUT_RETURNCODE)
    return process


def capture_results(
    *,
    mutmut: list[str],
    cwd: Path,
    results_path: Path,
) -> subprocess.CompletedProcess[str]:
    command = [*mutmut, "results", "--all", "true"]
    with results_path.open("w", encoding="utf-8") as output:
        output.write(f"$ {audit_support.shellish(command)}\n\n")
        output.flush()
        return subprocess.run(command, cwd=cwd, check=False, stdout=output, stderr=subprocess.STDOUT, text=True)
