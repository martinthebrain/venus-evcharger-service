# SPDX-License-Identifier: GPL-3.0-or-later
"""CLI helper to validate and probe wallbox backend adapter configs.

The probe tool gives contributors and operators a safe way to answer practical
questions such as:

- does this config file parse and match the intended backend role?
- does the composed wallbox topology resolve correctly?
- what does a backend expose before the full service is started?
- can one live charger read be performed without bringing up the whole service?

That makes this module one of the most useful bridges between configuration
work, backend development, and field troubleshooting.
"""

from __future__ import annotations

import argparse
import configparser
import json
from collections.abc import Callable
from dataclasses import fields, is_dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import requests

from .config import backend_selection_view
from .config_file import load_required_backend_config
from .factory import build_service_backends
from .registry import CHARGER_BACKENDS, METER_BACKENDS, SWITCH_BACKENDS
from venus_evcharger.dbus_introspection import load_introspection_snapshot

_ADAPTER_SECTION = "Adapter"
_DEFAULT_SECTION = "DEFAULT"
_LEGACY_ADAPTER_TYPE = "shelly_combined"
_TYPE_KEY = "type"
_HOST_KEY = "host"
_USERNAME_KEY = "username"
_PASSWORD_KEY = "password"
_DIGEST_AUTH_KEY = "digestauth"
_SHELLY_TIMEOUT_KEY = "shellyrequesttimeoutseconds"
_SHELLY_COMPONENT_KEY = "shellycomponent"
_SHELLY_ID_KEY = "shellyid"
_PHASE_KEY = "phase"
_MAX_CURRENT_KEY = "maxcurrent"
_DEVICE_INSTANCE_KEY = "deviceinstance"
_DBUS_INTROSPECTION_ENABLED_KEY = "dbusintrospectionenabled"
_DBUS_INTROSPECTION_SNAPSHOT_PATH_KEY = "dbusintrospectionsnapshotpath"
_DBUS_INTROSPECTION_MAX_AGE_KEY = "dbusintrospectionmaxageseconds"
_TRUTHY_TEXTS = frozenset(("1", "true", "yes", "on"))


def _config(path: str) -> configparser.ConfigParser:
    """Load one backend config file."""
    return load_required_backend_config(path, "backend probe")


def _adapter_type(path: str) -> str:
    """Return one normalized adapter type from config."""
    parser = _config(path)
    if parser.has_section(_ADAPTER_SECTION):
        return _normalized_adapter_type(_section_option_text(parser[_ADAPTER_SECTION], _TYPE_KEY))
    return _normalized_adapter_type(_section_option_text(parser[_DEFAULT_SECTION], _TYPE_KEY))


def _normalized_adapter_type(raw: str) -> str:
    """Return one normalized adapter type, preserving the legacy fallback when omitted."""
    return raw.lower() if raw else _LEGACY_ADAPTER_TYPE


def _section_option_text(section: configparser.SectionProxy, option_lower: str, default: str = "") -> str:
    """Return one stripped config option using case-insensitive ConfigParser lookup semantics."""
    for key, value in section.items():
        if key.strip().lower() == option_lower:
            return str(value).strip()
    return default


def _section_option_bool(section: configparser.SectionProxy, option_lower: str, default: bool = False) -> bool:
    """Return one normalized boolean config option."""
    raw = _section_option_text(section, option_lower)
    return default if raw == "" else raw.lower() in _TRUTHY_TEXTS


def _section_option_float(section: configparser.SectionProxy, option_lower: str, default: float) -> float:
    """Return one normalized float config option."""
    raw = _section_option_text(section, option_lower)
    return default if not raw else float(raw)


def _section_option_int(section: configparser.SectionProxy, option_lower: str, default: int) -> int:
    """Return one normalized integer config option."""
    raw = _section_option_text(section, option_lower)
    return default if not raw else int(raw)


def _probe_service() -> Any:
    """Return one small service stub for standalone backend probing.

    Backends expect a service-like host object with a handful of shared
    attributes. The probe CLI supplies the smallest practical host surface so a
    backend can be instantiated outside the full Venus runtime.
    """
    return SimpleNamespace(
        session=requests.Session(),
        host="",
        username="",
        password="",
        use_digest_auth=False,
        shelly_request_timeout_seconds=2.0,
        pm_component="Switch",
        pm_id=0,
        phase="L1",
        max_current=16.0,
        _last_voltage=None,
    )


def _probe_service_from_wallbox_config(config: configparser.ConfigParser) -> Any:
    """Return one small service stub seeded from a full wallbox config file."""
    defaults = config["DEFAULT"]
    return SimpleNamespace(
        config=config,
        session=requests.Session(),
        host=_section_option_text(defaults, _HOST_KEY),
        username=_section_option_text(defaults, _USERNAME_KEY),
        password=_section_option_text(defaults, _PASSWORD_KEY),
        use_digest_auth=_section_option_bool(defaults, _DIGEST_AUTH_KEY),
        shelly_request_timeout_seconds=_section_option_float(defaults, _SHELLY_TIMEOUT_KEY, 2.0),
        pm_component=_section_option_text(defaults, _SHELLY_COMPONENT_KEY, "Switch"),
        pm_id=_section_option_int(defaults, _SHELLY_ID_KEY, 0),
        phase=_section_option_text(defaults, _PHASE_KEY, "L1"),
        max_current=_section_option_float(defaults, _MAX_CURRENT_KEY, 16.0),
        _last_voltage=None,
    )


def _json_ready(value: Any) -> Any:
    """Convert dataclasses recursively to JSON-friendly structures."""
    if is_dataclass(value) and not isinstance(value, type):
        return _json_ready_dataclass(value)
    return _json_ready_container(value)


def _json_ready_dataclass(value: Any) -> dict[str, Any]:
    """Convert one dataclass instance to a JSON-friendly dict."""
    return _json_ready_mapping({field.name: getattr(value, field.name) for field in fields(value)})


def _json_ready_container(value: Any) -> Any:
    """Convert non-dataclass containers to JSON-friendly values."""
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return _json_ready_mapping(value)
    if isinstance(value, (list, tuple)):
        return _json_ready_sequence(value)
    return value


def _json_ready_mapping(value: dict[Any, Any]) -> dict[str, Any]:
    """Convert JSON object payloads recursively to stable string-keyed dicts."""
    return {str(key): _json_ready(item) for key, item in value.items()}


def _json_ready_sequence(value: list[Any] | tuple[Any, ...]) -> list[Any]:
    """Convert JSON arrays recursively to plain Python lists."""
    return [_json_ready(item) for item in value]


def validate_backend_config(path: str) -> dict[str, object]:
    """Validate backend config type and role compatibility without network I/O.

    This is the fastest safety check for a single adapter file. It proves that
    the file can be parsed, that its adapter type is known, and that the file
    is accepted by the backend constructor for the intended role.
    """
    adapter_type = _adapter_type(path)
    valid_roles: list[str] = []
    if adapter_type in METER_BACKENDS:
        METER_BACKENDS[adapter_type](_probe_service(), config_path=path)
        valid_roles.append("meter")
    if adapter_type in SWITCH_BACKENDS:
        SWITCH_BACKENDS[adapter_type](_probe_service(), config_path=path)
        valid_roles.append("switch")
    if adapter_type in CHARGER_BACKENDS:
        CHARGER_BACKENDS[adapter_type](_probe_service(), config_path=path)
        valid_roles.append("charger")
    if not valid_roles:
        raise ValueError(f"Unsupported backend type '{adapter_type}'")
    return {
        "path": path,
        "type": adapter_type,
        "roles": valid_roles,
    }


def validate_wallbox_config(path: str) -> dict[str, object]:
    """Validate one full wallbox config including backend selection compatibility.

    This command is especially useful for composed installations because it
    resolves the meter/switch/charger selection as the full service would see
    it, while still staying outside live device I/O.
    """
    config = _config(path)
    service = _probe_service_from_wallbox_config(config)
    resolved = build_service_backends(service)
    defaults = config["DEFAULT"]
    return {
        "path": path,
        "runtime": _json_ready(resolved.runtime),
        "selection": _json_ready(backend_selection_view(resolved.runtime)),
        "resolved_roles": {
            "meter": resolved.meter is not None,
            "switch": resolved.switch is not None,
            "charger": resolved.charger is not None,
        },
        "dbus_introspection": _dbus_introspection_probe_summary(defaults),
    }


def _dbus_introspection_probe_summary(defaults: configparser.SectionProxy) -> dict[str, object]:
    deviceinstance = int(_section_option_float(defaults, _DEVICE_INSTANCE_KEY, 60.0))
    snapshot_path = _dbus_introspection_snapshot_path(defaults, deviceinstance)
    snapshot = _dbus_introspection_snapshot(defaults, snapshot_path)
    services = snapshot.get("services") if isinstance(snapshot, dict) else None
    return {
        "enabled": _dbus_introspection_enabled(defaults),
        "snapshot_path": snapshot_path,
        "snapshot_fresh": bool(snapshot),
        "worker_state": _dbus_introspection_gateway_state(snapshot),
        "queue_depth": _dbus_introspection_queue_depth(snapshot),
        "service_count": _dbus_introspection_service_count(services),
    }


def _dbus_introspection_snapshot_path(defaults: configparser.SectionProxy, deviceinstance: int) -> str:
    default_path = f"/run/dbus-venus-evcharger-dbus-map-{deviceinstance}.json"
    return _section_option_text(defaults, _DBUS_INTROSPECTION_SNAPSHOT_PATH_KEY, default_path)


def _dbus_introspection_snapshot(defaults: configparser.SectionProxy, snapshot_path: str) -> dict[str, object]:
    max_age_seconds = _section_option_float(defaults, _DBUS_INTROSPECTION_MAX_AGE_KEY, 900.0)
    return load_introspection_snapshot(snapshot_path, max_age_seconds=max_age_seconds)


def _dbus_introspection_enabled(defaults: configparser.SectionProxy) -> bool:
    return _section_option_bool(defaults, _DBUS_INTROSPECTION_ENABLED_KEY, True)


def _dbus_introspection_gateway_state(snapshot: object) -> str:
    state = snapshot.get("worker_state") if isinstance(snapshot, dict) else None
    return "" if state is None else str(state)


def _dbus_introspection_queue_depth(snapshot: object) -> int:
    if not isinstance(snapshot, dict):
        return 0
    depth = snapshot.get("queue_depth")
    return int(depth) if depth is not None else 0


def _dbus_introspection_service_count(services: object) -> int:
    return len(services) if isinstance(services, dict) else 0


def probe_meter_backend(path: str) -> dict[str, object]:
    """Read one sample meter result from the configured backend."""
    adapter_type = _adapter_type(path)
    constructor = METER_BACKENDS.get(adapter_type)
    if constructor is None:
        raise ValueError(f"Backend type '{adapter_type}' is not a meter backend")
    backend = constructor(_probe_service(), config_path=path)
    settings = getattr(backend, "settings", None)
    return {
        "path": path,
        "type": adapter_type,
        "shelly_profile": getattr(settings, "profile_name", None),
        "component": getattr(settings, "component", None),
        "device_id": getattr(settings, "device_id", None),
        "meter": _json_ready(backend.read_meter()),
    }


def probe_switch_backend(path: str) -> dict[str, object]:
    """Read one sample switch state and capabilities from the configured backend."""
    adapter_type = _adapter_type(path)
    constructor = SWITCH_BACKENDS.get(adapter_type)
    if constructor is None:
        raise ValueError(f"Backend type '{adapter_type}' is not a switch backend")
    backend = constructor(_probe_service(), config_path=path)
    settings = getattr(backend, "settings", None)
    return {
        "path": path,
        "type": adapter_type,
        "shelly_profile": getattr(settings, "profile_name", None),
        "component": getattr(settings, "component", None),
        "device_id": getattr(settings, "device_id", None),
        "capabilities": _json_ready(backend.capabilities()),
        "phase_switch_targets": _json_ready(getattr(settings, "phase_switch_targets", {})),
        "phase_members": _json_ready(getattr(settings, "phase_members", {})),
        "feedback_readback": _json_ready(getattr(settings, "feedback_readback", None)),
        "interlock_readback": _json_ready(getattr(settings, "interlock_readback", None)),
        "switch_state": _json_ready(backend.read_switch_state()),
    }


def probe_charger_backend(path: str) -> dict[str, object]:
    """Return normalized non-destructive charger backend config details."""
    adapter_type = _adapter_type(path)
    constructor = CHARGER_BACKENDS.get(adapter_type)
    if constructor is None:
        raise ValueError(f"Backend type '{adapter_type}' is not a charger backend")
    backend = constructor(_probe_service(), config_path=path)
    settings = getattr(backend, "settings", None)
    return {
        "path": path,
        "type": adapter_type,
        "profile_name": getattr(settings, "profile_name", None),
        "transport_kind": getattr(getattr(settings, "transport_settings", None), "transport_kind", None),
        "transport_unit_id": getattr(getattr(settings, "transport_settings", None), "unit_id", None),
        "transport_device": getattr(getattr(settings, "transport_settings", None), "device", None),
        "transport_timeout_seconds": getattr(getattr(settings, "transport_settings", None), "timeout_seconds", None),
        "transport_serial_port_owner": getattr(
            getattr(settings, "transport_settings", None), "serial_port_owner", None
        ),
        "transport_serial_retry_count": getattr(
            getattr(settings, "transport_settings", None), "serial_retry_count", None
        ),
        "transport_serial_retry_delay_seconds": getattr(
            getattr(settings, "transport_settings", None), "serial_retry_delay_seconds", None
        ),
        "supported_phase_selections": _json_ready(
            getattr(settings, "supported_phase_selections", ("P1",))
        ),
        "state_url": getattr(settings, "state_url", None),
        "state_actual_current_path": getattr(settings, "state_actual_current_path", None),
        "state_power_watts_path": getattr(settings, "state_power_watts_path", None),
        "state_energy_kwh_path": getattr(settings, "state_energy_kwh_path", None),
        "state_status_path": getattr(settings, "state_status_path", None),
        "state_fault_path": getattr(settings, "state_fault_path", None),
        "enable_url": getattr(settings, "enable_url", None),
        "current_url": getattr(settings, "current_url", None),
        "phase_url": getattr(settings, "phase_url", None),
    }


def read_charger_backend(path: str) -> dict[str, object]:
    """Read one live charger-state sample through the configured backend.

    This is the most runtime-near probe mode. It keeps the full service out of
    the picture and answers the concrete question "what state does this charger
    backend return right now?".
    """
    payload = probe_charger_backend(path)
    adapter_type = _adapter_type(path)
    constructor = CHARGER_BACKENDS.get(adapter_type)
    if constructor is None:
        raise ValueError(f"Backend type '{adapter_type}' is not a charger backend")
    backend = constructor(_probe_service(), config_path=path)
    payload["charger_state"] = _json_ready(backend.read_charger_state())
    return payload


def main(argv: list[str] | None = None) -> int:
    """Run the backend probe CLI."""
    parser = argparse.ArgumentParser(description="Validate or probe wallbox backend configs")
    parser.add_argument(
        "command",
        choices=("validate", "validate-wallbox", "probe-meter", "probe-switch", "probe-charger", "read-charger"),
    )
    parser.add_argument("config_path")
    args = parser.parse_args(argv)
    payload = _probe_command_payload(args.command, args.config_path)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _probe_command_payload(command: str, config_path: str) -> dict[str, object]:
    """Return the payload for one probe CLI subcommand."""
    handlers: dict[str, Callable[[str], object]] = {
        "validate": validate_backend_config,
        "validate-wallbox": validate_wallbox_config,
        "probe-meter": probe_meter_backend,
        "probe-switch": probe_switch_backend,
        "probe-charger": probe_charger_backend,
        "read-charger": read_charger_backend,
    }
    payload = handlers[command](config_path)
    if not isinstance(payload, dict):
        raise TypeError(f"Probe command {command!r} must return dict, got {type(payload).__name__}")
    return {str(key): value for key, value in payload.items()}


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
