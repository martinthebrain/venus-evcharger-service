# SPDX-License-Identifier: GPL-3.0-or-later
"""Common primitives for the Raspberry-Pi DBus gateway release gate."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUI_PATHS = (
    "/Connected",
    "/Mode",
    "/StartStop",
    "/AutoStart",
    "/SetCurrent",
    "/Ac/Power",
    "/Ac/Current",
    "/Session/Energy",
    "/Session/Time",
    "/ChargingTime",
)


@dataclass(frozen=True)
class CommandResult:
    cmd: Sequence[str]
    returncode: int
    stdout: str
    stderr: str


class GateFailure(RuntimeError):
    """Raised when one release-gate assertion fails."""


class PiSession:
    def __init__(self, target: str, *, ssh_config: str) -> None:
        self.target = target
        self.ssh_config = ssh_config

    def ssh(self, script: str, *, timeout: float = 30.0) -> str:
        result = run_command(
            ["ssh", "-F", self.ssh_config, "-o", "BatchMode=yes", self.target, script],
            timeout=timeout,
        )
        return require_success(result, f"ssh {self.target}")


def run_command(cmd: Sequence[str], *, input_text: str | None = None, timeout: float = 30.0) -> CommandResult:
    completed = subprocess.run(
        list(cmd),
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return CommandResult(tuple(cmd), completed.returncode, completed.stdout, completed.stderr)


def require_success(result: CommandResult, detail: str) -> str:
    if result.returncode != 0:
        raise GateFailure(
            f"{detail} failed rc={result.returncode}\n"
            f"cmd={' '.join(result.cmd)}\nstdout={result.stdout}\nstderr={result.stderr}"
        )
    return result.stdout.strip()
