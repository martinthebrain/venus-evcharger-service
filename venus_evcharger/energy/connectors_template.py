# SPDX-License-Identifier: GPL-3.0-or-later
"""Template HTTP connector for external energy sources."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from venus_evcharger.backend.template_support import (
    TemplateAuthSettings,
    config_section,
    load_template_auth_settings,
    load_template_config,
    normalize_http_method,
    resolved_url,
)
from venus_evcharger.core.contracts import finite_float_or_none

from .connectors_common import (
    EnergySourceHttpClient as TemplateHttpBackendBase,
)
from .connectors_common import (
    _bounded_request_timeout_seconds,
    _optional_bool_path,
    _optional_confidence_path,
    _optional_float_path,
    _optional_path,
    _optional_text_path,
    _runtime_cache_get,
    _runtime_cache_put,
    _runtime_default_timeout_seconds,
    _runtime_owner,
)
from .models import EnergySourceDefinition, EnergySourceSnapshot

_DEFAULT_TIMEOUT_SECONDS = 2.0
_ADAPTER_SECTION = "Adapter"
_REQUEST_SECTION = "EnergyRequest"
_RESPONSE_SECTION = "EnergyResponse"
_BASE_URL_KEY = "BaseUrl"
_REQUEST_TIMEOUT_KEY = "RequestTimeoutSeconds"
_METHOD_KEY = "Method"
_URL_KEY = "Url"
_DEFAULT_METHOD = "GET"
_SETTINGS_CACHE = "template_http.settings"
_RESPONSE_PATH_KEYS = (
    "SocPath",
    "UsableCapacityWhPath",
    "BatteryPowerPath",
    "AcPowerPath",
    "PvInputPowerPath",
    "GridInteractionPath",
    "OperatingModePath",
    "OnlinePath",
    "ConfidencePath",
)


@dataclass(frozen=True)
class TemplateHttpEnergySourceSettings:
    """Normalized config for one HTTP/JSON-backed external energy source."""

    base_url: str
    auth_settings: TemplateAuthSettings
    timeout_seconds: float
    request_method: str
    request_url: str
    soc_path: str | None
    usable_capacity_wh_path: str | None
    battery_power_path: str | None
    ac_power_path: str | None
    pv_input_power_path: str | None
    grid_interaction_path: str | None
    operating_mode_path: str | None
    online_path: str | None
    confidence_path: str | None


def _template_http_energy_source_snapshot(
    owner: object,
    source: EnergySourceDefinition,
    now: float,
) -> EnergySourceSnapshot:
    runtime = _runtime_owner(owner)
    settings = _template_http_energy_source_settings(runtime, source)
    payload = _template_http_payload(runtime, settings)
    soc_value = _template_soc_value(payload, settings)
    usable_capacity_wh = _template_usable_capacity_wh(payload, settings, source)
    online = _template_online(payload, settings)
    confidence = _template_confidence(payload, settings)
    return EnergySourceSnapshot(
        source_id=source.source_id,
        role=source.role,
        service_name=_template_source_name(source, settings),
        physical_id=source.physical_id,
        physical_priority=source.physical_priority,
        soc=soc_value,
        usable_capacity_wh=usable_capacity_wh,
        net_battery_power_w=_optional_float_path(payload, settings.battery_power_path),
        ac_power_w=_optional_float_path(payload, settings.ac_power_path),
        pv_input_power_w=_optional_float_path(payload, settings.pv_input_power_path),
        grid_interaction_w=_optional_float_path(payload, settings.grid_interaction_path),
        operating_mode=_optional_text_path(payload, settings.operating_mode_path) or "",
        online=online,
        confidence=confidence,
        captured_at=now,
    )


def _template_source_name(source: EnergySourceDefinition, settings: TemplateHttpEnergySourceSettings) -> str:
    if source.service_name:
        return source.service_name
    if settings.base_url:
        return settings.base_url
    return source.config_path or source.source_id


def _template_http_payload(
    runtime: object,
    settings: TemplateHttpEnergySourceSettings,
) -> dict[str, object]:
    return TemplateHttpBackendBase(
        runtime,
        settings.timeout_seconds,
        auth_settings=settings.auth_settings,
    )._perform_request(settings.request_method, settings.request_url)


def _template_soc_value(
    payload: dict[str, object],
    settings: TemplateHttpEnergySourceSettings,
) -> float | None:
    soc_value = _optional_float_path(payload, settings.soc_path)
    if soc_value is not None and not 0.0 <= soc_value <= 100.0:
        return None
    return soc_value


def _template_usable_capacity_wh(
    payload: dict[str, object],
    settings: TemplateHttpEnergySourceSettings,
    source: EnergySourceDefinition,
) -> float | None:
    usable_capacity_wh = _optional_float_path(payload, settings.usable_capacity_wh_path)
    if usable_capacity_wh is None:
        return source.usable_capacity_wh
    if usable_capacity_wh <= 0.0:
        return None
    return usable_capacity_wh


def _template_online(
    payload: dict[str, object],
    settings: TemplateHttpEnergySourceSettings,
) -> bool:
    online = _optional_bool_path(payload, settings.online_path)
    return True if online is None else bool(online)


def _template_confidence(
    payload: dict[str, object],
    settings: TemplateHttpEnergySourceSettings,
) -> float:
    confidence = _optional_confidence_path(payload, settings.confidence_path)
    return 1.0 if confidence is None else confidence


def _template_timeout_seconds(
    runtime: object,
    adapter: Mapping[str, object],
) -> float:
    default_timeout = _runtime_default_timeout_seconds(runtime, _DEFAULT_TIMEOUT_SECONDS)
    timeout = finite_float_or_none(adapter.get(_REQUEST_TIMEOUT_KEY))
    configured = default_timeout if timeout is None or timeout <= 0.0 else float(timeout)
    return _bounded_request_timeout_seconds(runtime, configured)


def _section_text(
    section: Mapping[str, object],
    key: str,
    default: str = "",
) -> str:
    value = section.get(key)
    if value is None:
        return default
    return str(value).strip() or default


def _template_http_energy_source_settings(
    runtime: object,
    source: EnergySourceDefinition,
) -> TemplateHttpEnergySourceSettings:
    cache_key = str(source.config_path).strip()
    cached = _runtime_cache_get(
        runtime,
        _SETTINGS_CACHE,
        cache_key,
        TemplateHttpEnergySourceSettings,
    )
    if cached is not None:
        return cached
    if not cache_key:
        raise ValueError(f"Energy source '{source.source_id}' requires ConfigPath for template_http connector")
    parser = load_template_config(cache_key)
    adapter = config_section(parser, _ADAPTER_SECTION)
    request = config_section(parser, _REQUEST_SECTION)
    response = config_section(parser, _RESPONSE_SECTION)
    base_url = _section_text(adapter, _BASE_URL_KEY)
    response_paths = tuple(_optional_path(response.get(key)) for key in _RESPONSE_PATH_KEYS)
    settings = TemplateHttpEnergySourceSettings(
        base_url=base_url,
        auth_settings=load_template_auth_settings(adapter),
        timeout_seconds=_template_timeout_seconds(runtime, adapter),
        request_method=normalize_http_method(_section_text(request, _METHOD_KEY), _DEFAULT_METHOD),
        request_url=resolved_url(base_url, _section_text(request, _URL_KEY)),
        soc_path=response_paths[0],
        usable_capacity_wh_path=response_paths[1],
        battery_power_path=response_paths[2],
        ac_power_path=response_paths[3],
        pv_input_power_path=response_paths[4],
        grid_interaction_path=response_paths[5],
        operating_mode_path=response_paths[6],
        online_path=response_paths[7],
        confidence_path=response_paths[8],
    )
    _validate_template_http_energy_source_settings(source, settings)
    _runtime_cache_put(runtime, _SETTINGS_CACHE, cache_key, settings)
    return settings


def _validate_template_http_energy_source_settings(
    source: EnergySourceDefinition,
    settings: TemplateHttpEnergySourceSettings,
) -> None:
    if not settings.request_url:
        raise ValueError(f"Energy source '{source.source_id}' requires [EnergyRequest] Url")
    if _template_has_readable_response(settings, source):
        return
    raise ValueError(
        f"Energy source '{source.source_id}' requires at least one readable EnergyResponse path or UsableCapacityWh"
    )


def _template_has_readable_response(
    settings: TemplateHttpEnergySourceSettings,
    source: EnergySourceDefinition,
) -> bool:
    readable_paths = (
        settings.soc_path,
        settings.battery_power_path,
        settings.ac_power_path,
        settings.pv_input_power_path,
        settings.grid_interaction_path,
        settings.usable_capacity_wh_path,
    )
    if any(path is not None for path in readable_paths):
        return True
    return source.usable_capacity_wh is not None
