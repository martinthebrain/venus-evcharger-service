# SPDX-License-Identifier: GPL-3.0-or-later
"""Local and remote software-update state helpers."""

from __future__ import annotations

import os
from typing import Any, ClassVar, TYPE_CHECKING


class _SoftwareUpdateStateMixin:
    SOFTWARE_UPDATE_CHECK_INTERVAL_SECONDS: ClassVar[float]

    if TYPE_CHECKING:  # pragma: no cover

        @classmethod
        def _software_update_manifest_result(
            cls,
            manifest_source: str,
            current_version: str,
            installed_bundle_hash: str,
        ) -> tuple[str, bool, str]: ...

        @classmethod
        def _software_update_version_result(
            cls,
            version_source: str,
            current_version: str,
        ) -> tuple[str, bool, str]: ...

    @staticmethod
    def _software_update_install_script_missing(install_script: str, repo_root: str) -> bool:
        """Return whether the install script or repo root is unavailable."""
        return not install_script or not os.path.isfile(install_script) or not repo_root

    @staticmethod
    def _software_update_restart_script_missing(restart_script: str) -> bool:
        """Return whether the restart handoff script is unavailable."""
        return not restart_script or not os.path.isfile(restart_script)

    @staticmethod
    def _read_text_file(path: Any) -> str:
        """Return one text file payload, or an empty string when unavailable."""
        if not path:
            return ""
        try:
            with open(str(path), "r", encoding="utf-8") as handle:
                return handle.read().strip()
        except OSError:
            return ""

    @classmethod
    def _local_software_update_version(cls, svc: Any) -> str:
        """Return the local wallbox version text used for update diagnostics."""
        installed_version_path = os.path.join(
            str(getattr(svc, "software_update_repo_root", "") or ""),
            ".bootstrap-state",
            "installed_version",
        )
        installed_version = cls._read_text_file(installed_version_path)
        if installed_version:
            return installed_version.splitlines()[0].strip()
        version_path = os.path.join(
            str(getattr(svc, "software_update_repo_root", "") or ""),
            "version.txt",
        )
        version_text = cls._read_text_file(version_path)
        return version_text.splitlines()[0].strip() if version_text else ""

    @classmethod
    def _local_installed_bundle_hash(cls, svc: Any) -> str:
        """Return the locally remembered bundle hash when one exists."""
        path = os.path.join(
            str(getattr(svc, "software_update_repo_root", "") or ""),
            ".bootstrap-state",
            "installed_bundle_sha256",
        )
        payload = cls._read_text_file(path)
        return payload.split(" ", 1)[0].strip() if payload else ""

    @staticmethod
    def _software_update_no_update_active(svc: Any) -> bool:
        """Return whether the local installation currently blocks refreshes."""
        path = str(getattr(svc, "software_update_no_update_file", "") or "")
        return bool(path) and os.path.isfile(path)

    @classmethod
    def _refresh_software_update_local_state(cls, svc: Any) -> None:
        """Refresh the local software-update diagnostics derived from disk layout."""
        svc._software_update_current_version = cls._local_software_update_version(svc)
        svc._software_update_no_update_active = int(cls._software_update_no_update_active(svc))

    @staticmethod
    def _set_software_update_state(
        svc: Any,
        state: str,
        *,
        detail: str = "",
        available: bool | None = None,
        available_version: str | None = None,
        last_result: str | None = None,
    ) -> None:
        """Update the outward software-update state fields in one place."""
        svc._software_update_state = state
        svc._software_update_detail = detail
        if available is not None:
            svc._software_update_available = bool(available)
        if available_version is not None:
            svc._software_update_available_version = available_version
        if last_result is not None:
            svc._software_update_last_result = last_result

    @classmethod
    def _software_update_state_for_no_update_block(cls, svc: Any) -> str:
        """Return the outward state that best describes a ``noUpdate`` block."""
        if bool(getattr(svc, "_software_update_available", False)):
            return "available-blocked"
        if getattr(svc, "_software_update_last_check_at", None) is not None:
            return "up-to-date"
        return "idle"

    @staticmethod
    def _software_update_payload_value(payload: dict[str, Any], key: str) -> str:
        """Return one trimmed string value from an update payload."""
        return str(payload.get(key, "") or "").strip()

    @staticmethod
    def _software_update_manifest_available(
        available_version: str,
        bundle_hash: str,
        current_version: str,
        installed_bundle_hash: str,
    ) -> bool:
        """Return whether a manifest payload announces a newer installable update."""
        if bundle_hash and installed_bundle_hash:
            return bundle_hash != installed_bundle_hash
        return bool(available_version and available_version != current_version)

    @classmethod
    def _software_update_availability_state(cls, svc: Any, available: bool) -> str:
        """Return the outward software-update state for one availability result."""
        if available and cls._software_update_no_update_active(svc):
            return "available-blocked"
        return "available" if available else "up-to-date"

    @classmethod
    def _software_update_check_sources(cls, svc: Any) -> tuple[str, str, str, str]:
        """Return normalized software-update source inputs and local identifiers."""
        return (
            str(getattr(svc, "software_update_manifest_source", "") or "").strip(),
            str(getattr(svc, "software_update_version_source", "") or "").strip(),
            str(getattr(svc, "_software_update_current_version", "") or ""),
            cls._local_installed_bundle_hash(svc),
        )

    @classmethod
    def _run_software_update_check(cls, svc: Any, now: float) -> None:
        """Refresh remote software-update availability from manifest or version text."""
        cls._refresh_software_update_local_state(svc)
        manifest_source, version_source, current_version, installed_bundle_hash = (
            cls._software_update_check_sources(svc)
        )
        available_version = ""
        available = False
        detail = ""
        try:
            cls._set_software_update_state(svc, "checking", detail="")
            if manifest_source:
                available_version, available, detail = cls._software_update_manifest_result(
                    manifest_source,
                    current_version,
                    installed_bundle_hash,
                )
            if not available_version and version_source:
                available_version, available, detail = cls._software_update_version_result(
                    version_source,
                    current_version,
                )
            svc._software_update_last_check_at = now
            svc._software_update_next_check_at = now + cls.SOFTWARE_UPDATE_CHECK_INTERVAL_SECONDS
            cls._set_software_update_state(
                svc,
                cls._software_update_availability_state(svc, available),
                detail=detail,
                available=available,
                available_version=available_version,
            )
        except Exception as error:  # pylint: disable=broad-except
            svc._software_update_last_check_at = now
            svc._software_update_next_check_at = now + cls.SOFTWARE_UPDATE_CHECK_INTERVAL_SECONDS
            cls._set_software_update_state(
                svc,
                "check-failed",
                detail=str(error),
                available=False,
                available_version="",
            )
