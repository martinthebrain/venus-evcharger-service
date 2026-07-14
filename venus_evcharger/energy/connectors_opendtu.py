# SPDX-License-Identifier: GPL-3.0-or-later
"""OpenDTU connector for external energy sources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from venus_evcharger.backend.template_support import (
    TemplateAuthSettings,
    TemplateHttpBackendBase,
    config_section,
    load_template_auth_settings,
    load_template_config,
    resolved_url,
)
from venus_evcharger.core.contracts import finite_float_or_none

from .connectors_common import _csv_filter, _runtime_owner, _sum_optional, _typed_cache_map
from .models import EnergySourceDefinition, EnergySourceSnapshot
from .profiles import resolve_energy_source_profile


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


def _opendtu_snapshot_client(runtime: Any, settings: OpenDtuEnergySourceSettings) -> TemplateHttpBackendBase:
    return TemplateHttpBackendBase(
        runtime,
        settings.timeout_seconds,
        auth_settings=settings.auth_settings,
    )


def _opendtu_snapshot_payload(client: TemplateHttpBackendBase, settings: OpenDtuEnergySourceSettings) -> dict[str, object]:
    return client._perform_request("GET", settings.status_url)


def _opendtu_online_inverters(
    inverters: tuple[dict[str, object], ...],
    max_data_age_seconds: float,
) -> tuple[dict[str, object], ...]:
    return tuple(
        inverter
        for inverter in inverters
        if _opendtu_inverter_online(inverter, max_data_age_seconds)
    )


def _opendtu_snapshot_confidence(
    inverters: tuple[dict[str, object], ...],
    max_data_age_seconds: float,
    plausible_idle: bool,
) -> tuple[bool, float]:
    filtered_count = len(inverters)
    reachable_count = len(_opendtu_online_inverters(inverters, max_data_age_seconds))
    online = bool(filtered_count) and (bool(reachable_count) or plausible_idle)
    confidence = 0.0 if filtered_count <= 0 else float(reachable_count) / float(filtered_count)
    if plausible_idle:
        confidence = max(confidence, 1.0)
    return online, confidence


def _opendtu_energy_source_snapshot(owner: Any, source: EnergySourceDefinition, now: float) -> EnergySourceSnapshot:
    runtime = _runtime_owner(owner)
    settings = _opendtu_energy_source_settings(runtime, source)
    client = _opendtu_snapshot_client(runtime, settings)
    payload = _opendtu_snapshot_payload(client, settings)
    inverters = _opendtu_selected_inverters(payload, settings, client)
    ac_power = _opendtu_total_ac_power(payload, inverters, settings.serial_filter)
    pv_input_power = _opendtu_total_dc_power(inverters)
    plausible_idle = _opendtu_plausible_idle_snapshot(
        payload,
        inverters,
        ac_power_w=ac_power,
        pv_input_power_w=pv_input_power,
        max_data_age_seconds=settings.max_data_age_seconds,
        allow_unreachable_idle=_energy_source_allows_unreachable_idle(source),
    )
    online, confidence = _opendtu_snapshot_confidence(inverters, settings.max_data_age_seconds, plausible_idle)
    return EnergySourceSnapshot(
        source_id=source.source_id,
        role=source.role,
        service_name=_opendtu_source_name(source, settings),
        ac_power_w=ac_power,
        pv_input_power_w=pv_input_power,
        operating_mode="producing" if _opendtu_any_producing(inverters) else "idle",
        online=online,
        confidence=confidence,
        captured_at=now,
    )


def _opendtu_source_name(source: EnergySourceDefinition, settings: OpenDtuEnergySourceSettings) -> str:
    if source.service_name:
        return source.service_name
    if settings.base_url:
        return settings.base_url
    return source.config_path or source.source_id


def _opendtu_timeout_seconds(runtime: Any, adapter: Any) -> float:
    default_timeout = float(getattr(runtime, "shelly_request_timeout_seconds", None) or _DEFAULT_TIMEOUT_SECONDS)
    timeout = finite_float_or_none(adapter.get(_REQUEST_TIMEOUT_KEY))
    return default_timeout if timeout is None or timeout <= 0.0 else float(timeout)


def _opendtu_max_data_age_seconds(opendtu: Any) -> float:
    max_data_age = finite_float_or_none(opendtu.get(_MAX_DATA_AGE_KEY))
    return _DEFAULT_MAX_DATA_AGE_SECONDS if max_data_age is None or max_data_age < 0.0 else float(max_data_age)


def _section_text(section: Any, key: str, default: str = "") -> str:
    value = section.get(key)
    if value is None:
        return default
    return str(value).strip() or default


def _opendtu_energy_source_settings(runtime: Any, source: EnergySourceDefinition) -> OpenDtuEnergySourceSettings:
    cache = _typed_cache_map(runtime, "_energy_opendtu_settings_cache", OpenDtuEnergySourceSettings)
    cache_key = str(source.config_path).strip()
    cached = cache.get(cache_key)
    if isinstance(cached, OpenDtuEnergySourceSettings):
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
        serial_filter=_csv_filter(opendtu.get(_INVERTER_SERIALS_KEY)),
        max_data_age_seconds=_opendtu_max_data_age_seconds(opendtu),
    )
    _validate_opendtu_energy_source_settings(source, settings)
    cache[cache_key] = settings
    return settings


def _validate_opendtu_energy_source_settings(source: EnergySourceDefinition, settings: OpenDtuEnergySourceSettings) -> None:
    if not settings.status_url:
        raise ValueError(f"Energy source '{source.source_id}' requires OpenDTU.StatusUrl or Adapter.BaseUrl")


def _opendtu_selected_inverters(
    payload: dict[str, object],
    settings: OpenDtuEnergySourceSettings,
    client: TemplateHttpBackendBase,
) -> tuple[dict[str, object], ...]:
    raw_inverters = payload.get("inverters")
    if not isinstance(raw_inverters, list):
        return ()
    filtered = _opendtu_filtered_raw_inverters(raw_inverters, settings.serial_filter)
    return tuple(
        inverter
        for inverter in (
            _opendtu_selected_inverter(raw_inverter, settings, client)
            for raw_inverter in filtered
        )
        if inverter is not None
    )


def _opendtu_filtered_raw_inverters(
    raw_inverters: list[object],
    serial_filter: tuple[str, ...],
) -> tuple[dict[str, object], ...]:
    filtered: list[dict[str, object]] = []
    for raw_inverter in raw_inverters:
        if isinstance(raw_inverter, dict) and _opendtu_filtered_raw_inverter(raw_inverter, serial_filter):
            filtered.append(raw_inverter)
    return tuple(filtered)


def _opendtu_filtered_raw_inverter(
    raw_inverter: object,
    serial_filter: tuple[str, ...],
) -> bool:
    return isinstance(raw_inverter, dict) and _opendtu_matches_serial_filter(
        raw_inverter, serial_filter
    )


def _opendtu_matches_serial_filter(inverter: dict[str, object], serial_filter: tuple[str, ...]) -> bool:
    serial = _opendtu_serial(inverter)
    return not serial_filter or serial in serial_filter


def _opendtu_selected_inverter(
    raw_inverter: dict[str, object],
    settings: OpenDtuEnergySourceSettings,
    client: TemplateHttpBackendBase,
) -> dict[str, object] | None:
    serial = _opendtu_serial(raw_inverter)
    if "AC" in raw_inverter or _opendtu_unreachable_idle_stub(raw_inverter):
        return raw_inverter
    if not serial:
        return None
    detail = client._perform_request("GET", settings.inverter_status_url, context={"serial": serial})
    return _opendtu_detail_inverter(detail)


def _opendtu_serial(inverter: dict[str, object]) -> str:
    value = inverter.get("serial")
    return "" if value is None else str(value).strip()


def _opendtu_detail_inverter(payload: dict[str, object]) -> dict[str, object] | None:
    raw_inverters = payload.get("inverters")
    if not isinstance(raw_inverters, list) or not raw_inverters:
        return None
    first = raw_inverters[0]
    return {str(key): value for key, value in first.items()} if isinstance(first, dict) else None


def _opendtu_total_ac_power(
    payload: dict[str, object],
    inverters: tuple[dict[str, object], ...],
    serial_filter: tuple[str, ...],
) -> float | None:
    if serial_filter:
        return _opendtu_summed_ac_power(inverters)
    total_power = _opendtu_payload_total_power(payload)
    if total_power is not None:
        return total_power
    return _opendtu_summed_ac_power(inverters)


def _opendtu_payload_total_power(payload: dict[str, object]) -> float | None:
    total = payload.get("total")
    if not isinstance(total, dict):
        return None
    return _opendtu_metric_value(total, "Power")


def _opendtu_summed_ac_power(inverters: tuple[dict[str, object], ...]) -> float | None:
    return _sum_optional(_opendtu_ac_power(inverter) for inverter in inverters)


def _opendtu_total_dc_power(inverters: tuple[dict[str, object], ...]) -> float | None:
    return _sum_optional(_opendtu_dc_power(inverter) for inverter in inverters)


def _opendtu_any_producing(inverters: tuple[dict[str, object], ...]) -> bool:
    return any(bool(inverter.get("producing")) for inverter in inverters)


def _opendtu_has_online_inverter(
    inverters: tuple[dict[str, object], ...],
    max_data_age_seconds: float,
) -> bool:
    return any(_opendtu_inverter_online(inverter, max_data_age_seconds) for inverter in inverters)


def _opendtu_has_radio_problem(payload: dict[str, object]) -> bool:
    hints = payload.get("hints")
    return isinstance(hints, dict) and bool(hints.get("radio_problem"))


def _opendtu_all_unreachable_idle_stubs(inverters: tuple[dict[str, object], ...]) -> bool:
    return all(_opendtu_unreachable_idle_stub(inverter) for inverter in inverters)


def _opendtu_plausible_idle_snapshot(
    payload: dict[str, object],
    inverters: tuple[dict[str, object], ...],
    *,
    ac_power_w: float | None,
    pv_input_power_w: float | None,
    max_data_age_seconds: float,
    allow_unreachable_idle: bool,
) -> bool:
    checks = (
        allow_unreachable_idle,
        bool(inverters),
        not _opendtu_any_producing(inverters),
        not _opendtu_has_online_inverter(inverters, max_data_age_seconds),
        _opendtu_zeroish_power(ac_power_w),
        _opendtu_zeroish_power(pv_input_power_w),
        not _opendtu_has_radio_problem(payload),
        _opendtu_all_unreachable_idle_stubs(inverters),
    )
    return all(checks)


def _opendtu_unreachable_idle_stub(inverter: dict[str, object]) -> bool:
    return not bool(inverter.get("reachable")) and not bool(inverter.get("producing"))


def _energy_source_allows_unreachable_idle(source: EnergySourceDefinition) -> bool:
    profile = resolve_energy_source_profile(source.profile_name)
    if profile is not None:
        return profile.idle_unreachable_policy == "allow_plausible_idle"
    return source.role == "inverter"


def _opendtu_inverter_online(inverter: dict[str, object], max_data_age_seconds: float) -> bool:
    reachable = bool(inverter.get("reachable"))
    if not reachable:
        return False
    data_age = finite_float_or_none(inverter.get("data_age"))
    if data_age is None:
        return reachable
    return float(data_age) <= float(max_data_age_seconds)


def _opendtu_ac_power(inverter: dict[str, object]) -> float | None:
    ac = inverter.get("AC")
    if not isinstance(ac, dict):
        return None
    phase = ac.get("0")
    if not isinstance(phase, dict):
        return None
    return _opendtu_metric_value(phase, "Power")


def _opendtu_dc_power(inverter: dict[str, object]) -> float | None:
    dc = inverter.get("DC")
    if not isinstance(dc, dict):
        return None
    values: list[float] = []
    for channel in dc.values():
        if not isinstance(channel, dict):
            continue
        power = _opendtu_metric_value(channel, "Power")
        if power is not None:
            values.append(power)
    return _sum_optional(values)


def _opendtu_metric_value(container: dict[str, object], key: str) -> float | None:
    raw_metric = container.get(key)
    if not isinstance(raw_metric, dict):
        return None
    return finite_float_or_none(raw_metric.get("v"))


def _opendtu_zeroish_power(value: float | None) -> bool:
    return value is None or abs(float(value)) <= 0.5
