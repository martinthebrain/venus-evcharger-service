# SPDX-License-Identifier: GPL-3.0-or-later
"""Template HTTP connector for external energy sources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from venus_evcharger.backend.template_support import (
    TemplateAuthSettings,
    TemplateHttpBackendBase,
    config_section,
    load_template_auth_settings,
    load_template_config,
    normalize_http_method,
    resolved_url,
)
from venus_evcharger.core.contracts import finite_float_or_none

from .connectors_common import (
    _optional_bool_path,
    _optional_confidence_path,
    _optional_float_path,
    _optional_path,
    _optional_text_path,
    _runtime_owner,
    _typed_cache_map,
)
from .models import EnergySourceDefinition, EnergySourceSnapshot


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


def _template_http_energy_source_snapshot(owner: Any, source: EnergySourceDefinition, now: float) -> EnergySourceSnapshot:
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
        soc=soc_value,
        usable_capacity_wh=usable_capacity_wh,
        net_battery_power_w=_optional_float_path(payload, settings.battery_power_path),
        ac_power_w=_optional_float_path(payload, settings.ac_power_path),
        pv_input_power_w=_optional_float_path(payload, settings.pv_input_power_path),
        grid_interaction_w=_optional_float_path(payload, settings.grid_interaction_path),
        operating_mode=_optional_text_path(payload, settings.operating_mode_path) or "",
        online=True if online is None else bool(online),
        confidence=1.0 if confidence is None else confidence,
        captured_at=now,
    )


def _template_source_name(source: EnergySourceDefinition, settings: TemplateHttpEnergySourceSettings) -> str:
    if source.service_name:
        return source.service_name
    if settings.base_url:
        return settings.base_url
    return source.config_path or source.source_id


def _template_http_payload(runtime: Any, settings: TemplateHttpEnergySourceSettings) -> Any:
    return TemplateHttpBackendBase(
        runtime,
        settings.timeout_seconds,
        auth_settings=settings.auth_settings,
    )._perform_request(settings.request_method, settings.request_url)


def _template_soc_value(payload: Any, settings: TemplateHttpEnergySourceSettings) -> float | None:
    soc_value = _optional_float_path(payload, settings.soc_path)
    if soc_value is not None and not 0.0 <= soc_value <= 100.0:
        return None
    return soc_value


def _template_usable_capacity_wh(
    payload: Any,
    settings: TemplateHttpEnergySourceSettings,
    source: EnergySourceDefinition,
) -> float | None:
    usable_capacity_wh = _optional_float_path(payload, settings.usable_capacity_wh_path)
    if usable_capacity_wh is None:
        return source.usable_capacity_wh
    if usable_capacity_wh <= 0.0:
        return None
    return usable_capacity_wh


def _template_online(payload: Any, settings: TemplateHttpEnergySourceSettings) -> bool:
    online = _optional_bool_path(payload, settings.online_path)
    return True if online is None else bool(online)


def _template_confidence(payload: Any, settings: TemplateHttpEnergySourceSettings) -> float:
    confidence = _optional_confidence_path(payload, settings.confidence_path)
    return 1.0 if confidence is None else confidence


def _template_timeout_seconds(runtime: Any, adapter: Any) -> float:
    default_timeout = float(getattr(runtime, "shelly_request_timeout_seconds", 2.0) or 2.0)
    timeout = finite_float_or_none(adapter.get("RequestTimeoutSeconds", str(default_timeout)))
    if timeout is None or timeout <= 0.0:
        return default_timeout
    return float(timeout)


def _template_http_energy_source_settings(runtime: Any, source: EnergySourceDefinition) -> TemplateHttpEnergySourceSettings:
    cache = _typed_cache_map(runtime, "_energy_template_settings_cache", TemplateHttpEnergySourceSettings)
    cache_key = str(source.config_path).strip()
    cached = cache.get(cache_key)
    if isinstance(cached, TemplateHttpEnergySourceSettings):
        return cached
    if not cache_key:
        raise ValueError(f"Energy source '{source.source_id}' requires ConfigPath for template_http connector")
    parser = load_template_config(cache_key)
    adapter = config_section(parser, "Adapter")
    request = config_section(parser, "EnergyRequest")
    response = config_section(parser, "EnergyResponse")
    base_url = str(adapter.get("BaseUrl", "")).strip()
    settings = TemplateHttpEnergySourceSettings(
        base_url=base_url,
        auth_settings=load_template_auth_settings(adapter),
        timeout_seconds=_template_timeout_seconds(runtime, adapter),
        request_method=normalize_http_method(request.get("Method", "GET"), "GET"),
        request_url=resolved_url(base_url, request.get("Url", "")),
        soc_path=_optional_path(response.get("SocPath", "")),
        usable_capacity_wh_path=_optional_path(response.get("UsableCapacityWhPath", "")),
        battery_power_path=_optional_path(response.get("BatteryPowerPath", "")),
        ac_power_path=_optional_path(response.get("AcPowerPath", "")),
        pv_input_power_path=_optional_path(response.get("PvInputPowerPath", "")),
        grid_interaction_path=_optional_path(response.get("GridInteractionPath", "")),
        operating_mode_path=_optional_path(response.get("OperatingModePath", "")),
        online_path=_optional_path(response.get("OnlinePath", "")),
        confidence_path=_optional_path(response.get("ConfidencePath", "")),
    )
    _validate_template_http_energy_source_settings(source, settings)
    cache[cache_key] = settings
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
