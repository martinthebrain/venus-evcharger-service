# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent software-update controller for the wallbox runtime."""

from __future__ import annotations

import subprocess
from typing import BinaryIO

import requests

from venus_evcharger.update.software_update_errors import SOFTWARE_UPDATE_PROCESS_ERRORS
from venus_evcharger.update.software_update_run import _SoftwareUpdateRun


class SoftwareUpdateController(_SoftwareUpdateRun):
    """Own software-update checks and process lifecycle outside the update cycle."""

    CHECK_INTERVAL_SECONDS = 7.0 * 24.0 * 3600.0
    REQUEST_TIMEOUT_SECONDS = 5.0

    @classmethod
    def _software_update_manifest_result(
        cls,
        manifest_source: str,
        current_version: str,
        installed_bundle_hash: str,
    ) -> tuple[str, bool, str]:
        response = requests.get(
            manifest_source,
            timeout=cls.REQUEST_TIMEOUT_SECONDS,
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
        response = requests.get(
            version_source,
            timeout=cls.REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        version_lines = str(response.text or "").splitlines()
        available_version = version_lines[0].strip() if version_lines else ""
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
    ) -> tuple[subprocess.Popen[bytes], BinaryIO]:
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

    def housekeeping(self, service: object, now: float) -> None:
        """Advance scheduled checks and queued update runs once."""
        self._software_update_housekeeping(service, now)


__all__ = ["SoftwareUpdateController"]
