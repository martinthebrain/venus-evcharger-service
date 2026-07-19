# SPDX-License-Identifier: GPL-3.0-or-later
"""Registry of backend type names to runtime constructors."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeVar

from .base import ChargerBackend, MeterBackend, SwitchBackend
from .cerbo_gx_relay_switch import CerboGxRelaySwitchBackend
from .goe_charger import GoEChargerBackend
from .modbus_charger import ModbusChargerBackend
from .registry_contracts import BackendConstructor, SwitchBackendFactory
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

BackendT = TypeVar("BackendT")


def _create_switch_group_child_backend(
    backend_type: str,
    service: object,
    config_path: str = "",
) -> SwitchBackend:
    """Create one switch-group child with the group's role-specific error contract."""
    return _create_backend(
        SWITCH_BACKENDS,
        backend_type,
        service,
        config_path,
        "switch-group child",
    )


def _create_switch_group_backend(service: object, *, config_path: str = "") -> SwitchBackend:
    """Compose a switch group with the registry-owned child factory."""
    child_factory: SwitchBackendFactory = _create_switch_group_child_backend
    return SwitchGroupBackend(
        service,
        config_path=config_path,
        child_backend_factory=child_factory,
    )


METER_BACKENDS: dict[str, BackendConstructor[MeterBackend]] = {
    "shelly_meter": ShellyMeterBackend,
    "tasmota_meter": TasmotaMeterBackend,
    "template_meter": TemplateMeterBackend,
    "tuya_meter": TuyaMeterBackend,
}

SWITCH_BACKENDS: dict[str, BackendConstructor[SwitchBackend]] = {
    "cerbo_gx_relay_switch": CerboGxRelaySwitchBackend,
    "shelly_switch": ShellySwitchBackend,
    "shelly_contactor_switch": ShellyContactorSwitchBackend,
    "switch_group": _create_switch_group_backend,
    "tasmota_contactor_switch": TasmotaContactorSwitchBackend,
    "tasmota_switch": TasmotaSwitchBackend,
    "template_switch": TemplateSwitchBackend,
    "tuya_contactor_switch": TuyaContactorSwitchBackend,
    "tuya_switch": TuyaSwitchBackend,
}

CHARGER_BACKENDS: dict[str, BackendConstructor[ChargerBackend]] = {
    "goe_charger": GoEChargerBackend,
    "modbus_charger": ModbusChargerBackend,
    "smartevse_charger": SmartEvseChargerBackend,
    "simpleevse_charger": SimpleEvseChargerBackend,
    "template_charger": TemplateChargerBackend,
}


def _create_backend(
    registry: Mapping[str, BackendConstructor[BackendT]],
    backend_type: str,
    service: object,
    config_path: str,
    role: str,
) -> BackendT:
    """Instantiate one backend from the matching registry."""
    normalized_type = str(backend_type).strip().lower()
    constructor = registry.get(normalized_type)
    if constructor is None:
        raise ValueError(f"Unsupported {role} backend '{backend_type}'")
    return constructor(service, config_path=config_path)


def create_meter_backend(backend_type: str, service: object, config_path: str = "") -> MeterBackend:
    return _create_backend(METER_BACKENDS, backend_type, service, config_path, "meter")


def create_switch_backend(backend_type: str, service: object, config_path: str = "") -> SwitchBackend:
    return _create_backend(SWITCH_BACKENDS, backend_type, service, config_path, "switch")


def create_charger_backend(backend_type: str, service: object, config_path: str = "") -> ChargerBackend:
    return _create_backend(CHARGER_BACKENDS, backend_type, service, config_path, "charger")
