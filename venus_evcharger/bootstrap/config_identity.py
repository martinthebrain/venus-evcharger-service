# SPDX-License-Identifier: GPL-3.0-or-later
"""Identity, control-API, and companion configuration component."""

from __future__ import annotations

import configparser
from collections.abc import Callable

from venus_evcharger.bootstrap.config_shared import _config_value

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


def _integer_attribute(service: object, name: str) -> int:
    value = getattr(service, name, None)
    if isinstance(value, int):
        return value
    raise TypeError(f"bootstrap service attribute {name} is not an int")


class IdentityConfigLoader:
    """Load presentation, HTTP control, and companion identity settings."""

    def __init__(self, service: object, normalize_phase: Callable[[object], str]) -> None:
        self._service = service
        self._normalize_phase = normalize_phase

    def load(self, defaults: configparser.SectionProxy) -> None:
        """Apply all identity-related configuration groups to the service."""
        self._load_device_identity(defaults)
        self._load_control_api(defaults)
        self._load_companion_features(defaults)
        self._reset_control_api_bindings()

    def _load_device_identity(self, defaults: configparser.SectionProxy) -> None:
        svc = self._service
        instance_id = _config_int(defaults, "DeviceInstance", 60)
        host = _config_text(defaults, "Host")
        setattr(svc, "deviceinstance", instance_id)
        setattr(svc, "host", host)
        setattr(svc, "host_configured", _host_is_configured(host))
        setattr(svc, "phase", self._normalize_phase(defaults.get("Phase", "L1")))
        setattr(svc, "position", _config_int(defaults, "Position", 1))
        setattr(svc, "poll_interval_ms", _config_int(defaults, "PollIntervalMs", 1000))
        setattr(svc, "sign_of_life_minutes", _config_int(defaults, "SignOfLifeLog", 10))
        setattr(svc, "max_current", _config_float(defaults, "MaxCurrent", 16.0))
        setattr(svc, "min_current", _config_float(defaults, "MinCurrent", 6.0))
        setattr(svc, "display_learned_set_current", _config_bool(defaults, "DisplayLearnedSetCurrent", True))
        setattr(svc, "charging_threshold_watts", _config_float(defaults, "ChargingThresholdWatts", 100.0))
        setattr(svc, "idle_status", _config_int(defaults, "IdleStatus", 6))
        setattr(svc, "voltage_mode", _config_lower_text(defaults, "ThreePhaseVoltageMode", "phase"))
        setattr(svc, "username", _config_text(defaults, "Username"))
        setattr(svc, "password", _config_text(defaults, "Password"))
        setattr(svc, "use_digest_auth", _config_bool(defaults, "DigestAuth"))
        setattr(svc, "pm_component", _config_text(defaults, "ShellyComponent", "Switch"))
        setattr(svc, "pm_id", _config_int(defaults, "ShellyId", 0))
        setattr(svc, "custom_name_override", _config_text(defaults, "Name"))
        setattr(svc, "connection_name", _config_text(defaults, "Connection", "Shelly 1PM Gen4 RPC"))
        setattr(
            svc,
            "runtime_state_path",
            _config_text(defaults, "RuntimeStatePath", f"/run/dbus-venus-evcharger-{instance_id}.json"),
        )
        overrides_fallback = str(
            getattr(svc, "runtime_overrides_path", f"/run/dbus-venus-evcharger-overrides-{instance_id}.ini")
        )
        setattr(
            svc,
            "runtime_overrides_path",
            _config_text(defaults, "RuntimeOverridesPath", overrides_fallback),
        )

    def _load_control_api(self, defaults: configparser.SectionProxy) -> None:
        svc = self._service
        deviceinstance = _integer_attribute(svc, "deviceinstance")
        setattr(svc, "control_api_enabled", _config_bool(defaults, "ControlApiEnabled"))
        setattr(svc, "control_api_host", _config_text(defaults, "ControlApiHost") or "127.0.0.1")
        setattr(svc, "control_api_port", _config_int(defaults, "ControlApiPort", 8765))
        for attribute, key in (
            ("control_api_auth_token", "ControlApiAuthToken"),
            ("control_api_read_token", "ControlApiReadToken"),
            ("control_api_control_token", "ControlApiControlToken"),
            ("control_api_admin_token", "ControlApiAdminToken"),
            ("control_api_update_token", "ControlApiUpdateToken"),
        ):
            setattr(svc, attribute, _config_text(defaults, key))
        setattr(
            svc,
            "control_api_audit_path",
            _config_text(
                defaults,
                "ControlApiAuditPath",
                f"/run/dbus-venus-evcharger-control-audit-{deviceinstance}.jsonl",
            ),
        )
        setattr(svc, "control_api_audit_max_entries", _config_int(defaults, "ControlApiAuditMaxEntries", 200))
        setattr(
            svc,
            "control_api_idempotency_path",
            _config_text(
                defaults,
                "ControlApiIdempotencyPath",
                f"/run/dbus-venus-evcharger-idempotency-{deviceinstance}.json",
            ),
        )
        setattr(
            svc,
            "control_api_idempotency_max_entries",
            _config_int(defaults, "ControlApiIdempotencyMaxEntries", 200),
        )
        setattr(
            svc,
            "control_api_rate_limit_max_requests",
            _config_int(defaults, "ControlApiRateLimitMaxRequests", 30),
        )
        setattr(
            svc,
            "control_api_rate_limit_window_seconds",
            _config_float(defaults, "ControlApiRateLimitWindowSeconds", 5.0),
        )
        setattr(
            svc,
            "control_api_critical_cooldown_seconds",
            _config_float(defaults, "ControlApiCriticalCooldownSeconds", 2.0),
        )
        setattr(svc, "control_api_localhost_only", _config_bool(defaults, "ControlApiLocalhostOnly", True))
        setattr(svc, "control_api_unix_socket_path", _config_text(defaults, "ControlApiUnixSocketPath"))

    def _load_companion_features(self, defaults: configparser.SectionProxy) -> None:
        svc = self._service
        bool_values = (
            ("companion_publication_enabled", "CompanionDbusBridgeEnabled", False),
            ("companion_battery_service_enabled", "CompanionBatteryServiceEnabled", True),
            ("companion_pvinverter_service_enabled", "CompanionPvInverterServiceEnabled", True),
            ("companion_grid_service_enabled", "CompanionGridServiceEnabled", False),
            ("companion_source_services_enabled", "CompanionSourceServicesEnabled", True),
            ("companion_source_grid_services_enabled", "CompanionSourceGridServicesEnabled", False),
        )
        for attribute, key, bool_fallback in bool_values:
            setattr(svc, attribute, _config_bool(defaults, key, bool_fallback))
        setattr(
            svc,
            "companion_grid_authoritative_source",
            _config_text(defaults, "CompanionGridAuthoritativeSource"),
        )
        float_values = (
            ("companion_grid_hold_seconds", "CompanionGridHoldSeconds", 5.0),
            ("companion_grid_smoothing_alpha", "CompanionGridSmoothingAlpha", 1.0),
            ("companion_grid_smoothing_max_jump_watts", "CompanionGridSmoothingMaxJumpWatts", 0.0),
            ("companion_source_grid_hold_seconds", "CompanionSourceGridHoldSeconds", 5.0),
            ("companion_source_grid_smoothing_alpha", "CompanionSourceGridSmoothingAlpha", 1.0),
            (
                "companion_source_grid_smoothing_max_jump_watts",
                "CompanionSourceGridSmoothingMaxJumpWatts",
                0.0,
            ),
        )
        for attribute, key, float_fallback in float_values:
            setattr(svc, attribute, _config_float(defaults, key, float_fallback))

    def _reset_control_api_bindings(self) -> None:
        setattr(self._service, "control_api_listen_host", "")
        setattr(self._service, "control_api_listen_port", 0)
        setattr(self._service, "control_api_bound_unix_socket_path", "")
