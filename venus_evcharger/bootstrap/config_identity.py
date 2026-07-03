# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import configparser

from venus_evcharger.bootstrap.config_shared import _config_value
from venus_evcharger.core.controller_contracts import ControllerAssemblyContract


def _host_is_configured(value: object) -> bool:
    """Return whether the primary device host has been configured."""
    return bool(str(value).strip() if value is not None else "")


class _ServiceBootstrapIdentityConfig(ControllerAssemblyContract):
    def _load_identity_config(self, defaults: configparser.SectionProxy) -> None:
        """Load generic device, HTTP, and EV charger presentation settings."""
        svc = self.service
        svc.deviceinstance = int(_config_value(defaults, "DeviceInstance", 60))
        svc.host = defaults["Host"].strip()
        svc.host_configured = _host_is_configured(svc.host)
        svc.phase = self._normalize_phase(defaults.get("Phase", "L1"))
        svc.position = int(_config_value(defaults, "Position", 1))
        svc.poll_interval_ms = int(_config_value(defaults, "PollIntervalMs", 1000))
        svc.sign_of_life_minutes = int(_config_value(defaults, "SignOfLifeLog", 10))
        svc.max_current = float(_config_value(defaults, "MaxCurrent", 16))
        svc.min_current = float(_config_value(defaults, "MinCurrent", 6))
        svc.display_learned_set_current = defaults.get("DisplayLearnedSetCurrent", "1").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        svc.charging_threshold_watts = float(_config_value(defaults, "ChargingThresholdWatts", 100))
        svc.idle_status = int(_config_value(defaults, "IdleStatus", 6))
        svc.voltage_mode = defaults.get("ThreePhaseVoltageMode", "phase").strip().lower()
        svc.username = defaults.get("Username", "").strip()
        svc.password = defaults.get("Password", "").strip()
        svc.use_digest_auth = defaults.get("DigestAuth", "0").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        svc.pm_component = defaults.get("ShellyComponent", "Switch").strip()
        svc.pm_id = int(_config_value(defaults, "ShellyId", 0))
        svc.custom_name_override = defaults.get("Name", "").strip()
        svc.service_name = defaults.get("ServiceName", "com.victronenergy.evcharger").strip()
        svc.connection_name = defaults.get("Connection", "Shelly 1PM Gen4 RPC").strip()
        svc.runtime_state_path = defaults.get(
            "RuntimeStatePath",
            f"/run/dbus-venus-evcharger-{svc.deviceinstance}.json",
        ).strip()
        svc.runtime_overrides_path = defaults.get(
            "RuntimeOverridesPath",
            getattr(svc, "runtime_overrides_path", f"/run/dbus-venus-evcharger-overrides-{svc.deviceinstance}.ini"),
        ).strip()
        svc.control_api_enabled = defaults.get("ControlApiEnabled", "0").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        svc.control_api_host = defaults.get("ControlApiHost", "127.0.0.1").strip() or "127.0.0.1"
        svc.control_api_port = int(_config_value(defaults, "ControlApiPort", 8765))
        svc.control_api_auth_token = defaults.get("ControlApiAuthToken", "").strip()
        svc.control_api_read_token = defaults.get("ControlApiReadToken", "").strip()
        svc.control_api_control_token = defaults.get("ControlApiControlToken", "").strip()
        svc.control_api_admin_token = defaults.get("ControlApiAdminToken", "").strip()
        svc.control_api_update_token = defaults.get("ControlApiUpdateToken", "").strip()
        svc.control_api_audit_path = defaults.get(
            "ControlApiAuditPath",
            f"/run/dbus-venus-evcharger-control-audit-{svc.deviceinstance}.jsonl",
        ).strip()
        svc.control_api_audit_max_entries = int(_config_value(defaults, "ControlApiAuditMaxEntries", 200))
        svc.control_api_idempotency_path = defaults.get(
            "ControlApiIdempotencyPath",
            f"/run/dbus-venus-evcharger-idempotency-{svc.deviceinstance}.json",
        ).strip()
        svc.control_api_idempotency_max_entries = int(_config_value(defaults, "ControlApiIdempotencyMaxEntries", 200))
        svc.control_api_rate_limit_max_requests = int(_config_value(defaults, "ControlApiRateLimitMaxRequests", 30))
        svc.control_api_rate_limit_window_seconds = float(
            _config_value(defaults, "ControlApiRateLimitWindowSeconds", 5.0)
        )
        svc.control_api_critical_cooldown_seconds = float(
            _config_value(defaults, "ControlApiCriticalCooldownSeconds", 2.0)
        )
        svc.control_api_localhost_only = defaults.get("ControlApiLocalhostOnly", "1").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        svc.control_api_unix_socket_path = defaults.get("ControlApiUnixSocketPath", "").strip()
        svc.companion_dbus_bridge_enabled = defaults.get("CompanionDbusBridgeEnabled", "0").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        svc.companion_battery_service_enabled = defaults.get("CompanionBatteryServiceEnabled", "1").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        svc.companion_pvinverter_service_enabled = defaults.get(
            "CompanionPvInverterServiceEnabled",
            "1",
        ).strip().lower() in ("1", "true", "yes", "on")
        svc.companion_grid_service_enabled = defaults.get(
            "CompanionGridServiceEnabled",
            "0",
        ).strip().lower() in ("1", "true", "yes", "on")
        svc.companion_grid_authoritative_source = str(
            _config_value(defaults, "CompanionGridAuthoritativeSource", "")
        ).strip()
        svc.companion_grid_hold_seconds = float(_config_value(defaults, "CompanionGridHoldSeconds", 5.0))
        svc.companion_grid_smoothing_alpha = float(_config_value(defaults, "CompanionGridSmoothingAlpha", 1.0))
        svc.companion_grid_smoothing_max_jump_watts = float(
            _config_value(defaults, "CompanionGridSmoothingMaxJumpWatts", 0.0)
        )
        svc.companion_source_services_enabled = defaults.get(
            "CompanionSourceServicesEnabled",
            "1",
        ).strip().lower() in ("1", "true", "yes", "on")
        svc.companion_source_grid_services_enabled = defaults.get(
            "CompanionSourceGridServicesEnabled",
            "0",
        ).strip().lower() in ("1", "true", "yes", "on")
        svc.companion_source_grid_hold_seconds = float(_config_value(defaults, "CompanionSourceGridHoldSeconds", 5.0))
        svc.companion_source_grid_smoothing_alpha = float(
            _config_value(defaults, "CompanionSourceGridSmoothingAlpha", 1.0)
        )
        svc.companion_source_grid_smoothing_max_jump_watts = float(
            _config_value(defaults, "CompanionSourceGridSmoothingMaxJumpWatts", 0.0)
        )
        svc.companion_battery_deviceinstance = int(
            _config_value(defaults, "CompanionBatteryDeviceInstance", svc.deviceinstance + 40)
        )
        svc.companion_pvinverter_deviceinstance = int(
            _config_value(defaults, "CompanionPvInverterDeviceInstance", svc.deviceinstance + 41)
        )
        svc.companion_grid_deviceinstance = int(
            _config_value(defaults, "CompanionGridDeviceInstance", svc.deviceinstance + 42)
        )
        svc.companion_source_battery_deviceinstance_base = int(
            _config_value(defaults, "CompanionSourceBatteryDeviceInstanceBase", svc.deviceinstance + 140)
        )
        svc.companion_source_pvinverter_deviceinstance_base = int(
            _config_value(defaults, "CompanionSourcePvInverterDeviceInstanceBase", svc.deviceinstance + 240)
        )
        svc.companion_source_grid_deviceinstance_base = int(
            _config_value(defaults, "CompanionSourceGridDeviceInstanceBase", svc.deviceinstance + 340)
        )
        svc.companion_battery_service_name = defaults.get(
            "CompanionBatteryServiceName",
            f"com.victronenergy.battery.external_{svc.companion_battery_deviceinstance}",
        ).strip()
        svc.companion_pvinverter_service_name = defaults.get(
            "CompanionPvInverterServiceName",
            f"com.victronenergy.pvinverter.external_{svc.companion_pvinverter_deviceinstance}",
        ).strip()
        svc.companion_grid_service_name = defaults.get(
            "CompanionGridServiceName",
            f"com.victronenergy.grid.external_{svc.companion_grid_deviceinstance}",
        ).strip()
        svc.companion_source_battery_service_prefix = defaults.get(
            "CompanionSourceBatteryServicePrefix",
            "com.victronenergy.battery.external",
        ).strip()
        svc.companion_source_pvinverter_service_prefix = defaults.get(
            "CompanionSourcePvInverterServicePrefix",
            "com.victronenergy.pvinverter.external",
        ).strip()
        svc.companion_source_grid_service_prefix = defaults.get(
            "CompanionSourceGridServicePrefix",
            "com.victronenergy.grid.external",
        ).strip()
        svc.control_api_listen_host = ""
        svc.control_api_listen_port = 0
        svc.control_api_bound_unix_socket_path = ""
