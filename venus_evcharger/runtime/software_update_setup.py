# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime-state initialization for the software updater."""

from __future__ import annotations

import os
from typing import Any

from venus_evcharger.update.software_update_contracts import UPDATE_RESULT_NONE, UPDATE_STATE_IDLE


def _software_update_repo_path(repo_root: str, *parts: str) -> str:
    """Return one path below the repository root, or empty text without a root."""
    if not repo_root:
        return ""
    return os.path.join(repo_root, *parts)


def _software_update_env(name: str, fallback: str) -> str:
    """Return one updater environment setting with an explicit fallback."""
    value = os.environ.get(name)
    if value is None:
        return fallback
    return value


def _default_version_source(repo_slug: str, channel: str) -> str:
    """Return the default raw GitHub version URL for one updater channel."""
    return f"https://raw.githubusercontent.com/{repo_slug}/{channel}/version.txt"


def initialize_software_update_runtime_state(
    svc: Any,
    *,
    repo_root: str,
    started_at: float,
    current_version: str,
    boot_auto_due_at: float | None,
) -> None:
    """Initialize RAM-only software-update runtime state."""
    svc.started_at = started_at
    svc.software_update_repo_root = repo_root
    svc.software_update_install_script = _software_update_repo_path(repo_root, "install.sh")
    svc.software_update_restart_script = _software_update_repo_path(
        repo_root,
        "deploy/venus/restart_venus_evcharger_service.sh",
    )
    svc.software_update_no_update_file = _software_update_repo_path(repo_root, "noUpdate")
    svc.software_update_log_path = "/var/volatile/log/dbus-venus-evcharger/software-update.log"
    svc.software_update_repo_slug = _software_update_env(
        "VENUS_EVCHARGER_REPO_SLUG",
        "martinthebrain/venus-evcharger-service",
    )
    svc.software_update_channel = _software_update_env("VENUS_EVCHARGER_CHANNEL", "main")
    svc.software_update_manifest_source = _software_update_env(
        "VENUS_EVCHARGER_MANIFEST_SOURCE",
        "",
    )
    svc.software_update_version_source = _software_update_env(
        "VENUS_EVCHARGER_VERSION_SOURCE",
        _default_version_source(svc.software_update_repo_slug, svc.software_update_channel),
    )
    svc._software_update_current_version = current_version
    svc._software_update_available_version = ""
    svc._software_update_available = False
    svc._software_update_state = UPDATE_STATE_IDLE
    svc._software_update_detail = ""
    svc._software_update_last_check_at = None
    svc._software_update_last_run_at = None
    svc._software_update_last_result = UPDATE_RESULT_NONE
    svc._software_update_process = None
    svc._software_update_process_log_handle = None
    svc._software_update_run_requested_at = None
    svc._software_update_no_update_active = int(
        bool(svc.software_update_no_update_file and os.path.isfile(svc.software_update_no_update_file))
    )
    svc._software_update_next_check_at = started_at + 300.0
    svc._software_update_boot_auto_due_at = boot_auto_due_at
