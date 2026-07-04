# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import configparser

from venus_evcharger.bootstrap.config_shared import _config_value
from venus_evcharger.core.controller_contracts import ControllerAssemblyContract

_TRUE_CONFIG_VALUES = frozenset(("1", "true", "yes", "on"))


def _host_is_configured(value: object) -> bool:
    """Return whether the primary device host has been configured."""
    return bool(str(value).strip() if value is not None else "")


def _config_text(defaults: configparser.SectionProxy, key: str, fallback: str = "") -> str:
    return defaults.get(key, fallback).strip()


def _config_bool(defaults: configparser.SectionProxy, key: str, fallback: bool = False) -> bool:
    if key not in defaults:
        return fallback
    return _config_text(defaults, key).lower() in _TRUE_CONFIG_VALUES


def _config_lower_text(defaults: configparser.SectionProxy, key: str, fallback: str) -> str:
    if key not in defaults:
        return fallback
    return _config_text(defaults, key).lower()


def _config_int(defaults: configparser.SectionProxy, key: str, fallback: int) -> int:
    return int(_config_value(defaults, key, fallback))


def _config_float(defaults: configparser.SectionProxy, key: str, fallback: float) -> float:
    return float(_config_value(defaults, key, fallback))


class _ServiceBootstrapIdentityConfig(ControllerAssemblyContract):
    def _load_identity_config(self, defaults: configparser.SectionProxy) -> None:
        """Load generic device, HTTP, and EV charger presentation settings."""
        self._load_device_identity_config(defaults)
        self._load_control_api_config(defaults)
        self._load_companion_feature_config(defaults)
        self._load_companion_device_instances(defaults)
        self._load_companion_service_names(defaults)
        self._reset_control_api_runtime_bindings()

    def _load_device_identity_config(self, defaults: configparser.SectionProxy) -> None:
        svc = self.service
        svc.deviceinstance = _config_int(defaults, "DeviceInstance", 60)
        svc.host = _config_text(defaults, "Host")
        svc.host_configured = _host_is_configured(svc.host)
        svc.phase = self._normalize_phase(defaults.get("Phase", "L1"))
        svc.position = _config_int(defaults, "Position", 1)
        svc.poll_interval_ms = _config_int(defaults, "PollIntervalMs", 1000)
        svc.sign_of_life_minutes = _config_int(defaults, "SignOfLifeLog", 10)
        svc.max_current = _config_float(defaults, "MaxCurrent", 16.0)
        svc.min_current = _config_float(defaults, "MinCurrent", 6.0)
        svc.display_learned_set_current = _config_bool(defaults, "DisplayLearnedSetCurrent", True)
        svc.charging_threshold_watts = _config_float(defaults, "ChargingThresholdWatts", 100.0)
        svc.idle_status = _config_int(defaults, "IdleStatus", 6)
        svc.voltage_mode = _config_lower_text(defaults, "ThreePhaseVoltageMode", "phase")
        svc.username = _config_text(defaults, "Username")
        svc.password = _config_text(defaults, "Password")
        svc.use_digest_auth = _config_bool(defaults, "DigestAuth")
        svc.pm_component = _config_text(defaults, "ShellyComponent", "Switch")
        svc.pm_id = _config_int(defaults, "ShellyId", 0)
        svc.custom_name_override = _config_text(defaults, "Name")
        svc.service_name = _config_text(defaults, "ServiceName", "com.victronenergy.evcharger")
        svc.connection_name = _config_text(defaults, "Connection", "Shelly 1PM Gen4 RPC")
        svc.runtime_state_path = _config_text(
            defaults,
            "RuntimeStatePath",
            f"/run/dbus-venus-evcharger-{svc.deviceinstance}.json",
        )
        svc.runtime_overrides_path = _config_text(
            defaults,
            "RuntimeOverridesPath",
            getattr(svc, "runtime_overrides_path", f"/run/dbus-venus-evcharger-overrides-{svc.deviceinstance}.ini"),
        )

    def _load_control_api_config(self, defaults: configparser.SectionProxy) -> None:
        svc = self.service
        svc.control_api_enabled = _config_bool(defaults, "ControlApiEnabled")
        svc.control_api_host = _config_text(defaults, "ControlApiHost") or "127.0.0.1"
        svc.control_api_port = _config_int(defaults, "ControlApiPort", 8765)
        svc.control_api_auth_token = _config_text(defaults, "ControlApiAuthToken")
        svc.control_api_read_token = _config_text(defaults, "ControlApiReadToken")
        svc.control_api_control_token = _config_text(defaults, "ControlApiControlToken")
        svc.control_api_admin_token = _config_text(defaults, "ControlApiAdminToken")
        svc.control_api_update_token = _config_text(defaults, "ControlApiUpdateToken")
        svc.control_api_audit_path = _config_text(
            defaults,
            "ControlApiAuditPath",
            f"/run/dbus-venus-evcharger-control-audit-{svc.deviceinstance}.jsonl",
        )
        svc.control_api_audit_max_entries = _config_int(defaults, "ControlApiAuditMaxEntries", 200)
        svc.control_api_idempotency_path = _config_text(
            defaults,
            "ControlApiIdempotencyPath",
            f"/run/dbus-venus-evcharger-idempotency-{svc.deviceinstance}.json",
        )
        svc.control_api_idempotency_max_entries = _config_int(defaults, "ControlApiIdempotencyMaxEntries", 200)
        svc.control_api_rate_limit_max_requests = _config_int(defaults, "ControlApiRateLimitMaxRequests", 30)
        svc.control_api_rate_limit_window_seconds = _config_float(defaults, "ControlApiRateLimitWindowSeconds", 5.0)
        svc.control_api_critical_cooldown_seconds = _config_float(defaults, "ControlApiCriticalCooldownSeconds", 2.0)
        svc.control_api_localhost_only = _config_bool(defaults, "ControlApiLocalhostOnly", True)
        svc.control_api_unix_socket_path = _config_text(defaults, "ControlApiUnixSocketPath")

    def _load_companion_feature_config(self, defaults: configparser.SectionProxy) -> None:
        svc = self.service
        svc.companion_dbus_bridge_enabled = _config_bool(defaults, "CompanionDbusBridgeEnabled")
        svc.companion_battery_service_enabled = _config_bool(defaults, "CompanionBatteryServiceEnabled", True)
        svc.companion_pvinverter_service_enabled = _config_bool(defaults, "CompanionPvInverterServiceEnabled", True)
        svc.companion_grid_service_enabled = _config_bool(defaults, "CompanionGridServiceEnabled")
        svc.companion_grid_authoritative_source = _config_text(defaults, "CompanionGridAuthoritativeSource")
        svc.companion_grid_hold_seconds = _config_float(defaults, "CompanionGridHoldSeconds", 5.0)
        svc.companion_grid_smoothing_alpha = _config_float(defaults, "CompanionGridSmoothingAlpha", 1.0)
        svc.companion_grid_smoothing_max_jump_watts = _config_float(
            defaults,
            "CompanionGridSmoothingMaxJumpWatts",
            0.0,
        )
        svc.companion_source_services_enabled = _config_bool(defaults, "CompanionSourceServicesEnabled", True)
        svc.companion_source_grid_services_enabled = _config_bool(defaults, "CompanionSourceGridServicesEnabled")
        svc.companion_source_grid_hold_seconds = _config_float(defaults, "CompanionSourceGridHoldSeconds", 5.0)
        svc.companion_source_grid_smoothing_alpha = _config_float(
            defaults,
            "CompanionSourceGridSmoothingAlpha",
            1.0,
        )
        svc.companion_source_grid_smoothing_max_jump_watts = _config_float(
            defaults,
            "CompanionSourceGridSmoothingMaxJumpWatts",
            0.0,
        )

    def _load_companion_device_instances(self, defaults: configparser.SectionProxy) -> None:
        svc = self.service
        svc.companion_battery_deviceinstance = _config_int(
            defaults,
            "CompanionBatteryDeviceInstance",
            svc.deviceinstance + 40,
        )
        svc.companion_pvinverter_deviceinstance = _config_int(
            defaults,
            "CompanionPvInverterDeviceInstance",
            svc.deviceinstance + 41,
        )
        svc.companion_grid_deviceinstance = _config_int(
            defaults,
            "CompanionGridDeviceInstance",
            svc.deviceinstance + 42,
        )
        svc.companion_source_battery_deviceinstance_base = _config_int(
            defaults,
            "CompanionSourceBatteryDeviceInstanceBase",
            svc.deviceinstance + 140,
        )
        svc.companion_source_pvinverter_deviceinstance_base = _config_int(
            defaults,
            "CompanionSourcePvInverterDeviceInstanceBase",
            svc.deviceinstance + 240,
        )
        svc.companion_source_grid_deviceinstance_base = _config_int(
            defaults,
            "CompanionSourceGridDeviceInstanceBase",
            svc.deviceinstance + 340,
        )

    def _load_companion_service_names(self, defaults: configparser.SectionProxy) -> None:
        svc = self.service
        svc.companion_battery_service_name = _config_text(
            defaults,
            "CompanionBatteryServiceName",
            f"com.victronenergy.battery.external_{svc.companion_battery_deviceinstance}",
        )
        svc.companion_pvinverter_service_name = _config_text(
            defaults,
            "CompanionPvInverterServiceName",
            f"com.victronenergy.pvinverter.external_{svc.companion_pvinverter_deviceinstance}",
        )
        svc.companion_grid_service_name = _config_text(
            defaults,
            "CompanionGridServiceName",
            f"com.victronenergy.grid.external_{svc.companion_grid_deviceinstance}",
        )
        svc.companion_source_battery_service_prefix = _config_text(
            defaults,
            "CompanionSourceBatteryServicePrefix",
            "com.victronenergy.battery.external",
        )
        svc.companion_source_pvinverter_service_prefix = _config_text(
            defaults,
            "CompanionSourcePvInverterServicePrefix",
            "com.victronenergy.pvinverter.external",
        )
        svc.companion_source_grid_service_prefix = _config_text(
            defaults,
            "CompanionSourceGridServicePrefix",
            "com.victronenergy.grid.external",
        )

    def _reset_control_api_runtime_bindings(self) -> None:
        svc = self.service
        svc.control_api_listen_host = ""
        svc.control_api_listen_port = 0
        svc.control_api_bound_unix_socket_path = ""
