# SPDX-License-Identifier: GPL-3.0-or-later
"""Helper-process lifecycle helpers for the Auto input supervisor."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import uuid
from typing import TYPE_CHECKING, Any

from venus_evcharger.inputs.supervisor_snapshot import _AutoInputSupervisorSnapshot


class _AutoInputSupervisorProcess(_AutoInputSupervisorSnapshot):
    if TYPE_CHECKING:  # pragma: no cover
        service: Any

        def refresh_snapshot(self, now: float | None = None) -> None: ...

    def stop_helper(self, force: bool = False) -> None:
        svc = self.service
        svc._ensure_worker_state()
        process = svc._auto_input_helper_process
        if process is None:
            return
        if process.poll() is not None:
            svc._auto_input_helper_process = None
            svc._auto_input_helper_restart_requested_at = None
            return
        try:
            if force:
                process.kill()
            else:
                process.terminate()
        except (OSError, RuntimeError) as error:
            logging.debug("Unable to stop auto input helper pid=%s: %s", getattr(process, "pid", "na"), error)

    def spawn_helper(self, now: float | None = None) -> None:
        svc = self.service
        svc._ensure_worker_state()
        current = svc._time_now() if now is None else float(now)
        self._ensure_runtime_instance_id()
        generation = int(getattr(svc, "_auto_input_helper_generation", 0) or 0) + 1
        svc._auto_input_helper_generation = generation
        self._reset_snapshot_liveness_for_new_helper()
        self._remove_stale_snapshot_file()
        self._terminate_orphaned_helpers()
        command = self._helper_command(generation)
        process = subprocess.Popen(command)  # pylint: disable=consider-using-with
        svc._auto_input_helper_process = process
        svc._auto_input_helper_last_start_at = current
        svc._auto_input_helper_restart_requested_at = None
        logging.info(
            "Started auto input helper pid=%s snapshot=%s instance=%s",
            getattr(process, "pid", "na"),
            svc.auto_input_snapshot_path,
            getattr(svc, "_auto_input_runtime_instance_id", ""),
        )

    def _ensure_runtime_instance_id(self) -> None:
        svc = self.service
        if not str(getattr(svc, "_auto_input_runtime_instance_id", "") or "").strip():
            svc._auto_input_runtime_instance_id = uuid.uuid4().hex

    def _helper_command(self, generation: int) -> list[str]:
        svc = self.service
        return [
            sys.executable,
            "-u",
            svc._auto_input_helper_path(),
            svc._config_path(),
            svc.auto_input_snapshot_path,
            str(os.getpid()),
            str(generation),
            str(getattr(svc, "_auto_input_runtime_instance_id", "") or ""),
        ]

    def _reset_snapshot_liveness_for_new_helper(self) -> None:
        svc = self.service
        svc._auto_input_snapshot_last_seen = None
        svc._auto_input_snapshot_seen_for_current_helper = False
        svc._auto_input_snapshot_writer_pid = None
        svc._auto_input_snapshot_generation = None
        svc._auto_input_snapshot_runtime_instance_id = None

    def _remove_stale_snapshot_file(self) -> None:
        svc = self.service
        path = str(getattr(svc, "auto_input_snapshot_path", "") or "").strip()
        if not self._stale_snapshot_path_removable(path):
            return
        try:
            os.unlink(path)
            svc._auto_input_snapshot_mtime_ns = None
        except FileNotFoundError:
            svc._auto_input_snapshot_mtime_ns = None
        except (OSError, RuntimeError) as error:
            logging.debug("Unable to remove stale auto input snapshot %s: %s", path, error)

    @staticmethod
    def _snapshot_path_is_volatile(path: str) -> bool:
        normalized = os.path.abspath(path)
        return normalized.startswith(("/run/", "/tmp/", "/var/volatile/"))

    def _stale_snapshot_path_removable(self, path: str) -> bool:
        """Return whether a stale helper snapshot may be removed safely."""
        return bool(path) and self._snapshot_path_is_volatile(path)

    def _terminate_orphaned_helpers(self) -> None:
        """Stop stale helper processes for this snapshot before spawning a new one."""
        for pid in self._orphaned_helper_pids():
            try:
                os.kill(pid, 15)
                logging.info("Stopped orphaned auto input helper pid=%s", pid)
            except ProcessLookupError:
                continue
            except (OSError, RuntimeError) as error:
                logging.debug("Unable to stop orphaned auto input helper pid=%s: %s", pid, error)

    def _orphaned_helper_pids(self) -> list[int]:
        snapshot_path = self._orphan_snapshot_path()
        if not snapshot_path:
            return []
        return [
            pid
            for pid in self._orphan_candidate_pids(os.getpid())
            if self._helper_cmdline_matches(pid, snapshot_path)
        ]

    def _orphan_snapshot_path(self) -> str:
        return str(getattr(self.service, "auto_input_snapshot_path", "") or "").strip()

    def _orphan_candidate_pids(self, current_pid: int) -> list[int]:
        return [
            pid
            for name in self._proc_entries()
            for pid in (self._orphan_candidate_pid(name, current_pid),)
            if pid is not None
        ]

    @staticmethod
    def _proc_entries() -> list[str]:
        try:
            return os.listdir("/proc")
        except OSError:
            return []

    @staticmethod
    def _orphan_candidate_pid(name: str, current_pid: int) -> int | None:
        if not name.isdigit():
            return None
        pid = int(name)
        return None if pid == current_pid else pid

    @staticmethod
    def _helper_cmdline_matches(pid: int, snapshot_path: str) -> bool:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as handle:
                cmdline = handle.read().decode("utf-8", "replace").replace("\x00", " ")
        except OSError:
            return False
        return "venus_evcharger_auto_input_helper.py" in cmdline and snapshot_path in cmdline

    def _helper_snapshot_age(self, current: float) -> float | None:
        svc = self.service
        if (
            svc._auto_input_snapshot_last_seen is not None
            and getattr(svc, "_auto_input_snapshot_seen_for_current_helper", True)
        ):
            return current - float(svc._auto_input_snapshot_last_seen)
        if svc._auto_input_helper_last_start_at > 0:
            return current - float(svc._auto_input_helper_last_start_at)
        return None

    def _refresh_snapshot_for_liveness_check(self, current: float) -> None:
        """Read the latest helper heartbeat before deciding whether it is stale."""
        if not str(getattr(self.service, "auto_input_snapshot_path", "") or "").strip():
            return
        self.refresh_snapshot(current)

    def _handle_stale_running_helper(self, process: Any, current: float, snapshot_age: float | None) -> bool:
        svc = self.service
        if snapshot_age is None or snapshot_age <= svc.auto_input_helper_stale_seconds:
            return False
        if svc._auto_input_helper_restart_requested_at is None:
            svc._auto_input_helper_restart_requested_at = current
            logging.warning(
                "Auto input helper pid=%s stale for %.0fs, restarting",
                getattr(process, "pid", "na"),
                snapshot_age,
            )
            svc._stop_auto_input_helper(force=False)
            return True
        if (current - svc._auto_input_helper_restart_requested_at) > max(2.0, svc.auto_input_helper_restart_seconds):
            svc._stop_auto_input_helper(force=True)
        return True

    def _handle_existing_helper_process(self, process: Any, current: float) -> bool:
        svc = self.service
        return_code = process.poll()
        if return_code is None:
            self._refresh_snapshot_for_liveness_check(current)
            snapshot_age = self._helper_snapshot_age(current)
            if self._handle_stale_running_helper(process, current, snapshot_age):
                return True
            return True
        logging.warning(
            "Auto input helper exited with rc=%s pid=%s",
            return_code,
            getattr(process, "pid", "na"),
        )
        svc._auto_input_helper_process = None
        svc._auto_input_helper_restart_requested_at = None
        return False

    def _helper_restart_cooldown_active(self, current: float) -> bool:
        svc = self.service
        return bool(
            svc._auto_input_helper_last_start_at > 0
            and (current - svc._auto_input_helper_last_start_at) < svc.auto_input_helper_restart_seconds
        )

    def _spawn_helper_with_warning(self, current: float) -> None:
        svc = self.service
        try:
            svc._spawn_auto_input_helper(current)
        except (OSError, RuntimeError) as error:
            svc._warning_throttled(
                "auto-input-helper-start-failed",
                max(1.0, svc.auto_input_helper_restart_seconds),
                "Unable to start auto input helper: %s",
                error,
                exc_info=error,
            )

    def ensure_helper_process(self, now: float | None = None) -> None:
        svc = self.service
        svc._ensure_worker_state()
        current = svc._time_now() if now is None else float(now)
        process = svc._auto_input_helper_process
        if process is not None and self._handle_existing_helper_process(process, current):
            return
        if self._helper_restart_cooldown_active(current):
            return
        self._spawn_helper_with_warning(current)
