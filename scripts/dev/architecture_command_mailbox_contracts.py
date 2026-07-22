#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Architecture checks for neutral IPC command mailbox boundaries."""

from __future__ import annotations

import re
from pathlib import Path

DBUS_COMMAND_POLICY_IMPORT_PATTERN = re.compile(
    r"\b(?:from|import)\s+venus_evcharger\.dbus_gateway_commands\b"
)
IPC_DBUS_IMPORT_PATTERN = re.compile(
    r"\b(?:from|import)\s+venus_evcharger\.(?:dbus_adapter|dbus_gateway)\b"
)


def check_command_mailbox_contracts(repo: Path) -> list[str]:
    """Return violations of neutral mailbox and DBus policy ownership."""
    failures = _retired_command_type_failures(repo)
    for path in sorted((repo / "venus_evcharger").rglob("*.py")):
        failures.extend(_command_mailbox_path_failures(repo, path))
    return failures


def _retired_command_type_failures(repo: Path) -> list[str]:
    retired_types = repo / "venus_evcharger/dbus_gateway_command_types.py"
    if not retired_types.exists():
        return []
    return [
        "venus_evcharger/dbus_gateway_command_types.py: neutral command types belong under venus_evcharger/ipc/"
    ]


def _command_mailbox_path_failures(repo: Path, path: Path) -> list[str]:
    relative_path = str(path.relative_to(repo))
    text = path.read_text(encoding="utf-8")
    return [
        *_retired_command_type_import_failures(relative_path, text),
        *_ipc_dbus_dependency_failures(relative_path, text),
        *_dbus_command_policy_boundary_failures(relative_path, text),
    ]


def _retired_command_type_import_failures(relative_path: str, text: str) -> list[str]:
    if "dbus_gateway_command_types" not in text:
        return []
    return [f"{relative_path}: retired DBus command type bridge is forbidden"]


def _ipc_dbus_dependency_failures(relative_path: str, text: str) -> list[str]:
    if not relative_path.startswith("venus_evcharger/ipc/"):
        return []
    if not IPC_DBUS_IMPORT_PATTERN.search(text):
        return []
    return [f"{relative_path}: neutral IPC modules must not depend on DBus gateway modules"]


def _dbus_command_policy_boundary_failures(relative_path: str, text: str) -> list[str]:
    if not DBUS_COMMAND_POLICY_IMPORT_PATTERN.search(text):
        return []
    if _dbus_command_policy_consumer(relative_path):
        return []
    return [f"{relative_path}: DBus command policy is private to the gateway adapter boundary"]


def _dbus_command_policy_consumer(relative_path: str) -> bool:
    return relative_path in {
        "venus_evcharger/dbus_gateway.py",
        "venus_evcharger/dbus_gateway_client.py",
    } or relative_path.startswith("venus_evcharger/dbus_adapter/")
