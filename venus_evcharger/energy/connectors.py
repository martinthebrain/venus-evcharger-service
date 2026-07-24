# SPDX-License-Identifier: GPL-3.0-or-later
"""Connector registry facade for external energy-source transports."""

from __future__ import annotations

import json

from venus_evcharger.backend.modbus_client import ModbusClient
from venus_evcharger.backend.modbus_transport import create_modbus_transport
from venus_evcharger.backend.template_support import normalized_object_mapping

from .connectors_command import (
    _build_command_json_energy_source_snapshot,
    _command_json_energy_source_settings,
)
from .connectors_common import (
    _bounded_request_timeout_seconds,
    _normalized_connector_type,
    _runtime_cache_get,
    _runtime_cache_pop,
    _runtime_cache_put,
    _runtime_owner,
)
from .connectors_modbus import (
    ModbusEnergyFieldSettings,
    ModbusReadProgress,
    ModbusEnergySourceSettings,
    _build_modbus_energy_source_snapshot,
    _cached_modbus_client,
    _modbus_energy_source_settings,
    _modbus_field_value,
    _modbus_progress_value,
    _modbus_read_fields,
    _store_modbus_client,
)
from .connectors_opendtu import _opendtu_energy_source_step
from .connectors_template import _template_http_energy_source_snapshot
from .bounded_subprocess import run_bounded_command
from .models import EnergySourceDefinition
from .read_steps import (
    EnergySourceReadStep,
    EnergySourceStepReader,
    completed_read,
    pending_read,
)

_MODBUS_PROGRESS_CACHE = "modbus.progress"
_COMMAND_STDOUT_LIMIT = 262144
_COMMAND_STDERR_LIMIT = 16384

__all__ = ["EnergySourceStepReader", "read_energy_source_step"]


def read_energy_source_step(
    owner: object,
    source: EnergySourceDefinition,
    observed_at: float,
) -> EnergySourceReadStep:
    """Execute at most one I/O operation through the configured connector."""
    connector_type = _normalized_connector_type(source.connector_type)
    reader = _ENERGY_SOURCE_STEP_READERS.get(connector_type)
    if reader is None:
        raise ValueError(f"Unsupported energy-source connector: {connector_type or '<empty>'}")
    return reader(owner, source, observed_at)


def _modbus_energy_source_client(
    runtime: object,
    source: EnergySourceDefinition,
    settings: ModbusEnergySourceSettings,
) -> ModbusClient:
    cache_key = str(source.config_path).strip()
    cached = _cached_modbus_client(runtime, cache_key)
    if cached is not None:
        return cached
    transport = create_modbus_transport(settings.transport_settings)
    client = ModbusClient(transport, settings.transport_settings.unit_id, settings.transport_settings.timeout_seconds)
    _store_modbus_client(runtime, cache_key, client)
    return client


def _modbus_energy_source_step(
    owner: object,
    source: EnergySourceDefinition,
    observed_at: float,
) -> EnergySourceReadStep:
    runtime = _runtime_owner(owner)
    settings = _modbus_energy_source_settings(runtime, source)
    client = _modbus_energy_source_client(runtime, source, settings)
    fields = _modbus_read_fields(settings)
    progress_key = _connector_progress_key(source)
    progress = _modbus_read_progress(runtime, progress_key)
    if not fields:
        return completed_read(
            _build_modbus_energy_source_snapshot(source, observed_at, settings, {})
        )
    _read_next_modbus_field(
        runtime,
        client,
        settings,
        fields,
        progress,
        progress_key,
    )
    if progress.next_field_index < len(fields):
        _runtime_cache_put(runtime, _MODBUS_PROGRESS_CACHE, progress_key, progress)
        return pending_read()
    _runtime_cache_pop(runtime, _MODBUS_PROGRESS_CACHE, progress_key)
    return completed_read(
        _build_modbus_energy_source_snapshot(
            source,
            observed_at,
            settings,
            progress.values,
        )
    )


def _modbus_read_progress(
    runtime: object,
    progress_key: str,
) -> ModbusReadProgress:
    progress = _runtime_cache_get(
        runtime,
        _MODBUS_PROGRESS_CACHE,
        progress_key,
        ModbusReadProgress,
    )
    return progress if progress is not None else ModbusReadProgress(0, {})


def _read_next_modbus_field(
    runtime: object,
    client: ModbusClient,
    settings: ModbusEnergySourceSettings,
    fields: tuple[tuple[str, ModbusEnergyFieldSettings], ...],
    progress: ModbusReadProgress,
    progress_key: str,
) -> None:
    field_name, field = fields[progress.next_field_index]
    client.timeout_seconds = _bounded_request_timeout_seconds(
        runtime,
        settings.transport_settings.timeout_seconds,
    )
    try:
        value = _modbus_field_value(client, field)
    except Exception:
        _runtime_cache_pop(runtime, _MODBUS_PROGRESS_CACHE, progress_key)
        raise
    assert value is not None
    progress.values[field_name] = _modbus_progress_value(field_name, value, settings)
    progress.next_field_index += 1


def _command_json_energy_source_step(
    owner: object,
    source: EnergySourceDefinition,
    observed_at: float,
) -> EnergySourceReadStep:
    runtime = _runtime_owner(owner)
    settings = _command_json_energy_source_settings(runtime, source)
    timeout_seconds = _bounded_request_timeout_seconds(runtime, settings.timeout_seconds)
    completed = run_bounded_command(
        settings.command,
        timeout_seconds=timeout_seconds,
        stdout_limit=_COMMAND_STDOUT_LIMIT,
        stderr_limit=_COMMAND_STDERR_LIMIT,
    )
    decoded: object = json.loads(completed.stdout.strip() or "{}")
    payload = normalized_object_mapping(decoded)
    if payload is None:
        raise ValueError(f"Energy source '{source.source_id}' helper did not return a JSON object")
    return completed_read(
        _build_command_json_energy_source_snapshot(
            source,
            observed_at,
            settings,
            payload,
        )
    )


def _template_http_energy_source_step(
    owner: object,
    source: EnergySourceDefinition,
    observed_at: float,
) -> EnergySourceReadStep:
    return completed_read(
        _template_http_energy_source_snapshot(owner, source, observed_at)
    )


def _connector_progress_key(source: EnergySourceDefinition) -> str:
    return f"{source.source_id}\0{source.config_path}"


_ENERGY_SOURCE_STEP_READERS: dict[str, EnergySourceStepReader] = {
    "template_http": _template_http_energy_source_step,
    "opendtu_http": _opendtu_energy_source_step,
    "modbus": _modbus_energy_source_step,
    "command_json": _command_json_energy_source_step,
}
