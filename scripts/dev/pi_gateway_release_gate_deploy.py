# SPDX-License-Identifier: GPL-3.0-or-later
"""Atomically deploy an exact workspace snapshot for the Raspberry-Pi gate."""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from typing import BinaryIO, NoReturn

from pi_gateway_release_gate_common import ROOT, GateFailure, PiSession

DEPLOY_EXCLUDES = (
    "--exclude-vcs-ignores",
    "--exclude=.git",
    "--exclude=.venv*",
    "--exclude=.mypy_cache",
    "--exclude=.pytest_cache",
    "--exclude=.mutmut-cache",
    "--exclude=.coverage",
    "--exclude=coverage.xml",
    "--exclude=htmlcov",
    "--exclude=publication-order-state.json.journal",
    "--exclude=__pycache__",
    "--exclude=*.pyc",
)


@dataclass(frozen=True)
class _TransferDiagnostics:
    tar_rc: int
    tar_stderr: str
    ssh_rc: int | None
    ssh_stdout: bytes
    ssh_stderr: bytes


def deploy_repo(pi: PiSession, remote_dir: str) -> None:
    """Transfer into staging and activate only after both transports succeed."""
    staging_dir = f"{remote_dir}.release-gate-staging"
    backup_dir = f"{remote_dir}.release-gate-previous"
    _prepare_staging(pi, staging_dir, backup_dir)
    tar = _start_archive()
    tar_stdout, tar_stderr_pipe = _archive_pipes(pi, staging_dir, tar)
    ssh = _start_transfer(pi, staging_dir, tar_stdout)
    tar_stdout.close()
    ssh_stdout, ssh_stderr = ssh.communicate(timeout=90.0)
    tar_stderr = tar_stderr_pipe.read().decode(errors="replace")
    tar_rc = tar.wait(timeout=10.0)
    if tar_rc != 0 or ssh.returncode != 0:
        _raise_transfer_failure(
            pi,
            staging_dir,
            _TransferDiagnostics(
                tar_rc=tar_rc,
                tar_stderr=tar_stderr,
                ssh_rc=ssh.returncode,
                ssh_stdout=ssh_stdout,
                ssh_stderr=ssh_stderr,
            ),
        )
    _activate_deployment(pi, remote_dir, staging_dir, backup_dir)


def _prepare_staging(pi: PiSession, staging_dir: str, backup_dir: str) -> None:
    """Create an empty staging tree without touching the active target."""
    pi.ssh(
        f"rm -rf {shlex.quote(staging_dir)} {shlex.quote(backup_dir)} && "
        f"mkdir -p {shlex.quote(staging_dir)}",
        timeout=20.0,
    )


def _start_archive() -> subprocess.Popen[bytes]:
    """Stream an exact filtered workspace archive to stdout."""
    return subprocess.Popen(
        ["tar", *DEPLOY_EXCLUDES, "-C", str(ROOT), "-czf", "-", "."],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
    )


def _archive_pipes(
    pi: PiSession,
    staging_dir: str,
    tar: subprocess.Popen[bytes],
) -> tuple[BinaryIO, BinaryIO]:
    """Validate the binary archive pipes promised by ``Popen``."""
    if tar.stdout is None:
        _raise_missing_pipe(pi, staging_dir, "stdout")
    if tar.stderr is None:
        _raise_missing_pipe(pi, staging_dir, "stderr")
    return tar.stdout, tar.stderr


def _raise_missing_pipe(pi: PiSession, staging_dir: str, missing_pipe: str) -> NoReturn:
    """Clean staging and reject an invalid local archive process."""
    pi.ssh(f"rm -rf {shlex.quote(staging_dir)}", timeout=20.0)
    raise GateFailure(f"deploy tar {missing_pipe} pipe unavailable")


def _start_transfer(pi: PiSession, staging_dir: str, tar_stdout: BinaryIO) -> subprocess.Popen[bytes]:
    """Start the SSH extraction process against the staging tree."""
    return subprocess.Popen(
        [
            "ssh",
            "-F",
            pi.ssh_config,
            "-o",
            "BatchMode=yes",
            pi.target,
            f"cd {shlex.quote(staging_dir)} && tar -xzf -",
        ],
        stdin=tar_stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
    )


def _raise_transfer_failure(
    pi: PiSession,
    staging_dir: str,
    diagnostics: _TransferDiagnostics,
) -> NoReturn:
    """Clean staging and report both sides of a failed stream transfer."""
    pi.ssh(f"rm -rf {shlex.quote(staging_dir)}", timeout=20.0)
    raise GateFailure(
        "deploy failed\n"
        f"tar_rc={diagnostics.tar_rc} tar_stderr={diagnostics.tar_stderr}\n"
        f"ssh_rc={diagnostics.ssh_rc} stdout={diagnostics.ssh_stdout.decode(errors='replace')}"
        f" stderr={diagnostics.ssh_stderr.decode(errors='replace')}"
    )


def _activate_deployment(pi: PiSession, remote_dir: str, staging_dir: str, backup_dir: str) -> None:
    """Swap staging into place and restore the previous tree on swap failure."""
    remote = shlex.quote(remote_dir)
    staging = shlex.quote(staging_dir)
    backup = shlex.quote(backup_dir)
    pi.ssh(
        "set -eu; "
        f"if [ -e {remote} ]; then mv {remote} {backup}; fi; "
        f"if mv {staging} {remote}; then rm -rf {backup}; "
        f"else if [ -e {backup} ]; then mv {backup} {remote}; fi; exit 1; fi",
        timeout=30.0,
    )
