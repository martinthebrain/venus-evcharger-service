# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="attr-defined,no-any-return"
# pyright: reportAttributeAccessIssue=false, reportReturnType=false
"""Core state payload helpers for the Control API mixin."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from venus_evcharger.backend.config import backend_mode_for_service, backend_type_for_service
from venus_evcharger.control import ControlCommand
from venus_evcharger.core.contracts import (
    normalized_state_api_dbus_diagnostics_fields,
    normalized_state_api_runtime_fields,
    normalized_state_api_summary_fields,
    normalized_state_api_topology_fields,
    normalized_state_api_update_fields,
)


def _plain_state_mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


class _ControlApiStateCoreMixin:
    def _control_command_from_payload(self, payload: dict[str, Any], source: str = "http") -> ControlCommand:
        self._ensure_write_controller()
        command = self._write_controller.build_control_command_from_payload(payload, source=source)
        if not isinstance(command, ControlCommand):
            raise TypeError("write controller returned non-ControlCommand payload")
        return command

    def _state_api_summary_payload(self) -> dict[str, Any]:
        summary = getattr(self, "_state_summary")()
        return normalized_state_api_summary_fields(
            {
                "ok": True,
                "api_version": "v1",
                "kind": "summary",
                "summary": summary,
            }
        )

    def _state_api_runtime_payload(self) -> dict[str, Any]:
        runtime_state = getattr(self, "_current_runtime_state")()
        return normalized_state_api_runtime_fields(
            {
                "ok": True,
                "api_version": "v1",
                "kind": "runtime",
                "state": runtime_state,
            }
        )

    def _state_api_dbus_diagnostics_payload(self) -> dict[str, Any]:
        self._ensure_dbus_publisher()
        now_func = getattr(self, "_time_now", None)
        raw_now = now_func() if callable(now_func) else time.time()
        now = float(raw_now) if isinstance(raw_now, (int, float)) else time.time()
        counters = _plain_state_mapping(self._dbus_publisher._diagnostic_counter_values(now))
        ages = _plain_state_mapping(self._dbus_publisher._diagnostic_age_values(now))
        return normalized_state_api_dbus_diagnostics_fields(
            {
                "ok": True,
                "api_version": "v1",
                "kind": "dbus-diagnostics",
                "state": {
                    **counters,
                    **ages,
                },
            }
        )

    def _state_api_topology_payload(self) -> dict[str, Any]:
        supported = tuple(getattr(self, "supported_phase_selections", ("P1",)) or ("P1",))
        return normalized_state_api_topology_fields(
            {
                "ok": True,
                "api_version": "v1",
                "kind": "topology",
                "state": {
                    "backend_mode": backend_mode_for_service(self, "combined"),
                    "meter_backend": backend_type_for_service(self, "meter", "na"),
                    "switch_backend": backend_type_for_service(self, "switch", "na"),
                    "charger_backend": backend_type_for_service(self, "charger", "na"),
                    "active_phase_selection": getattr(self, "active_phase_selection", "P1"),
                    "requested_phase_selection": getattr(self, "requested_phase_selection", "P1"),
                    "supported_phase_selections": list(supported),
                    "available_modes": [0, 1, 2],
                    "service_name": getattr(self, "service_name", ""),
                    "connection_name": getattr(self, "connection_name", ""),
                },
            }
        )

    def _state_api_update_payload(self) -> dict[str, Any]:
        return normalized_state_api_update_fields(
            {
                "ok": True,
                "api_version": "v1",
                "kind": "update",
                "state": {
                    "current_version": getattr(self, "_software_update_current_version", ""),
                    "available_version": getattr(self, "_software_update_available_version", ""),
                    "available": getattr(self, "_software_update_available", False),
                    "state": getattr(self, "_software_update_state", "idle"),
                    "detail": getattr(self, "_software_update_detail", ""),
                    "last_check_at": getattr(self, "_software_update_last_check_at", None),
                    "last_run_at": getattr(self, "_software_update_last_run_at", None),
                    "last_result": getattr(self, "_software_update_last_result", ""),
                    "run_requested_at": getattr(self, "_software_update_run_requested_at", None),
                    "next_check_at": getattr(self, "_software_update_next_check_at", None),
                    "boot_auto_due_at": getattr(self, "_software_update_boot_auto_due_at", None),
                    "no_update_active": getattr(self, "_software_update_no_update_active", False),
                },
            }
        )
