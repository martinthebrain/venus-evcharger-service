# SPDX-License-Identifier: GPL-3.0-or-later
"""Software-update run and housekeeping helpers."""

from __future__ import annotations

import os
from typing import Any, TYPE_CHECKING

from venus_evcharger.update.software_update_errors import SOFTWARE_UPDATE_PROCESS_ERRORS
from venus_evcharger.update.software_update_state import _SoftwareUpdateStateMixin


class _SoftwareUpdateRunMixin(_SoftwareUpdateStateMixin):
    if TYPE_CHECKING:  # pragma: no cover

        @classmethod
        def _spawn_software_update_process(
            cls,
            log_path: str,
            repo_root: str,
            restart_script: str,
        ) -> tuple[Any, Any]: ...

    @classmethod
    def _start_software_update_run(cls, svc: Any, now: float, source: str) -> bool:
        """Launch one detached software-update run through the bootstrap installer."""
        if cls._software_update_run_already_active(svc):
            return False
        cls._refresh_software_update_local_state(svc)
        if cls._software_update_run_blocked_by_no_update(svc):
            return False
        run_paths = cls._prepared_software_update_run_paths(svc)
        if run_paths is None:
            return False
        return cls._launch_software_update_run(svc, run_paths, now, source)

    @classmethod
    def _launch_software_update_run(
        cls,
        svc: Any,
        run_paths: tuple[str, str],
        now: float,
        source: str,
    ) -> bool:
        """Spawn the detached update process and publish the running state."""
        repo_root, restart_script = run_paths
        log_path = str(getattr(svc, "software_update_log_path", "") or "")
        try:
            process, log_handle = cls._spawn_software_update_process(
                log_path,
                repo_root,
                restart_script,
            )
        except SOFTWARE_UPDATE_PROCESS_ERRORS as error:
            cls._software_update_mark_install_failed(svc, error)
            return False
        svc._software_update_process = process
        svc._software_update_process_log_handle = log_handle
        svc._software_update_last_run_at = now
        svc._software_update_run_requested_at = None
        cls._set_software_update_state(svc, "running", detail=source, last_result="running")
        return True

    @classmethod
    def _prepared_software_update_run_paths(cls, svc: Any) -> tuple[str, str] | None:
        """Return normalized repo-root and restart-script paths when the run is available."""
        install_script, repo_root, restart_script = cls._software_update_run_paths(svc)
        unavailable_detail = cls._software_update_unavailable_detail(
            install_script,
            repo_root,
            restart_script,
        )
        if unavailable_detail is None:
            return repo_root, restart_script
        cls._software_update_mark_unavailable(svc, unavailable_detail)
        return None

    @staticmethod
    def _software_update_run_paths(svc: Any) -> tuple[str, str, str]:
        """Return normalized install, repo-root, and restart paths for update runs."""
        return (
            str(getattr(svc, "software_update_install_script", "") or ""),
            str(getattr(svc, "software_update_repo_root", "") or ""),
            str(getattr(svc, "software_update_restart_script", "") or ""),
        )

    @classmethod
    def _software_update_run_already_active(cls, svc: Any) -> bool:
        """Return whether an update run is already active and clear the queued trigger."""
        if getattr(svc, "_software_update_process", None) is None:
            return False
        svc._software_update_run_requested_at = None
        return True

    @classmethod
    def _software_update_run_blocked_by_no_update(cls, svc: Any) -> bool:
        """Return whether local noUpdate policy blocks a requested update run."""
        if not cls._software_update_no_update_active(svc):
            return False
        cls._set_software_update_state(
            svc,
            cls._software_update_state_for_no_update_block(svc),
            detail="noUpdate marker present",
        )
        svc._software_update_run_requested_at = None
        return True

    @classmethod
    def _software_update_unavailable_detail(
        cls,
        install_script: str,
        repo_root: str,
        restart_script: str,
    ) -> str | None:
        """Return the missing-resource detail for one update run or ``None`` when usable."""
        if cls._software_update_install_script_missing(install_script, repo_root):
            return "install.sh missing"
        if cls._software_update_restart_script_missing(restart_script):
            return "restart script missing"
        return None

    @classmethod
    def _software_update_mark_unavailable(cls, svc: Any, detail: str) -> None:
        """Publish one unavailable update-run state."""
        cls._set_software_update_state(
            svc,
            "update-unavailable",
            detail=detail,
            last_result="failed",
        )
        svc._software_update_run_requested_at = None

    @classmethod
    def _software_update_mark_install_failed(cls, svc: Any, error: Exception) -> None:
        """Publish one failed update-run start result."""
        cls._set_software_update_state(
            svc,
            "install-failed",
            detail=str(error),
            last_result="failed",
        )
        svc._software_update_run_requested_at = None

    @staticmethod
    def _software_update_log_handle(log_path: str) -> Any:
        """Open the update log file after creating its parent directory when needed."""
        log_dir = os.path.dirname(log_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        return open(log_path, "ab")  # pylint: disable=consider-using-with

    @staticmethod
    def _software_update_command(repo_root: str, restart_script: str) -> list[str]:
        """Return the shell command used for detached update execution."""
        return ["/bin/bash", "-lc", f'cd "{repo_root}" && "./install.sh" && "{restart_script}"']

    @staticmethod
    def _close_open_log_handle(log_handle: Any) -> None:
        """Close one possibly-open log handle while ignoring close errors."""
        if log_handle is None:
            return
        try:
            log_handle.close()
        except OSError:
            pass

    @staticmethod
    def _close_software_update_log_handle(svc: Any) -> None:
        """Close the current update log handle when one is open."""
        log_handle = getattr(svc, "_software_update_process_log_handle", None)
        if log_handle is None:
            return
        try:
            log_handle.close()
        except OSError:
            pass

    @classmethod
    def _completed_software_update_state(
        cls,
        svc: Any,
        return_code: int,
    ) -> tuple[str, str, Any, Any, str]:
        """Return the final outward state fields for one completed update process."""
        if int(return_code) == 0:
            return "installed", "completed", False, "", "success"
        return (
            "install-failed",
            f"exit {int(return_code)}",
            getattr(svc, "_software_update_available", False),
            getattr(svc, "_software_update_available_version", ""),
            "failed",
        )

    @classmethod
    def _poll_software_update_process(cls, svc: Any) -> None:
        """Refresh the software-update run state from the detached child process."""
        process = getattr(svc, "_software_update_process", None)
        if process is None:
            return
        return_code = process.poll()
        if return_code is None:
            return
        cls._close_software_update_log_handle(svc)
        svc._software_update_process = None
        svc._software_update_process_log_handle = None
        cls._refresh_software_update_local_state(svc)
        state, detail, available, available_version, last_result = (
            cls._completed_software_update_state(svc, int(return_code))
        )
        cls._set_software_update_state(
            svc,
            state,
            detail=detail,
            available=available,
            available_version=available_version,
            last_result=last_result,
        )

    @staticmethod
    def _software_update_due(now: float, due_at: Any) -> bool:
        """Return whether one optional update timestamp is due."""
        return isinstance(due_at, (int, float)) and float(now) >= float(due_at)

    @classmethod
    def _clear_software_update_triggers_while_running(
        cls,
        svc: Any,
        now: float,
        process_running: bool,
    ) -> None:
        """Clear queued update triggers that became irrelevant while a run is active."""
        if process_running and getattr(svc, "_software_update_run_requested_at", None) is not None:
            svc._software_update_run_requested_at = None
        if process_running and cls._software_update_due(
            now,
            getattr(svc, "_software_update_boot_auto_due_at", None),
        ):
            svc._software_update_boot_auto_due_at = None

    @classmethod
    def _software_update_housekeeping(cls, svc: Any, now: float) -> None:
        """Drive periodic update checks and deferred update runs from the main loop."""
        cls._poll_software_update_process(svc)
        cls._refresh_software_update_local_state(svc)
        process_running = getattr(svc, "_software_update_process", None) is not None
        cls._clear_software_update_triggers_while_running(svc, now, process_running)
        if process_running:
            return
        cls._software_update_run_due_check(svc, now)
        cls._software_update_run_due_boot_update(svc, now)
        cls._software_update_run_due_manual_trigger(svc, now)

    @classmethod
    def _software_update_run_due_check(cls, svc: Any, now: float) -> None:
        """Run the periodic update check when its due timestamp has arrived."""
        if cls._software_update_due(now, getattr(svc, "_software_update_next_check_at", None)):
            cls._run_software_update_check(svc, float(now))

    @classmethod
    def _software_update_run_due_boot_update(cls, svc: Any, now: float) -> None:
        """Run the deferred boot-time update when due."""
        if not cls._software_update_due(
            now,
            getattr(svc, "_software_update_boot_auto_due_at", None),
        ):
            return
        svc._software_update_boot_auto_due_at = None
        cls._start_software_update_run(svc, float(now), "boot-auto")

    @classmethod
    def _software_update_run_due_manual_trigger(cls, svc: Any, now: float) -> None:
        """Run a queued manual update trigger."""
        if getattr(svc, "_software_update_run_requested_at", None) is not None:
            cls._start_software_update_run(svc, float(now), "manual")
