# SPDX-License-Identifier: GPL-3.0-or-later
"""Connector registry facade for external energy-source transports."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
import json
import subprocess

from venus_evcharger.backend.modbus_client import ModbusClient
from venus_evcharger.backend.modbus_transport import create_modbus_transport
from venus_evcharger.backend.modbus_transport_types import ModbusTransportSettings
from venus_evcharger.backend.template_support import TemplateAuthSettings
from .connectors_command import (
    CommandJsonEnergySourceSettings,
    _build_command_json_energy_source_snapshot,
    _command_args,
    _command_json_energy_source_settings,
    _command_source_name,
    _validate_command_json_energy_source_settings,
)
from .connectors_common import (
    _normalized_connector_type,
    _optional_bool_path,
    _optional_confidence_path,
    _optional_float_path,
    _optional_path,
    _optional_text_path,
    _runtime_owner,
)
from .connectors_modbus import (
    ModbusEnergyFieldSettings,
    ModbusEnergySourceSettings,
    _build_modbus_energy_source_snapshot,
    _modbus_client_cache,
    _modbus_energy_source_settings,
    _modbus_field_settings,
    _modbus_field_value,
    _modbus_source_name,
    _validate_modbus_energy_source_settings,
)
from .connectors_opendtu import (
    OpenDtuEnergySourceSettings,
    _opendtu_energy_source_snapshot,
)
from .connectors_template import (
    TemplateHttpEnergySourceSettings,
    _template_http_energy_source_settings,
    _template_http_energy_source_snapshot,
    _template_source_name,
    _validate_template_http_energy_source_settings,
)
from .models import EnergySourceDefinition, EnergySourceSnapshot

EnergySourceReader = Callable[[Any, EnergySourceDefinition, float], EnergySourceSnapshot]

__all__ = [
    "CommandJsonEnergySourceSettings",
    "ModbusEnergyFieldSettings",
    "ModbusEnergySourceSettings",
    "ModbusTransportSettings",
    "OpenDtuEnergySourceSettings",
    "TemplateAuthSettings",
    "TemplateHttpEnergySourceSettings",
    "create_modbus_transport",
    "read_energy_source_snapshot",
    "_command_args",
    "_command_json_energy_source_settings",
    "_command_json_energy_source_snapshot",
    "_command_source_name",
    "_modbus_energy_source_client",
    "_modbus_energy_source_settings",
    "_modbus_energy_source_snapshot",
    "_modbus_field_settings",
    "_modbus_field_text",
    "_modbus_field_value",
    "_modbus_source_name",
    "_normalized_connector_type",
    "_opendtu_energy_source_snapshot",
    "_optional_bool_path",
    "_optional_confidence_path",
    "_optional_float_path",
    "_optional_path",
    "_optional_text_path",
    "_template_http_energy_source_settings",
    "_template_http_energy_source_snapshot",
    "_template_source_name",
    "_validate_command_json_energy_source_settings",
    "_validate_modbus_energy_source_settings",
    "_validate_template_http_energy_source_settings",
]


def read_energy_source_snapshot(owner: Any, source: EnergySourceDefinition, now: float) -> EnergySourceSnapshot:
    """Read one energy-source snapshot through the configured connector layer."""
    connector_type = _normalized_connector_type(source.connector_type)
    readers: dict[str, EnergySourceReader] = {
        "template_http": _template_http_energy_source_snapshot,
        "opendtu_http": _opendtu_energy_source_snapshot,
        "modbus": _modbus_energy_source_snapshot,
        "command_json": _command_json_energy_source_snapshot,
    }
    reader = readers.get(connector_type)
    if reader is None:
        raise ValueError(f"Unsupported energy-source connector: {connector_type or '<empty>'}")
    return reader(owner, source, now)


def _modbus_energy_source_client(
    runtime: Any,
    source: EnergySourceDefinition,
    settings: ModbusEnergySourceSettings,
) -> ModbusClient:
    cache = _modbus_client_cache(runtime)
    cache_key = str(source.config_path).strip()
    cached = cache.get(cache_key)
    if isinstance(cached, ModbusClient):
        return cached
    transport = create_modbus_transport(settings.transport_settings)
    client = ModbusClient(transport, settings.transport_settings.unit_id, settings.transport_settings.timeout_seconds)
    cache[cache_key] = client
    return client


def _modbus_field_text(
    client: ModbusClient,
    field: ModbusEnergyFieldSettings | None,
    text_map: dict[str, str] | None = None,
) -> str:
    value = _modbus_field_value(client, field)
    if value is None:
        return ""
    normalized = str(int(value)) if float(value).is_integer() else str(value)
    if text_map:
        return str(text_map.get(normalized, normalized))
    return normalized


def _modbus_energy_source_snapshot(owner: Any, source: EnergySourceDefinition, now: float) -> EnergySourceSnapshot:
    runtime = _runtime_owner(owner)
    settings = _modbus_energy_source_settings(runtime, source)
    client = _modbus_energy_source_client(runtime, source, settings)
    return _build_modbus_energy_source_snapshot(
        source,
        now,
        settings,
        client,
        _modbus_field_value,
        _modbus_field_text,
    )


def _command_json_energy_source_snapshot(owner: Any, source: EnergySourceDefinition, now: float) -> EnergySourceSnapshot:
    runtime = _runtime_owner(owner)
    settings = _command_json_energy_source_settings(runtime, source)
    completed = subprocess.run(  # noqa: S603
        settings.command,
        check=True,
        capture_output=True,
        text=True,
        timeout=settings.timeout_seconds,
    )
    payload = json.loads(completed.stdout.strip() or "{}")
    if not isinstance(payload, dict):
        raise ValueError(f"Energy source '{source.source_id}' helper did not return a JSON object")
    return _build_command_json_energy_source_snapshot(source, now, settings, payload)
