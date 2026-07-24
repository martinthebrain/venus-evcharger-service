# SPDX-License-Identifier: GPL-3.0-or-later
"""OpenDTU connector for external energy sources."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from venus_evcharger.backend.template_support import (
    TemplateAuthSettings,
    config_section,
    load_template_auth_settings,
    load_template_config,
    resolved_url,
)
from venus_evcharger.core.contracts import finite_float_or_none

from .connectors_common import (
    EnergySourceHttpClient as TemplateHttpBackendBase,
)
from .connectors_common import (
    _bounded_request_timeout_seconds,
    _csv_filter,
    _runtime_cache_get,
    _runtime_cache_pop,
    _runtime_cache_put,
    _runtime_default_timeout_seconds,
    _runtime_owner,
)
from .connectors_opendtu_payload import (
    energy_source_allows_unreachable_idle as _energy_source_allows_unreachable_idle,
)
from .connectors_opendtu_payload import (
    opendtu_any_producing as _opendtu_any_producing,
)
from .connectors_opendtu_payload import (
    opendtu_detail_inverter as _opendtu_detail_inverter,
)
from .connectors_opendtu_payload import (
    opendtu_inverter_has_measurements as _opendtu_inverter_has_measurements,
)
from .connectors_opendtu_payload import (
    opendtu_online_inverters as _opendtu_online_inverters,
)
from .connectors_opendtu_payload import (
    opendtu_plausible_idle_snapshot as _opendtu_plausible_idle_snapshot,
)
from .connectors_opendtu_payload import opendtu_serial as _opendtu_serial
from .connectors_opendtu_payload import (
    opendtu_snapshot_confidence as _opendtu_snapshot_confidence,
)
from .connectors_opendtu_payload import (
    opendtu_summed_ac_power as _opendtu_summed_ac_power,
)
from .connectors_opendtu_payload import (
    opendtu_total_dc_power as _opendtu_total_dc_power,
)
from .connectors_opendtu_payload import (
    opendtu_unique_raw_inverters as _opendtu_unique_raw_inverters,
)
from .models import EnergySourceDefinition, EnergySourceSnapshot
from .read_steps import EnergySourceReadStep, completed_read, pending_read

_DEFAULT_TIMEOUT_SECONDS = 2.0
_DEFAULT_MAX_DATA_AGE_SECONDS = 600.0
_ADAPTER_SECTION = "Adapter"
_OPENDTU_SECTION = "OpenDTU"
_BASE_URL_KEY = "BaseUrl"
_REQUEST_TIMEOUT_KEY = "RequestTimeoutSeconds"
_MAX_DATA_AGE_KEY = "MaxDataAgeSeconds"
_STATUS_URL_KEY = "StatusUrl"
_INVERTER_STATUS_URL_KEY = "InverterStatusUrl"
_INVERTER_SERIALS_KEY = "InverterSerials"
_DEFAULT_STATUS_URL = "/api/livedata/status"
_DEFAULT_INVERTER_STATUS_URL = "/api/livedata/status?inv=${serial}"
_SETTINGS_CACHE = "opendtu.settings"
_PROGRESS_CACHE = "opendtu.progress"


@dataclass(frozen=True)
class OpenDtuEnergySourceSettings:
    """Normalized config for one OpenDTU-backed energy source."""

    base_url: str
    auth_settings: TemplateAuthSettings
    timeout_seconds: float
    status_url: str
    inverter_status_url: str
    serial_filter: tuple[str, ...]
    max_data_age_seconds: float


@dataclass(slots=True)
class OpenDtuReadProgress:
    """Inverters accumulated across single-request OpenDTU steps."""

    payload: dict[str, object]
    inverters: dict[str, dict[str, object]]
    detail_serials: tuple[str, ...]
    next_detail_index: int

def _opendtu_snapshot_client(
    runtime: object,
    settings: OpenDtuEnergySourceSettings,
) -> TemplateHttpBackendBase:
    return TemplateHttpBackendBase(
        runtime,
        settings.timeout_seconds,
        auth_settings=settings.auth_settings,
    )


def _opendtu_snapshot_payload(
    client: TemplateHttpBackendBase, settings: OpenDtuEnergySourceSettings
) -> dict[str, object]:
    return client._perform_request("GET", settings.status_url)


def _opendtu_energy_source_step(
    owner: object,
    source: EnergySourceDefinition,
    observed_at: float,
) -> EnergySourceReadStep:
    runtime = _runtime_owner(owner)
    settings = _opendtu_energy_source_settings(runtime, source)
    client = _opendtu_snapshot_client(runtime, settings)
    progress_key = _opendtu_progress_key(source)
    progress = _runtime_cache_get(
        runtime,
        _PROGRESS_CACHE,
        progress_key,
        OpenDtuReadProgress,
    )
    try:
        if progress is None:
            progress = _opendtu_start_read(client, settings)
        else:
            _opendtu_continue_read(client, settings, progress)
    except Exception:
        _runtime_cache_pop(runtime, _PROGRESS_CACHE, progress_key)
        raise
    if progress.next_detail_index < len(progress.detail_serials):
        _runtime_cache_put(runtime, _PROGRESS_CACHE, progress_key, progress)
        return pending_read()
    _runtime_cache_pop(runtime, _PROGRESS_CACHE, progress_key)
    return completed_read(
        _opendtu_completed_snapshot(
            source,
            settings,
            progress.payload,
            tuple(progress.inverters.values()),
            observed_at,
        )
    )


def _opendtu_start_read(
    client: TemplateHttpBackendBase,
    settings: OpenDtuEnergySourceSettings,
) -> OpenDtuReadProgress:
    payload = _opendtu_snapshot_payload(client, settings)
    inverters = _opendtu_unique_raw_inverters(payload, settings.serial_filter)
    ready = {
        _opendtu_serial(inverter): inverter
        for inverter in inverters
        if _opendtu_inverter_has_measurements(inverter)
    }
    detail_serials = tuple(
        _opendtu_serial(inverter)
        for inverter in inverters
        if not _opendtu_inverter_has_measurements(inverter)
    )
    return OpenDtuReadProgress(payload, ready, detail_serials, 0)


def _opendtu_continue_read(
    client: TemplateHttpBackendBase,
    settings: OpenDtuEnergySourceSettings,
    progress: OpenDtuReadProgress,
) -> None:
    serial = progress.detail_serials[progress.next_detail_index]
    detail_payload = client._perform_request(
        "GET",
        settings.inverter_status_url,
        context={"serial": serial},
    )
    progress.inverters[serial] = _opendtu_detail_inverter(detail_payload, serial)
    progress.next_detail_index += 1


def _opendtu_completed_snapshot(
    source: EnergySourceDefinition,
    settings: OpenDtuEnergySourceSettings,
    payload: dict[str, object],
    inverters: tuple[dict[str, object], ...],
    observed_at: float,
) -> EnergySourceSnapshot:
    online_inverters = _opendtu_online_inverters(
        inverters,
        settings.max_data_age_seconds,
    )
    ac_power = _opendtu_summed_ac_power(online_inverters)
    pv_input_power = _opendtu_total_dc_power(online_inverters)
    plausible_idle = _opendtu_plausible_idle_snapshot(
        payload,
        inverters,
        ac_power_w=ac_power,
        pv_input_power_w=pv_input_power,
        max_data_age_seconds=settings.max_data_age_seconds,
        allow_unreachable_idle=_energy_source_allows_unreachable_idle(source),
    )
    online, confidence = _opendtu_snapshot_confidence(inverters, settings.max_data_age_seconds, plausible_idle)
    if plausible_idle:
        ac_power = 0.0
    return EnergySourceSnapshot(
        source_id=source.source_id,
        role=source.role,
        service_name=_opendtu_source_name(source, settings),
        physical_id=source.physical_id,
        ac_power_w=ac_power,
        pv_input_power_w=pv_input_power,
        operating_mode="producing" if _opendtu_any_producing(online_inverters) else "idle",
        online=online,
        confidence=confidence,
        captured_at=observed_at,
    )


def _opendtu_source_name(source: EnergySourceDefinition, settings: OpenDtuEnergySourceSettings) -> str:
    if source.service_name:
        return source.service_name
    if settings.base_url:
        return settings.base_url
    return source.config_path or source.source_id


def _opendtu_timeout_seconds(
    runtime: object,
    adapter: Mapping[str, object],
) -> float:
    default_timeout = _runtime_default_timeout_seconds(runtime, _DEFAULT_TIMEOUT_SECONDS)
    timeout = finite_float_or_none(adapter.get(_REQUEST_TIMEOUT_KEY))
    configured = default_timeout if timeout is None or timeout <= 0.0 else float(timeout)
    return _bounded_request_timeout_seconds(runtime, configured)


def _opendtu_max_data_age_seconds(opendtu: Mapping[str, object]) -> float:
    max_data_age = finite_float_or_none(opendtu.get(_MAX_DATA_AGE_KEY))
    return _DEFAULT_MAX_DATA_AGE_SECONDS if max_data_age is None or max_data_age < 0.0 else float(max_data_age)


def _section_text(
    section: Mapping[str, object],
    key: str,
    default: str = "",
) -> str:
    value = section.get(key)
    if value is None:
        return default
    return str(value).strip() or default


def _opendtu_energy_source_settings(
    runtime: object,
    source: EnergySourceDefinition,
) -> OpenDtuEnergySourceSettings:
    cache_key = str(source.config_path).strip()
    cached = _runtime_cache_get(
        runtime,
        _SETTINGS_CACHE,
        cache_key,
        OpenDtuEnergySourceSettings,
    )
    if cached is not None:
        return cached
    if not cache_key:
        raise ValueError(f"Energy source '{source.source_id}' requires ConfigPath for opendtu_http connector")
    parser = load_template_config(cache_key)
    adapter = config_section(parser, _ADAPTER_SECTION)
    opendtu = config_section(parser, _OPENDTU_SECTION)
    base_url = _section_text(adapter, _BASE_URL_KEY)
    settings = OpenDtuEnergySourceSettings(
        base_url=base_url,
        auth_settings=load_template_auth_settings(adapter),
        timeout_seconds=_opendtu_timeout_seconds(runtime, adapter),
        status_url=resolved_url(base_url, _section_text(opendtu, _STATUS_URL_KEY, _DEFAULT_STATUS_URL)),
        inverter_status_url=resolved_url(
            base_url,
            _section_text(opendtu, _INVERTER_STATUS_URL_KEY, _DEFAULT_INVERTER_STATUS_URL),
        ),
        serial_filter=_unique_serial_filter(opendtu.get(_INVERTER_SERIALS_KEY)),
        max_data_age_seconds=_opendtu_max_data_age_seconds(opendtu),
    )
    _validate_opendtu_energy_source_settings(source, settings)
    _runtime_cache_put(runtime, _SETTINGS_CACHE, cache_key, settings)
    return settings


def _validate_opendtu_energy_source_settings(
    source: EnergySourceDefinition, settings: OpenDtuEnergySourceSettings
) -> None:
    if not settings.status_url:
        raise ValueError(f"Energy source '{source.source_id}' requires OpenDTU.StatusUrl or Adapter.BaseUrl")


def _unique_serial_filter(raw_value: object) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_csv_filter(raw_value)))


def _opendtu_progress_key(source: EnergySourceDefinition) -> str:
    return f"{source.source_id}\0{source.config_path}"
