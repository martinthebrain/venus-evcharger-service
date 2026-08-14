# SPDX-License-Identifier: GPL-3.0-or-later
"""Helper-process lifecycle helpers for the Auto input supervisor."""

from __future__ import annotations

import logging
import os
import subprocess
import uuid

from venus_evcharger.inputs.supervisor_contracts import (
    AutoInputSupervisorService,
    HelperProcess,
    SnapshotRefreshPort,
)
from venus_evcharger.ipc.gateway_pressure import service_gateway_pressure_policy


class AutoInputProcessLifecycle:
    """Own the external helper process lifecycle and liveness policy."""

    def __init__(
        self,
        service: AutoInputSupervisorService,
        snapshot_runtime: SnapshotRefreshPort,
        *,
        config_path: str,
        helper_path: str,
    ) -> None:
        self._service = service
        self._snapshot_runtime = snapshot_runtime
        self._config_path = config_path
        self._helper_path = helper_path

    @staticmethod
    def _process_pid(process: HelperProcess) -> int:
        return process.pid

    def stop_helper(self, force: bool = False) -> None:
        svc = self._service
        svc.runtime.ensure_worker_state()
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
            logging.debug("Unable to stop auto input helper pid=%s: %s", self._process_pid(process), error)

    def spawn_helper(self, now: float | None = None) -> None:
        svc = self._service
        svc.runtime.ensure_worker_state()
        current = svc.monotonic_now() if now is None else float(now)
        self._ensure_runtime_instance_id()
        generation = svc._auto_input_helper_generation + 1
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
            self._process_pid(process),
            svc.auto_input_snapshot_path,
            svc._auto_input_runtime_instance_id,
        )

    def _ensure_runtime_instance_id(self) -> None:
        svc = self._service
        if not svc._auto_input_runtime_instance_id.strip():
            svc._auto_input_runtime_instance_id = uuid.uuid4().hex

    def _helper_command(self, generation: int) -> list[str]:
        svc = self._service
        return [
            self._helper_path,
            self._config_path,
            svc.auto_input_snapshot_path,
            str(os.getpid()),
            str(generation),
            svc._auto_input_runtime_instance_id,
        ]

    def _reset_snapshot_liveness_for_new_helper(self) -> None:
        svc = self._service
        svc._auto_input_snapshot_last_seen = None
        svc._auto_input_snapshot_seen_for_current_helper = False
        svc._auto_input_snapshot_writer_pid = None
        svc._auto_input_snapshot_generation = None
        svc._auto_input_snapshot_runtime_instance_id = None
        svc._auto_input_snapshot_last_sequence = None

    def _remove_stale_snapshot_file(self) -> None:
        svc = self._service
        path = svc.auto_input_snapshot_path.strip()
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
        # Supervisor snapshots are restricted to the explicit volatile roots below.
        return normalized.startswith(("/run/", "/tmp/", "/var/volatile/"))  # nosec B108

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
        return self._service.auto_input_snapshot_path.strip()

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
                cmdline = AutoInputProcessLifecycle._normalized_helper_cmdline(handle.read())
        except OSError:
            return False
        helper_names = (
            # Recognize pre-Rust helpers so upgrades can terminate stale processes.
            "venus_evcharger_auto_input_helper.py",
            "venus-evcharger-auto-input-helper",
        )
        return any(name in cmdline for name in helper_names) and snapshot_path in cmdline

    @staticmethod
    def _normalized_helper_cmdline(payload: bytes) -> str:
        return payload.decode(errors="replace").replace("\x00", " ")

    def _helper_snapshot_age(self, current: float) -> float | None:
        svc = self._service
        if (
            svc._auto_input_snapshot_last_seen is not None
            and svc._auto_input_snapshot_seen_for_current_helper
        ):
            return current - float(svc._auto_input_snapshot_last_seen)
        if svc._auto_input_helper_last_start_at > 0:
            return current - float(svc._auto_input_helper_last_start_at)
        return None

    def _refresh_snapshot_for_liveness_check(self, current: float) -> None:
        """Read the latest helper heartbeat before deciding whether it is stale."""
        if not self._service.auto_input_snapshot_path.strip():
            return
        self._snapshot_runtime.refresh_snapshot(current)

    def _handle_stale_running_helper(self, process: HelperProcess, current: float, snapshot_age: float | None) -> bool:
        svc = self._service
        policy = service_gateway_pressure_policy(svc)
        stale_seconds = policy.liveness_timeout_seconds(svc.auto_input_helper_stale_seconds)
        if snapshot_age is None or snapshot_age <= stale_seconds:
            return False
        if svc._auto_input_helper_restart_requested_at is None:
            svc._auto_input_helper_restart_requested_at = current
            logging.warning(
                "Auto input helper pid=%s stale for %.0fs, restarting",
                self._process_pid(process),
                snapshot_age,
            )
            self.stop_helper(force=False)
            return True
        restart_seconds = policy.liveness_timeout_seconds(max(2.0, svc.auto_input_helper_restart_seconds))
        if (current - svc._auto_input_helper_restart_requested_at) > restart_seconds:
            self.stop_helper(force=True)
        return True

    def _handle_existing_helper_process(self, process: HelperProcess, current: float) -> bool:
        svc = self._service
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
            self._process_pid(process),
        )
        svc._auto_input_helper_process = None
        svc._auto_input_helper_restart_requested_at = None
        return False

    def _helper_restart_cooldown_active(self, current: float) -> bool:
        svc = self._service
        restart_seconds = service_gateway_pressure_policy(svc).liveness_timeout_seconds(
            svc.auto_input_helper_restart_seconds,
        )
        return bool(
            svc._auto_input_helper_last_start_at > 0
            and (current - svc._auto_input_helper_last_start_at) < restart_seconds
        )

    def _spawn_helper_with_warning(self, current: float) -> None:
        svc = self._service
        try:
            self.spawn_helper(current)
        except (OSError, RuntimeError) as error:
            svc.runtime.warning_throttled(
                "auto-input-helper-start-failed",
                max(
                    1.0,
                    service_gateway_pressure_policy(svc).liveness_timeout_seconds(
                        svc.auto_input_helper_restart_seconds,
                    ),
                ),
                "Unable to start auto input helper: %s",
                error,
                exc_info=error,
            )

    def ensure_helper_process(self, now: float | None = None) -> None:
        svc = self._service
        svc.runtime.ensure_worker_state()
        current = svc.monotonic_now() if now is None else float(now)
        process = svc._auto_input_helper_process
        if process is not None and self._handle_existing_helper_process(process, current):
            return
        if self._helper_restart_cooldown_active(current):
            return
        self._spawn_helper_with_warning(current)
