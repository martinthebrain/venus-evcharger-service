# SPDX-License-Identifier: GPL-3.0-or-later
"""Virtual-state publishing and update-cycle helpers for the Venus EV charger service."""

from __future__ import annotations

import logging
import subprocess
from typing import Any

import requests

from venus_evcharger.update.input_cache import _UpdateCycleInputCacheMixin
from venus_evcharger.update.learning import _UpdateCycleLearningMixin
from venus_evcharger.update.learning_runtime import _UpdateCycleLearningRuntimeMixin
from venus_evcharger.update.offline_publish import _UpdateCycleOfflineMixin
from venus_evcharger.update.pm_snapshot import _UpdateCyclePmSnapshotMixin
from venus_evcharger.update.relay import _UpdateCycleRelayMixin
from venus_evcharger.update.runtime_cycle import _UpdateCycleRuntimeMixin
from venus_evcharger.update.software_update_errors import SOFTWARE_UPDATE_PROCESS_ERRORS
from venus_evcharger.update.software_update_support import _UpdateCycleSoftwareUpdateMixin
from venus_evcharger.update.state import _UpdateCycleStateMixin
from venus_evcharger.update.victron_ess_balance import _UpdateCycleVictronEssBalanceMixin


class UpdateCycleController(
    _UpdateCycleSoftwareUpdateMixin,
    _UpdateCyclePmSnapshotMixin,
    _UpdateCycleOfflineMixin,
    _UpdateCycleInputCacheMixin,
    _UpdateCycleLearningRuntimeMixin,
    _UpdateCycleVictronEssBalanceMixin,
    _UpdateCycleRuntimeMixin,
    _UpdateCycleStateMixin,
    _UpdateCycleRelayMixin,
    _UpdateCycleLearningMixin,
):
    """Encapsulate the periodic Shelly/Auto update pipeline."""

    LEARNED_POWER_STABLE_MIN_SAMPLES = 3
    LEARNED_POWER_STABLE_MIN_SECONDS = 15.0
    LEARNED_POWER_STABLE_TOLERANCE_WATTS = 150.0
    LEARNED_POWER_STABLE_TOLERANCE_RATIO = 0.08
    LEARNED_POWER_SIGNATURE_MISMATCH_SESSIONS = 2
    LEARNED_POWER_VOLTAGE_TOLERANCE_VOLTS = 10.0
    FUTURE_INPUT_TIMESTAMP_TOLERANCE_SECONDS = 1.0
    SOFTWARE_UPDATE_CHECK_INTERVAL_SECONDS = 7.0 * 24.0 * 3600.0
    SOFTWARE_UPDATE_REQUEST_TIMEOUT_SECONDS = 5.0

    def __init__(self, service: Any, phase_values_func: Any, health_code_func: Any) -> None:
        self.service = service
        self._phase_values = phase_values_func
        self._health_code = health_code_func

    @classmethod
    def _software_update_manifest_result(
        cls,
        manifest_source: str,
        current_version: str,
        installed_bundle_hash: str,
    ) -> tuple[str, bool, str]:
        """Return version, availability, and detail from one manifest source."""
        response = requests.get(
            manifest_source,
            timeout=cls.SOFTWARE_UPDATE_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return "", False, ""
        available_version = cls._software_update_payload_value(payload, "version")
        bundle_hash = cls._software_update_payload_value(payload, "bundle_sha256")
        available = cls._software_update_manifest_available(
            available_version,
            bundle_hash,
            current_version,
            installed_bundle_hash,
        )
        return available_version, available, "manifest"

    @classmethod
    def _software_update_version_result(
        cls,
        version_source: str,
        current_version: str,
    ) -> tuple[str, bool, str]:
        """Return version, availability, and detail from one version-text source."""
        response = requests.get(
            version_source,
            timeout=cls.SOFTWARE_UPDATE_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        available_version = str(response.text or "").splitlines()[0].strip()
        return (
            available_version,
            bool(available_version and available_version != current_version),
            "version-file",
        )

    @classmethod
    def _spawn_software_update_process(
        cls,
        log_path: str,
        repo_root: str,
        restart_script: str,
    ) -> tuple[subprocess.Popen[bytes], Any]:
        """Start one detached update process and return the child plus log handle."""
        log_handle = cls._software_update_log_handle(log_path)
        try:
            process = subprocess.Popen(  # pylint: disable=consider-using-with
                cls._software_update_command(repo_root, restart_script),
                cwd=repo_root,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except SOFTWARE_UPDATE_PROCESS_ERRORS:
            cls._close_open_log_handle(log_handle)
            raise
        return process, log_handle

    def sign_of_life(self) -> bool:
        """Periodic heartbeat log for troubleshooting."""
        svc = self.service
        logging.info("[%s] Last '/Ac/Power': %s", svc.service_name, svc._dbusservice["/Ac/Power"])
        return True

    def update(self) -> bool:
        """Periodic update loop: read Shelly, compute auto logic, update DBus."""
        svc = self.service
        try:
            result = self._run_update_cycle()
            now = svc._time_now()
            flush_runtime_overrides = getattr(svc, "_flush_runtime_overrides", None)
            if callable(flush_runtime_overrides):
                flush_runtime_overrides(now)
            self._software_update_housekeeping(svc, now)
            return result
        except Exception as error:  # pylint: disable=broad-except
            logging.warning(
                "Error updating Venus EV charger data: %s (%s)",
                error,
                svc._state_summary(),
                exc_info=error,
            )
        return True
