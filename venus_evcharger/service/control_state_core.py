# SPDX-License-Identifier: GPL-3.0-or-later
"""Core state payload helpers for the Control API role."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from venus_evcharger.backend.config import backend_mode_for_service, backend_type_for_service
from venus_evcharger.control import ControlCommand
from venus_evcharger.control.models import ControlCommandSource
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


class ControlStateCore:
    """Build core Control API payloads from explicit service components."""

    def __init__(self, service: Any) -> None:
        self.service = service

    def command_from_payload(
        self,
        payload: dict[str, Any],
        source: ControlCommandSource = "http",
    ) -> ControlCommand:
        command = self.service.auto.command_from_payload(payload, source=source)
        if not isinstance(command, ControlCommand):
            raise TypeError("write controller returned non-ControlCommand payload")
        return command

    def summary_payload(self) -> dict[str, Any]:
        summary = self.service.state.summary()
        return normalized_state_api_summary_fields(
            {
                "ok": True,
                "api_version": "v1",
                "kind": "summary",
                "summary": summary,
            }
        )

    def runtime_payload(self) -> dict[str, Any]:
        runtime_state = self.service.state.current()
        return normalized_state_api_runtime_fields(
            {
                "ok": True,
                "api_version": "v1",
                "kind": "runtime",
                "state": runtime_state,
            }
        )

    def dbus_diagnostics_payload(self) -> dict[str, Any]:
        publisher = self.service.controllers.runtime.publisher
        now_func = getattr(self.service, "time_now", None)
        raw_now = now_func() if callable(now_func) else time.time()
        now = float(raw_now) if isinstance(raw_now, (int, float)) else time.time()
        diagnostics = publisher.diagnostic_snapshot(now)
        counters = _plain_state_mapping(diagnostics.counters)
        ages = _plain_state_mapping(diagnostics.ages)
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

    def topology_payload(self) -> dict[str, Any]:
        service = self.service
        supported = tuple(getattr(service, "supported_phase_selections", ()))
        if not supported:
            supported = ("P1",)
        return normalized_state_api_topology_fields(
            {
                "ok": True,
                "api_version": "v1",
                "kind": "topology",
                "state": {
                    "backend_mode": backend_mode_for_service(service, "combined"),
                    "meter_backend": backend_type_for_service(service, "meter", "na"),
                    "switch_backend": backend_type_for_service(service, "switch", "na"),
                    "charger_backend": backend_type_for_service(service, "charger", "na"),
                    "active_phase_selection": getattr(service, "active_phase_selection", "P1"),
                    "requested_phase_selection": getattr(service, "requested_phase_selection", "P1"),
                    "supported_phase_selections": list(supported),
                    "available_modes": [0, 1, 2],
                    "service_name": getattr(service, "service_name", ""),
                    "connection_name": getattr(service, "connection_name", ""),
                },
            }
        )

    def update_payload(self) -> dict[str, Any]:
        service = self.service
        return normalized_state_api_update_fields(
            {
                "ok": True,
                "api_version": "v1",
                "kind": "update",
                "state": {
                    "current_version": getattr(service, "_software_update_current_version", ""),
                    "available_version": getattr(service, "_software_update_available_version", ""),
                    "available": getattr(service, "_software_update_available", False),
                    "state": getattr(service, "_software_update_state", "idle"),
                    "detail": getattr(service, "_software_update_detail", ""),
                    "last_check_at": getattr(service, "_software_update_last_check_at", None),
                    "last_run_at": getattr(service, "_software_update_last_run_at", None),
                    "last_result": getattr(service, "_software_update_last_result", ""),
                    "run_requested_at": getattr(service, "_software_update_run_requested_at", None),
                    "next_check_at": getattr(service, "_software_update_next_check_at", None),
                    "boot_auto_due_at": getattr(service, "_software_update_boot_auto_due_at", None),
                    "no_update_active": getattr(service, "_software_update_no_update_active", False),
                },
            }
        )
