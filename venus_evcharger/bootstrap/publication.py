# SPDX-License-Identifier: GPL-3.0-or-later
"""Semantic EVCS registration performed by the core bootstrap."""

from __future__ import annotations

import platform
from collections.abc import Mapping

from venus_evcharger.auto.policy_settings import auto_policy_control_values
from venus_evcharger.ports.gateway_publication import EvcsServiceIdentity, require_gateway_publication


class EvcsPublicationRegistrar:
    """Register the EVCS through transport-neutral semantic fields."""

    def __init__(self, service: object, *, script_path: str) -> None:
        self._service = service
        self._script_path = script_path

    def register(self) -> None:
        receipt = require_gateway_publication(self._service).register_evcs(
            self.identity(),
            self.initial_fields(),
        )
        if not receipt.accepted:
            raise RuntimeError("Gateway rejected EVCS registration")

    def identity(self) -> EvcsServiceIdentity:
        svc = self._service
        return EvcsServiceIdentity(
            product_name=_required_text(svc, "product_name"),
            custom_name=_required_text(svc, "custom_name"),
            firmware_version=_required_text(svc, "firmware_version"),
            hardware_version=_required_text(svc, "hardware_version"),
            serial=_required_text(svc, "serial"),
            connection_name=_required_text(svc, "connection_name"),
            process_name=self._script_path,
            process_version="Unknown version, and running on Python " + platform.python_version(),
        )

    def initial_fields(self) -> dict[str, object]:
        svc = self._service
        policy = auto_policy_control_values(getattr(svc, "auto_policy"))
        fields: dict[str, object] = {
            "connected": int(bool(getattr(svc, "topology_configured", getattr(svc, "host_configured", False)))),
            "status": int(getattr(svc, "last_status", 0)),
            "mode": int(getattr(svc, "virtual_mode", 0)),
            "auto_start": int(getattr(svc, "virtual_autostart", 0)),
            "start_stop": int(getattr(svc, "virtual_startstop", 0)),
            "enable": int(getattr(svc, "virtual_enable", 0)),
            "min_current": float(getattr(svc, "min_current", 0.0)),
            "max_current": float(getattr(svc, "max_current", 0.0)),
            "set_current": float(getattr(svc, "virtual_set_current", 0.0)),
            "phase_selection": str(getattr(svc, "requested_phase_selection", "P1")),
            "phase_selection_active": str(getattr(svc, "active_phase_selection", "P1")),
            "supported_phase_selections": ",".join(getattr(svc, "supported_phase_selections", ("P1",))),
            "auto_health": str(getattr(svc, "_last_health_reason", "init")),
            "auto_health_code": int(getattr(svc, "_last_health_code", 0)),
            "auto_state": str(getattr(svc, "_last_auto_state", "idle")),
            "auto_state_code": int(getattr(svc, "_last_auto_state_code", 0)),
            "auto_status_source": str(getattr(svc, "_last_status_source", "unknown")),
            "auto_start_delay_seconds": float(getattr(svc, "auto_start_delay_seconds", 0.0)),
            "auto_stop_delay_seconds": float(getattr(svc, "auto_stop_delay_seconds", 0.0)),
            "auto_scheduled_enabled_days": str(getattr(svc, "auto_scheduled_enabled_days", "")),
            "auto_scheduled_fallback_delay_seconds": float(
                getattr(svc, "auto_scheduled_night_start_delay_seconds", 0.0)
            ),
            "auto_scheduled_latest_end_time": str(getattr(svc, "auto_scheduled_latest_end_time", "06:30")),
            "auto_scheduled_night_current": float(getattr(svc, "auto_scheduled_night_current_amps", 0.0)),
            "auto_dbus_backoff_base_seconds": float(getattr(svc, "auto_dbus_backoff_base_seconds", 0.0)),
            "auto_dbus_backoff_max_seconds": float(getattr(svc, "auto_dbus_backoff_max_seconds", 0.0)),
        }
        fields.update(policy)
        return fields


def _required_text(source: object, name: str) -> str:
    value = getattr(source, name, None)
    if value is None:
        raise TypeError(f"EVCS identity attribute {name} is missing")
    return str(value)


def accepted_publication_fields(source: Mapping[str, object]) -> dict[str, object]:
    """Copy a semantic registration mapping at test and composition boundaries."""
    return {str(field): value for field, value in source.items()}


__all__ = ["EvcsPublicationRegistrar", "accepted_publication_fields"]
