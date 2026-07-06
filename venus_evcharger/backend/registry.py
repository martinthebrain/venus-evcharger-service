# SPDX-License-Identifier: GPL-3.0-or-later
"""Registry of backend type names to runtime constructors."""

from __future__ import annotations

from typing import Any

from .base import BackendConstructor
from .cerbo_gx_relay_switch import CerboGxRelaySwitchBackend
from .goe_charger import GoEChargerBackend
from .modbus_charger import ModbusChargerBackend
from .shelly_contactor_switch import ShellyContactorSwitchBackend
from .shelly_meter import ShellyMeterBackend
from .smartevse_charger import SmartEvseChargerBackend
from .simpleevse_charger import SimpleEvseChargerBackend
from .shelly_switch import ShellySwitchBackend
from .switch_group import SwitchGroupBackend
from .tasmota_meter import TasmotaMeterBackend
from .tasmota_switch import TasmotaContactorSwitchBackend, TasmotaSwitchBackend
from .template_charger import TemplateChargerBackend
from .template_meter import TemplateMeterBackend
from .template_switch import TemplateSwitchBackend
from .tuya_meter import TuyaMeterBackend
from .tuya_switch import TuyaContactorSwitchBackend, TuyaSwitchBackend


METER_BACKENDS: dict[str, BackendConstructor] = {
    "shelly_meter": ShellyMeterBackend,
    "tasmota_meter": TasmotaMeterBackend,
    "template_meter": TemplateMeterBackend,
    "tuya_meter": TuyaMeterBackend,
}

SWITCH_BACKENDS: dict[str, BackendConstructor] = {
    "cerbo_gx_relay_switch": CerboGxRelaySwitchBackend,
    "shelly_switch": ShellySwitchBackend,
    "shelly_contactor_switch": ShellyContactorSwitchBackend,
    "switch_group": SwitchGroupBackend,
    "tasmota_contactor_switch": TasmotaContactorSwitchBackend,
    "tasmota_switch": TasmotaSwitchBackend,
    "template_switch": TemplateSwitchBackend,
    "tuya_contactor_switch": TuyaContactorSwitchBackend,
    "tuya_switch": TuyaSwitchBackend,
}

CHARGER_BACKENDS: dict[str, BackendConstructor] = {
    "goe_charger": GoEChargerBackend,
    "modbus_charger": ModbusChargerBackend,
    "smartevse_charger": SmartEvseChargerBackend,
    "simpleevse_charger": SimpleEvseChargerBackend,
    "template_charger": TemplateChargerBackend,
}


def _create_backend(
    registry: dict[str, BackendConstructor],
    backend_type: str,
    service: Any,
    config_path: str,
    role: str,
) -> Any:
    """Instantiate one backend from the matching registry."""
    normalized_type = str(backend_type).strip().lower()
    constructor = registry.get(normalized_type)
    if constructor is None:
        raise ValueError(f"Unsupported {role} backend '{backend_type}'")
    return constructor(service, config_path=config_path)


def create_meter_backend(backend_type: str, service: Any, config_path: str = "") -> Any:
    return _create_backend(METER_BACKENDS, backend_type, service, config_path, "meter")


def create_switch_backend(backend_type: str, service: Any, config_path: str = "") -> Any:
    return _create_backend(SWITCH_BACKENDS, backend_type, service, config_path, "switch")


def create_charger_backend(backend_type: str, service: Any, config_path: str = "") -> Any:
    return _create_backend(CHARGER_BACKENDS, backend_type, service, config_path, "charger")
