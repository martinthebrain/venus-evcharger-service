# SPDX-License-Identifier: GPL-3.0-or-later
"""Run command connectors with hard stdout and stderr limits."""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import time
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BoundedCommandResult:
    """Completed command output held within configured byte limits."""

    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class _SpawnedCommand:
    pid: int
    command: tuple[str, ...]
    stdout_fd: int
    stderr_fd: int


def run_bounded_command(
    command: tuple[str, ...],
    *,
    timeout_seconds: float,
    stdout_limit: int,
    stderr_limit: int,
) -> BoundedCommandResult:
    """Run one command without allowing either output pipe to grow unbounded."""
    _validate_arguments(command, timeout_seconds, stdout_limit, stderr_limit)
    process = _spawn_command(command)
    deadline = time.monotonic() + timeout_seconds
    try:
        stdout, stderr = _collect_output(
            process,
            deadline=deadline,
            stdout_limit=stdout_limit,
            stderr_limit=stderr_limit,
        )
        return_code = _wait_for_process(process, deadline)
    except BaseException:
        _terminate_process(process)
        raise
    decoded_stdout = _decode_output(stdout, "stdout")
    decoded_stderr = _decode_output(stderr, "stderr")
    if return_code:
        raise subprocess.CalledProcessError(
            return_code,
            command,
            output=decoded_stdout,
            stderr=decoded_stderr,
        )
    return BoundedCommandResult(decoded_stdout, decoded_stderr)


def _spawn_command(command: tuple[str, ...]) -> _SpawnedCommand:
    stdout_read, stdout_write = os.pipe()
    stderr_read, stderr_write = os.pipe()
    file_actions = (
        (os.POSIX_SPAWN_DUP2, stdout_write, 1),
        (os.POSIX_SPAWN_DUP2, stderr_write, 2),
        (os.POSIX_SPAWN_CLOSE, stdout_read),
        (os.POSIX_SPAWN_CLOSE, stderr_read),
        (os.POSIX_SPAWN_CLOSE, stdout_write),
        (os.POSIX_SPAWN_CLOSE, stderr_write),
    )
    try:
        pid = os.posix_spawnp(command[0], command, os.environ, file_actions=file_actions)
    except BaseException:
        os.close(stdout_read)
        os.close(stderr_read)
        raise
    finally:
        os.close(stdout_write)
        os.close(stderr_write)
    return _SpawnedCommand(pid, command, stdout_read, stderr_read)


def _collect_output(
    process: _SpawnedCommand,
    *,
    deadline: float,
    stdout_limit: int,
    stderr_limit: int,
) -> tuple[bytes, bytes]:
    buffers = {
        process.stdout_fd: bytearray(),
        process.stderr_fd: bytearray(),
    }
    limits = {
        process.stdout_fd: stdout_limit,
        process.stderr_fd: stderr_limit,
    }
    labels = {
        process.stdout_fd: "stdout",
        process.stderr_fd: "stderr",
    }
    selector = selectors.DefaultSelector()
    try:
        selector.register(process.stdout_fd, selectors.EVENT_READ)
        selector.register(process.stderr_fd, selectors.EVENT_READ)
        while selector.get_map():
            events = selector.select(_remaining_seconds(deadline, process.command))
            if not events:
                raise subprocess.TimeoutExpired(process.command, timeout=0.0)
            for key, _mask in events:
                _read_ready_pipe(
                    selector,
                    int(key.fd),
                    buffers,
                    limits,
                    labels,
                )
    finally:
        selector.close()
        os.close(process.stdout_fd)
        os.close(process.stderr_fd)
    return bytes(buffers[process.stdout_fd]), bytes(buffers[process.stderr_fd])


def _read_ready_pipe(
    selector: selectors.BaseSelector,
    file_descriptor: int,
    buffers: dict[int, bytearray],
    limits: dict[int, int],
    labels: dict[int, str],
) -> None:
    limit = limits[file_descriptor]
    chunk = os.read(
        file_descriptor,
        min(16384, limit + 1 - len(buffers[file_descriptor])),
    )
    if not chunk:
        selector.unregister(file_descriptor)
        return
    buffers[file_descriptor].extend(chunk)
    if len(buffers[file_descriptor]) > limit:
        raise ValueError(f"Command {labels[file_descriptor]} exceeds {limit} bytes")


def _wait_for_process(process: _SpawnedCommand, deadline: float) -> int:
    while True:
        waited_pid, status = os.waitpid(process.pid, os.WNOHANG)
        if waited_pid == process.pid:
            return os.waitstatus_to_exitcode(status)
        time.sleep(min(0.01, _remaining_seconds(deadline, process.command)))


def _terminate_process(process: _SpawnedCommand) -> None:
    try:
        os.kill(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        os.waitpid(process.pid, 0)
    except ChildProcessError:
        pass


def _remaining_seconds(deadline: float, command: tuple[str, ...]) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0.0:
        raise subprocess.TimeoutExpired(command, timeout=0.0)
    return remaining


def _decode_output(payload: bytes, label: str) -> str:
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"Command {label} is not valid UTF-8") from error


def _validate_arguments(
    command: tuple[str, ...],
    timeout_seconds: float,
    stdout_limit: int,
    stderr_limit: int,
) -> None:
    _validate_command(command)
    _require_positive_timeout(timeout_seconds)
    _require_positive_limit(stdout_limit)
    _require_positive_limit(stderr_limit)


def _validate_command(command: tuple[str, ...]) -> None:
    if not command:
        raise ValueError("Command must not be empty")
    if not command[0]:
        raise ValueError("Command must not be empty")


def _require_positive_timeout(timeout_seconds: float) -> None:
    if timeout_seconds <= 0.0:
        raise ValueError("Command timeout must be positive")


def _require_positive_limit(limit: int) -> None:
    if limit < 1:
        raise ValueError("Command output limits must be positive")


__all__ = ["BoundedCommandResult", "run_bounded_command"]
