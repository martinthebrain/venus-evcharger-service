# SPDX-License-Identifier: GPL-3.0-or-later
"""Import helpers for cloning or seeding wizard answers from an existing config."""

from __future__ import annotations

import configparser
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from venus_evcharger.bootstrap.wizard_choices import optional_choice, recognized_choice
from venus_evcharger.bootstrap.wizard_models import (
    WIZARD_CHARGER_BACKENDS,
    WIZARD_POLICY_MODES,
    WIZARD_PROFILES,
    WIZARD_TRANSPORT_KINDS,
    WizardChargerBackend,
    WizardPolicyMode,
    WizardProfile,
    WizardTransportKind,
)
from venus_evcharger.bootstrap.wizard_support import (
    NATIVE_CHARGER_VALUES,
    PHASE_SWITCH_CHARGER_VALUES,
    backend_requires_transport,
)
from venus_evcharger.bootstrap.wizard_import_contracts import (
    KEY_BASE_URL as _KEY_BASE_URL,
    KEY_AUTO_MIN_SOC as _KEY_AUTO_MIN_SOC,
    KEY_AUTO_RESUME_SOC as _KEY_AUTO_RESUME_SOC,
    KEY_AUTO_SCHEDULED_DAYS as _KEY_AUTO_SCHEDULED_DAYS,
    KEY_AUTO_SCHEDULED_LATEST_END as _KEY_AUTO_SCHEDULED_LATEST_END,
    KEY_AUTO_SCHEDULED_NIGHT_AMPS as _KEY_AUTO_SCHEDULED_NIGHT_AMPS,
    KEY_AUTO_START_SURPLUS as _KEY_AUTO_START_SURPLUS,
    KEY_AUTO_STOP_SURPLUS as _KEY_AUTO_STOP_SURPLUS,
    KEY_CHARGER_CONFIG as _KEY_CHARGER_CONFIG,
    KEY_CHARGER_TYPE as _KEY_CHARGER_TYPE,
    KEY_DEVICE as _KEY_DEVICE,
    KEY_DEVICE_INSTANCE as _KEY_DEVICE_INSTANCE,
    KEY_DIGEST_AUTH as _KEY_DIGEST_AUTH,
    KEY_HOST as _KEY_HOST,
    KEY_METER_CONFIG as _KEY_METER_CONFIG,
    KEY_METER_TYPE as _KEY_METER_TYPE,
    KEY_MODE as _KEY_MODE,
    KEY_P1 as _KEY_P1,
    KEY_PASSWORD as _KEY_PASSWORD,
    KEY_PHASE as _KEY_PHASE,
    KEY_PORT as _KEY_PORT,
    KEY_PRESET as _KEY_PRESET,
    KEY_REQUEST_TIMEOUT as _KEY_REQUEST_TIMEOUT,
    KEY_SUPPORTED_PHASES as _KEY_SUPPORTED_PHASES,
    KEY_SWITCH_CONFIG as _KEY_SWITCH_CONFIG,
    KEY_SWITCH_TYPE as _KEY_SWITCH_TYPE,
    KEY_TRANSPORT as _KEY_TRANSPORT,
    KEY_UNIT_ID as _KEY_UNIT_ID,
    KEY_USERNAME as _KEY_USERNAME,
    PROFILE_DEFAULTS_BY_BACKENDS as _PROFILE_DEFAULTS_BY_BACKENDS,
    SECTION_ADAPTER as _SECTION_ADAPTER,
    SECTION_BACKENDS as _SECTION_BACKENDS,
    SECTION_CAPABILITIES as _SECTION_CAPABILITIES,
    SECTION_DEFAULT as _SECTION_DEFAULT,
    SECTION_MEMBERS as _SECTION_MEMBERS,
    SECTION_TRANSPORT as _SECTION_TRANSPORT,
)

ConfigValues = Mapping[str, str] | configparser.SectionProxy


@dataclass(frozen=True)
class ImportedWizardDefaults:
    imported_from: str
    profile: WizardProfile | None
    host_input: str | None
    meter_host_input: str | None
    switch_host_input: str | None
    charger_host_input: str | None
    device_instance: int | None
    phase: str | None
    policy_mode: WizardPolicyMode | None
    digest_auth: bool | None
    username: str | None
    password: str | None
    topology_preset: str | None
    charger_backend: WizardChargerBackend | None
    charger_preset: str | None
    request_timeout_seconds: float | None
    switch_group_phase_layout: str | None
    auto_start_surplus_watts: float | None
    auto_stop_surplus_watts: float | None
    auto_min_soc: float | None
    auto_resume_soc: float | None
    scheduled_enabled_days: str | None
    scheduled_latest_end_time: str | None
    scheduled_night_current_amps: float | None
    transport_kind: WizardTransportKind | None
    transport_host: str | None
    transport_port: int | None
    transport_device: str | None
    transport_unit_id: int | None
    inventory_path: str | None = None


def _sibling_inventory_path(config_path: Path) -> str | None:
    candidate = config_path.with_name(f"{config_path.name}.wizard-inventory.ini")
    return str(candidate) if candidate.exists() else None


def _config_parser(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    with path.open(encoding="utf-8") as handle:
        parser.read_file(handle)
    return parser


def _parser_defaults(parser: configparser.ConfigParser) -> ConfigValues:
    return parser[_SECTION_DEFAULT] if _SECTION_DEFAULT in parser else parser.defaults()


def _parser_section_or_defaults(parser: configparser.ConfigParser, section: str) -> ConfigValues:
    return parser[section] if parser.has_section(section) else _parser_defaults(parser)


def _section_text(values: ConfigValues, key: str) -> str:
    value = values.get(key)
    if value is None:
        return ""
    return str(value).strip()


def _section_optional(values: ConfigValues, key: str) -> str | None:
    value = _section_text(values, key)
    return value or None


def _as_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    return int(value)


def _as_float(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    return float(value)


def _adapter_path(config_path: Path, backends: configparser.SectionProxy | None, key: str) -> Path | None:
    if backends is None:
        return None
    adapter_path_value = backends.get(key)
    if not adapter_path_value:
        return None
    adapter_path = Path(adapter_path_value)
    if not adapter_path.is_absolute():
        adapter_path = config_path.parent / adapter_path
    return adapter_path if adapter_path.exists() else None


def _adapter_host_value(adapter_path: Path | None) -> str | None:
    if adapter_path is None:
        return None
    adapter = _config_parser(adapter_path)
    adapter_section = _parser_section_or_defaults(adapter, _SECTION_ADAPTER)
    return _section_optional(adapter_section, _KEY_HOST) or _section_optional(adapter_section, _KEY_BASE_URL)


def _switch_group_host_value(adapter_path: Path | None) -> str | None:
    if adapter_path is None:
        return None
    adapter = _config_parser(adapter_path)
    members = adapter[_SECTION_MEMBERS] if adapter.has_section(_SECTION_MEMBERS) else None
    if members is None:
        return _adapter_host_value(adapter_path)
    return _switch_group_member_host(adapter_path, members.get(_KEY_P1))


def _switch_group_member_host(adapter_path: Path, phase_path_value: str | None) -> str | None:
    if not phase_path_value:
        return None
    phase_path = Path(phase_path_value)
    if not phase_path.is_absolute():
        phase_path = adapter_path.parent / phase_path
    return _adapter_host_value(phase_path if phase_path.exists() else None)


def _policy_mode(value: str | None) -> WizardPolicyMode | None:
    if value is None:
        return None
    normalized = value.strip()
    if normalized == "1":
        return "auto"
    if normalized == "2":
        return "scheduled"
    if normalized == "0":
        return "manual"
    return None


def _profile_defaults(backends: configparser.SectionProxy | None) -> tuple[WizardProfile | None, str | None, WizardChargerBackend | None]:
    if backends is None:
        return "simple_relay", None, None
    return _profile_defaults_from_types(*_backend_types(backends))


def _profile_defaults_from_types(
    meter_type: str,
    switch_type: str,
    charger_type: str,
) -> tuple[WizardProfile | None, str | None, WizardChargerBackend | None]:
    backend = recognized_choice(charger_type or None, WIZARD_CHARGER_BACKENDS)
    native_defaults = _native_profile_defaults(meter_type, switch_type, charger_type, backend)
    if native_defaults is not None:
        return native_defaults
    preset_match = _PROFILE_DEFAULTS_BY_BACKENDS.get((meter_type, switch_type, charger_type))
    if preset_match is not None:
        return preset_match
    return ("advanced_manual", None, backend) if any((meter_type, switch_type, charger_type)) else (None, None, None)


def _backend_types(backends: configparser.SectionProxy) -> tuple[str, str, str]:
    return (
        _section_text(backends, _KEY_METER_TYPE),
        _section_text(backends, _KEY_SWITCH_TYPE),
        _section_text(backends, _KEY_CHARGER_TYPE),
    )


def _native_profile_defaults(
    meter_type: str,
    switch_type: str,
    charger_type: str,
    backend: WizardChargerBackend | None,
) -> tuple[WizardProfile, str | None, WizardChargerBackend | None] | None:
    if (meter_type, switch_type) == ("none", "none") and charger_type in NATIVE_CHARGER_VALUES:
        return "native_device", None, backend
    if switch_type == "switch_group" and charger_type in PHASE_SWITCH_CHARGER_VALUES:
        return "hybrid_topology", None, backend
    return None


def _transport_defaults(config_path: Path, backends: configparser.SectionProxy | None, backend: str | None) -> tuple[
    WizardTransportKind | None,
    str | None,
    int | None,
    str | None,
    int | None,
]:
    adapter_path = _charger_adapter_path(config_path, backends, backend)
    if adapter_path is None:
        return None, None, None, None, None
    adapter = _config_parser(adapter_path)
    adapter_section = _parser_section_or_defaults(adapter, _SECTION_ADAPTER)
    transport_kind = recognized_choice(adapter_section.get(_KEY_TRANSPORT), WIZARD_TRANSPORT_KINDS)
    transport_section = _parser_section_or_defaults(adapter, _SECTION_TRANSPORT)
    return (
        transport_kind,
        transport_section.get(_KEY_HOST),
        _as_int(transport_section.get(_KEY_PORT)),
        transport_section.get(_KEY_DEVICE),
        _as_int(transport_section.get(_KEY_UNIT_ID)),
    )


def _charger_adapter_path(config_path: Path, backends: configparser.SectionProxy | None, backend: str | None) -> Path | None:
    if not backend_requires_transport(backend) or backends is None:
        return None
    return _adapter_path(config_path, backends, _KEY_CHARGER_CONFIG)


def _request_timeout_seconds(config_path: Path, backends: configparser.SectionProxy | None, backend: str | None) -> float | None:
    adapter_path = _goe_charger_adapter_path(config_path, backends, backend)
    if adapter_path is None:
        return None
    adapter = _config_parser(adapter_path)
    adapter_section = _parser_section_or_defaults(adapter, _SECTION_ADAPTER)
    return _as_float(adapter_section.get(_KEY_REQUEST_TIMEOUT))


def _charger_preset(config_path: Path, backends: configparser.SectionProxy | None) -> str | None:
    adapter_path = _adapter_path(config_path, backends, _KEY_CHARGER_CONFIG)
    if adapter_path is None:
        return None
    adapter = _config_parser(adapter_path)
    adapter_section = _parser_section_or_defaults(adapter, _SECTION_ADAPTER)
    return _section_optional(adapter_section, _KEY_PRESET)


def _goe_charger_adapter_path(config_path: Path, backends: configparser.SectionProxy | None, backend: str | None) -> Path | None:
    if backend != "goe_charger":
        return None
    return _adapter_path(config_path, backends, _KEY_CHARGER_CONFIG)


def _switch_group_phase_layout(config_path: Path, backends: configparser.SectionProxy | None) -> str | None:
    adapter_path = _adapter_path(config_path, backends, _KEY_SWITCH_CONFIG)
    if adapter_path is None:
        return None
    adapter = _config_parser(adapter_path)
    if not adapter.has_section(_SECTION_CAPABILITIES):
        return None
    return adapter[_SECTION_CAPABILITIES].get(_KEY_SUPPORTED_PHASES)


def _json_str(value: object) -> str | None:
    if value is None or isinstance(value, str):
        return value
    raise ValueError(f"Wizard result field must be a string or null: {value!r}")


def _json_bool(value: object) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    raise ValueError(f"Wizard result field must be a boolean or null: {value!r}")


def _json_int(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is int:
        return value
    raise ValueError(f"Wizard result field must be an integer or null: {value!r}")


def _json_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    raise ValueError(f"Wizard result field must be numeric or null: {value!r}")


def _load_from_result_json(config_path: Path) -> ImportedWizardDefaults:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Wizard result does not contain a JSON object: {config_path}")
    defaults = payload.get("answer_defaults")
    if not isinstance(defaults, dict):
        raise ValueError(f"Wizard result is missing answer_defaults: {config_path}")
    return ImportedWizardDefaults(
        imported_from=str(config_path),
        profile=optional_choice(defaults.get("profile"), WIZARD_PROFILES, "profile"),
        host_input=_json_str(defaults.get("host_input")),
        meter_host_input=_json_str(defaults.get("meter_host_input")),
        switch_host_input=_json_str(defaults.get("switch_host_input")),
        charger_host_input=_json_str(defaults.get("charger_host_input")),
        device_instance=_json_int(defaults.get("device_instance")),
        phase=_json_str(defaults.get("phase")),
        policy_mode=optional_choice(defaults.get("policy_mode"), WIZARD_POLICY_MODES, "policy mode"),
        digest_auth=_json_bool(defaults.get("digest_auth")),
        username=_json_str(defaults.get("username")),
        password=None,
        topology_preset=_json_str(defaults.get("topology_preset")),
        charger_backend=optional_choice(defaults.get("charger_backend"), WIZARD_CHARGER_BACKENDS, "charger backend"),
        charger_preset=_json_str(defaults.get("charger_preset")),
        request_timeout_seconds=_json_float(defaults.get("request_timeout_seconds")),
        switch_group_phase_layout=_json_str(defaults.get("switch_group_supported_phase_selections")),
        auto_start_surplus_watts=_json_float(defaults.get("auto_start_surplus_watts")),
        auto_stop_surplus_watts=_json_float(defaults.get("auto_stop_surplus_watts")),
        auto_min_soc=_json_float(defaults.get("auto_min_soc")),
        auto_resume_soc=_json_float(defaults.get("auto_resume_soc")),
        scheduled_enabled_days=_json_str(defaults.get("scheduled_enabled_days")),
        scheduled_latest_end_time=_json_str(defaults.get("scheduled_latest_end_time")),
        scheduled_night_current_amps=_json_float(defaults.get("scheduled_night_current_amps")),
        transport_kind=optional_choice(defaults.get("transport_kind"), WIZARD_TRANSPORT_KINDS, "transport"),
        transport_host=_json_str(defaults.get("transport_host")),
        transport_port=_json_int(defaults.get("transport_port")),
        transport_device=_json_str(defaults.get("transport_device")),
        transport_unit_id=_json_int(defaults.get("transport_unit_id")),
        inventory_path=_json_str(payload.get("inventory_path")) or _sibling_inventory_path(config_path),
    )


def load_imported_defaults(config_path: Path) -> ImportedWizardDefaults:
    if not config_path.exists():
        raise ValueError(f"Import config does not exist: {config_path}")
    if config_path.name.endswith(".wizard-result.json"):
        return _load_from_result_json(config_path)
    parser = _config_parser(config_path)
    defaults = _parser_defaults(parser)
    backends = parser[_SECTION_BACKENDS] if parser.has_section(_SECTION_BACKENDS) else None
    profile, topology_preset, charger_backend = _profile_defaults(backends)
    meter_host_input = _adapter_host_value(_adapter_path(config_path, backends, _KEY_METER_CONFIG))
    switch_host_input = _switch_group_host_value(_adapter_path(config_path, backends, _KEY_SWITCH_CONFIG))
    charger_host_input = _adapter_host_value(_adapter_path(config_path, backends, _KEY_CHARGER_CONFIG))
    transport_kind, transport_host, transport_port, transport_device, transport_unit_id = _transport_defaults(
        config_path,
        backends,
        charger_backend,
    )
    return ImportedWizardDefaults(
        imported_from=str(config_path),
        profile=profile,
        host_input=defaults.get(_KEY_HOST),
        meter_host_input=meter_host_input,
        switch_host_input=switch_host_input,
        charger_host_input=charger_host_input,
        device_instance=_as_int(defaults.get(_KEY_DEVICE_INSTANCE)),
        phase=defaults.get(_KEY_PHASE),
        policy_mode=_policy_mode(defaults.get(_KEY_MODE)),
        digest_auth=_as_bool(defaults.get(_KEY_DIGEST_AUTH)),
        username=defaults.get(_KEY_USERNAME),
        password=defaults.get(_KEY_PASSWORD),
        topology_preset=topology_preset,
        charger_backend=charger_backend,
        charger_preset=_charger_preset(config_path, backends),
        request_timeout_seconds=_request_timeout_seconds(config_path, backends, charger_backend),
        switch_group_phase_layout=_switch_group_phase_layout(config_path, backends),
        auto_start_surplus_watts=_as_float(defaults.get(_KEY_AUTO_START_SURPLUS)),
        auto_stop_surplus_watts=_as_float(defaults.get(_KEY_AUTO_STOP_SURPLUS)),
        auto_min_soc=_as_float(defaults.get(_KEY_AUTO_MIN_SOC)),
        auto_resume_soc=_as_float(defaults.get(_KEY_AUTO_RESUME_SOC)),
        scheduled_enabled_days=defaults.get(_KEY_AUTO_SCHEDULED_DAYS),
        scheduled_latest_end_time=defaults.get(_KEY_AUTO_SCHEDULED_LATEST_END),
        scheduled_night_current_amps=_as_float(defaults.get(_KEY_AUTO_SCHEDULED_NIGHT_AMPS)),
        transport_kind=transport_kind,
        transport_host=transport_host,
        transport_port=transport_port,
        transport_device=transport_device,
        transport_unit_id=transport_unit_id,
        inventory_path=_sibling_inventory_path(config_path),
    )
