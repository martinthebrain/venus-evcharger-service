# SPDX-License-Identifier: GPL-3.0-or-later
"""Local and remote software-update state helpers."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from venus_evcharger.update.software_update_errors import SOFTWARE_UPDATE_CHECK_ERRORS
from venus_evcharger.update.software_update_contracts import (
    SoftwareUpdateLastResult,
    SoftwareUpdateState,
    UPDATE_STATE_AVAILABLE,
    UPDATE_STATE_AVAILABLE_BLOCKED,
    UPDATE_STATE_CHECK_FAILED,
    UPDATE_STATE_CHECKING,
    UPDATE_STATE_IDLE,
    UPDATE_STATE_UP_TO_DATE,
)


class _SoftwareUpdateState(ABC):
    CHECK_INTERVAL_SECONDS: ClassVar[float]

    @classmethod
    @abstractmethod
    def _software_update_manifest_result(
        cls,
        manifest_source: str,
        current_version: str,
        installed_bundle_hash: str,
    ) -> tuple[str, bool, str]:
        """Fetch and evaluate one manifest source."""

    @classmethod
    @abstractmethod
    def _software_update_version_result(
        cls,
        version_source: str,
        current_version: str,
    ) -> tuple[str, bool, str]:
        """Fetch and evaluate one version-text source."""

    @staticmethod
    def _software_update_install_script_missing(install_script: str, repo_root: str) -> bool:
        """Return whether the install script or repo root is unavailable."""
        return not install_script or not os.path.isfile(install_script) or not repo_root

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

    @staticmethod
    def _software_update_text_attr(svc: Any, name: str) -> str:
        """Return one optional service attribute as normalized text."""
        try:
            value = getattr(svc, name)
        except AttributeError:
            return ""
        return "" if value is None else str(value)

    @staticmethod
    def _software_update_bool_attr(svc: Any, name: str) -> bool:
        """Return one optional service attribute as a boolean flag."""
        try:
            return bool(getattr(svc, name))
        except AttributeError:
            return False

    @classmethod
    def _local_software_update_version(cls, svc: Any) -> str:
        """Return the local wallbox version text used for update diagnostics."""
        repo_root = cls._software_update_text_attr(svc, "software_update_repo_root")
        if not repo_root:
            return ""
        installed_version_path = os.path.join(
            repo_root,
            ".bootstrap-state",
            "installed_version",
        )
        installed_version = cls._read_text_file(installed_version_path)
        if installed_version:
            return installed_version.splitlines()[0].strip()
        version_path = os.path.join(
            repo_root,
            "version.txt",
        )
        version_text = cls._read_text_file(version_path)
        return version_text.splitlines()[0].strip() if version_text else ""

    @classmethod
    def _local_installed_bundle_hash(cls, svc: Any) -> str:
        """Return the locally remembered bundle hash when one exists."""
        repo_root = cls._software_update_text_attr(svc, "software_update_repo_root")
        if not repo_root:
            return ""
        path = os.path.join(
            repo_root,
            ".bootstrap-state",
            "installed_bundle_sha256",
        )
        payload = cls._read_text_file(path)
        parts = payload.split()
        return parts[0].strip() if parts else ""

    @staticmethod
    def _software_update_no_update_active(svc: Any) -> bool:
        """Return whether the local installation currently blocks refreshes."""
        path = _SoftwareUpdateState._software_update_text_attr(svc, "software_update_no_update_file")
        return bool(path) and os.path.isfile(path)

    @classmethod
    def _refresh_software_update_local_state(cls, svc: Any) -> None:
        """Refresh the local software-update diagnostics derived from disk layout."""
        svc._software_update_current_version = cls._local_software_update_version(svc)
        svc._software_update_no_update_active = int(cls._software_update_no_update_active(svc))

    @staticmethod
    def _set_software_update_state(
        svc: Any,
        state: SoftwareUpdateState,
        *,
        detail: str = "",
        available: bool | None = None,
        available_version: str | None = None,
        last_result: SoftwareUpdateLastResult | None = None,
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
    def _software_update_state_for_no_update_block(cls, svc: Any) -> SoftwareUpdateState:
        """Return the outward state that best describes a ``noUpdate`` block."""
        if cls._software_update_bool_attr(svc, "_software_update_available"):
            return UPDATE_STATE_AVAILABLE_BLOCKED
        try:
            last_check_at = getattr(svc, "_software_update_last_check_at")
        except AttributeError:
            last_check_at = None
        if last_check_at is not None:
            return UPDATE_STATE_UP_TO_DATE
        return UPDATE_STATE_IDLE

    @staticmethod
    def _software_update_payload_value(payload: dict[str, Any], key: str) -> str:
        """Return one trimmed string value from an update payload."""
        try:
            value = payload[key]
        except KeyError:
            return ""
        return "" if value is None else str(value).strip()

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
    def _software_update_availability_state(cls, svc: Any, available: bool) -> SoftwareUpdateState:
        """Return the outward software-update state for one availability result."""
        if available and cls._software_update_no_update_active(svc):
            return UPDATE_STATE_AVAILABLE_BLOCKED
        return UPDATE_STATE_AVAILABLE if available else UPDATE_STATE_UP_TO_DATE

    @classmethod
    def _software_update_check_sources(cls, svc: Any) -> tuple[str, str, str, str]:
        """Return normalized software-update source inputs and local identifiers."""
        return (
            cls._software_update_text_attr(svc, "software_update_manifest_source").strip(),
            cls._software_update_text_attr(svc, "software_update_version_source").strip(),
            cls._software_update_text_attr(svc, "_software_update_current_version"),
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
            cls._set_software_update_state(svc, UPDATE_STATE_CHECKING, detail="")
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
            svc._software_update_next_check_at = now + cls.CHECK_INTERVAL_SECONDS
            cls._set_software_update_state(
                svc,
                cls._software_update_availability_state(svc, available),
                detail=detail,
                available=available,
                available_version=available_version,
            )
        except SOFTWARE_UPDATE_CHECK_ERRORS as error:
            svc._software_update_last_check_at = now
            svc._software_update_next_check_at = now + cls.CHECK_INTERVAL_SECONDS
            cls._set_software_update_state(
                svc,
                UPDATE_STATE_CHECK_FAILED,
                detail=str(error),
                available=False,
                available_version="",
            )
