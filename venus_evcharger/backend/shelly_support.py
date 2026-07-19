# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared helpers for Shelly-backed meter and switch backends.

Shelly adapters share profile defaults, phase mapping, RPC auth, and signal
readback handling while individual meter/switch classes keep role behavior.
"""

from __future__ import annotations

import configparser
from typing import Mapping
from urllib.parse import urlencode

import requests
from requests.auth import HTTPDigestAuth

from .config_file import config_section, section_is_effectively_empty
from .models import (
    PhaseSelection,
    SwitchingMode,
    normalize_phase_selection,
)
from .shelly_io_types import (
    ShellyHttpSession,
    is_object_mapping,
    normalized_json_object,
    require_session,
)
from .shelly_profiles import (
    ShellyProfileDefaults,
    normalize_shelly_profile_name,
    resolve_shelly_profile,
    validate_shelly_profile_role,
)
from .shelly_support_phase import (
    _parse_switch_channel_ids,
    _phase_switch_targets,
    _switch_channel_id,
    normalize_switching_mode,
    parse_phase_selection_list,
    phase_currents_for_selection,
    phase_powers_for_selection,
)
from .shelly_support_types import ShellyBackendSettings, ShellySignalReadbackSettings
from venus_evcharger.core.contracts import finite_float_or_none, normalize_binary_flag
from venus_evcharger.backend.shelly_io import JsonObject, ShellyPmStatus, ShellyRpcScalar


ConfigValues = Mapping[str, str]


def _section_values(section: configparser.SectionProxy) -> dict[str, str]:
    """Return one case-insensitive, testable view of a config section."""
    return {str(key).strip().lower(): str(value) for key, value in section.items()}


def _config_value(defaults: ConfigValues, key: str, fallback: object) -> str:
    """Return one config value with a typed fallback."""
    return defaults.get(key, str(fallback))


def _optional_config_value(defaults: ConfigValues, key: str) -> str | None:
    """Return one optional normalized config value."""
    return defaults.get(key)


def _binary_config_flag(defaults: ConfigValues, key: str, *, default: bool = False) -> bool:
    """Return one bool config flag without hiding absent values in coercion defaults."""
    raw = _optional_config_value(defaults, key)
    if raw is None:
        return bool(default)
    return bool(normalize_binary_flag(raw))


def _default_phase_selection(
    profile_defaults: ShellyProfileDefaults | None,
    service: object,
) -> PhaseSelection:
    """Return the profile or service default measured phase selection."""
    if profile_defaults is not None and profile_defaults.default_phase_selection is not None:
        return profile_defaults.default_phase_selection
    raw = getattr(service, "phase", None)
    return "P1" if raw is None else normalize_phase_selection(raw)


def _service_digest_auth_default(service: object) -> bool:
    """Return the service-level digest-auth default when explicitly present."""
    return bool(getattr(service, "use_digest_auth")) if hasattr(service, "use_digest_auth") else False


def _config(defaults_path: str) -> configparser.ConfigParser:
    """Load one backend config file, or return an empty parser for inline defaults."""
    parser = configparser.ConfigParser()
    if defaults_path:
        read_files = parser.read(defaults_path)
        if not read_files:
            raise FileNotFoundError(defaults_path)
    return parser


def _empty_signal_section(section: configparser.SectionProxy) -> bool:
    """Return whether one optional signal-readback section is effectively absent."""
    return section_is_effectively_empty(section)


def _default_signal_value_path(component: str) -> str:
    """Return the most likely scalar status field for one Shelly component."""
    normalized = str(component).strip().lower()
    if normalized == "switch":
        return "output"
    return "state"


def _optional_signal_readback_settings(
    section: configparser.SectionProxy,
    *,
    default_component: str = "Input",
    default_id: int = 0,
) -> ShellySignalReadbackSettings | None:
    """Return one optional Shelly signal-readback descriptor from config."""
    if _empty_signal_section(section):
        return None
    values = _section_values(section)
    component = _config_value(values, "component", default_component).strip() or str(default_component)
    device_id = int(_config_value(values, "id", default_id))
    value_path = _config_value(values, "valuepath", _default_signal_value_path(component)).strip()
    if not value_path:
        raise ValueError(f"Shelly signal readback for [{section.name}] requires ValuePath")
    return ShellySignalReadbackSettings(
        component=component,
        device_id=device_id,
        value_path=value_path,
        invert=_binary_config_flag(values, "invert"),
    )


def _mapping_path_value(payload: Mapping[str, object], path: str) -> object:
    """Return one nested mapping value addressed by a dotted path."""
    current: object = dict(payload)
    for part in str(path).split("."):
        token = part.strip()
        if not token:
            continue
        if not is_object_mapping(current) or token not in current:
            raise ValueError(f"Missing Shelly signal response path '{path}'")
        current = current[token]
    return current


def load_shelly_backend_settings(
    service: object,
    config_path: str = "",
    *,
    default_switching_mode: SwitchingMode = "direct",
) -> ShellyBackendSettings:
    """Return one normalized Shelly backend config from file plus service defaults."""
    parser = _config(str(config_path).strip())
    adapter = config_section(parser, "Adapter")
    phase = config_section(parser, "Phase")
    capabilities = config_section(parser, "Capabilities")
    phase_map = config_section(parser, "PhaseMap")
    feedback = config_section(parser, "Feedback")
    interlock = config_section(parser, "Interlock")
    adapter_values = _section_values(adapter)
    capability_values = _section_values(capabilities)
    profile_name = normalize_shelly_profile_name(_config_value(adapter_values, "shellyprofile", ""))
    profile_defaults = resolve_shelly_profile(profile_name)
    default_phase = _default_phase_selection(profile_defaults, service)
    device_id = int(
        _config_value(
            adapter_values,
            "id",
            profile_defaults.device_id if profile_defaults is not None else getattr(service, "pm_id", 0),
        )
    )
    switching_mode = _resolved_switching_mode(capabilities, default_switching_mode)
    supported_phase_selections = _supported_phase_selections(capabilities)
    max_power = _resolved_max_direct_switch_power_w(service, capabilities, switching_mode)
    component = _resolved_shelly_component(adapter, profile_defaults, service)
    return ShellyBackendSettings(
        profile_name=profile_name,
        host=_config_value(adapter_values, "host", getattr(service, "host", "")).strip(),
        component=component,
        device_id=device_id,
        timeout_seconds=_resolved_timeout_seconds(adapter, service),
        username=_config_value(adapter_values, "username", getattr(service, "username", "")).strip(),
        password=_config_value(adapter_values, "password", getattr(service, "password", "")).strip(),
        use_digest_auth=_binary_config_flag(
            adapter_values,
            "digestauth",
            default=_service_digest_auth_default(service),
        ),
        phase_selection=_resolved_phase_selection(phase, default_phase),
        switching_mode=switching_mode,
        supported_phase_selections=supported_phase_selections,
        requires_charge_pause_for_phase_change=_binary_config_flag(
            capability_values,
            "requireschargepauseforphasechange",
        ),
        max_direct_switch_power_w=max_power,
        phase_switch_targets=_phase_switch_targets(phase_map, device_id, supported_phase_selections),
        feedback_readback=_optional_signal_readback_settings(feedback),
        interlock_readback=_optional_signal_readback_settings(interlock),
    )


def _resolved_shelly_component(
    adapter: configparser.SectionProxy,
    profile_defaults: ShellyProfileDefaults | None,
    service: object,
) -> str:
    """Return the effective Shelly RPC component for one backend."""
    values = _section_values(adapter)
    default_component = profile_defaults.component if profile_defaults is not None else getattr(service, "pm_component", "Switch")
    return _config_value(values, "component", default_component).strip() or "Switch"


def _resolved_switching_mode(
    capabilities: configparser.SectionProxy,
    default_switching_mode: SwitchingMode,
) -> SwitchingMode:
    """Return the normalized switching mode from backend capabilities."""
    values = _section_values(capabilities)
    return normalize_switching_mode(
        values.get("switchingmode"),
        default_switching_mode,
    )


def _supported_phase_selections(capabilities: configparser.SectionProxy) -> tuple[PhaseSelection, ...]:
    """Return the normalized supported phase selections from backend capabilities."""
    values = _section_values(capabilities)
    raw = _optional_config_value(values, "supportedphaseselections")
    return ("P1",) if raw is None else parse_phase_selection_list(raw)


def _configured_max_direct_switch_power_w(capabilities: configparser.SectionProxy) -> float | None:
    """Return the explicitly configured direct-switch power limit when present."""
    values = _section_values(capabilities)
    return finite_float_or_none(values.get("maxdirectswitchpowerwatts"))


def _derived_max_direct_switch_power_w(service: object) -> float | None:
    """Return a fallback direct-switch power limit from service max current and voltage."""
    max_current = finite_float_or_none(getattr(service, "max_current", None))
    voltage = finite_float_or_none(getattr(service, "_last_voltage", None))
    if max_current is None or voltage is None or max_current <= 0 or voltage <= 0:
        return None
    return max_current * voltage


def _resolved_max_direct_switch_power_w(
    service: object,
    capabilities: configparser.SectionProxy,
    switching_mode: SwitchingMode,
) -> float | None:
    """Return the active direct-switch power limit implied by capabilities and service defaults."""
    if switching_mode == "contactor":
        return None
    configured_power = _configured_max_direct_switch_power_w(capabilities)
    return configured_power if configured_power is not None else _derived_max_direct_switch_power_w(service)


def _resolved_timeout_seconds(adapter: configparser.SectionProxy, service: object) -> float:
    """Return the normalized Shelly backend timeout in seconds."""
    values = _section_values(adapter)
    return float(_config_value(values, "requesttimeoutseconds", getattr(service, "shelly_request_timeout_seconds", 2.0)))


def _resolved_phase_selection(
    phase: configparser.SectionProxy,
    default_phase: PhaseSelection,
) -> PhaseSelection:
    """Return the normalized measured phase selection for one Shelly backend."""
    values = _section_values(phase)
    raw = _optional_config_value(values, "measuredphaseselection")
    if raw is None:
        raw = _optional_config_value(values, "measuredphase")
    return default_phase if raw is None else normalize_phase_selection(raw, default_phase)


def _has_credentials(username: str, password: str) -> bool:
    """Return whether Shelly HTTP credentials are configured."""
    return bool(username and password)


class ShellyBackendBase:
    """Common Shelly RPC/config helpers shared by separate backend implementations."""

    def __init__(
        self,
        service: object,
        config_path: str = "",
        *,
        default_switching_mode: SwitchingMode = "direct",
    ) -> None:
        self.service = service
        self.config_path = str(config_path).strip()
        self.settings = load_shelly_backend_settings(
            service,
            self.config_path,
            default_switching_mode=default_switching_mode,
        )
        session = getattr(service, "session", None)
        self._session: ShellyHttpSession = (
            require_session(session) if session is not None else requests.Session()
        )

    def reset_transport_session(self, session: ShellyHttpSession | None = None) -> None:
        """Replace the HTTP session after transport-level failures."""
        old_session = getattr(self, "_session", None)
        if old_session is not None and old_session is not session and hasattr(old_session, "close"):
            old_session.close()
        self._session = session if session is not None else requests.Session()

    def _auth(self) -> HTTPDigestAuth | tuple[str, str] | None:
        """Return one optional auth object for Shelly HTTP calls."""
        settings = self.settings
        if not _has_credentials(settings.username, settings.password):
            return None
        if settings.use_digest_auth:
            return HTTPDigestAuth(settings.username, settings.password)
        return settings.username, settings.password

    @staticmethod
    def _encoded_rpc_params(params: Mapping[str, ShellyRpcScalar]) -> dict[str, str | int | float]:
        """Encode Shelly RPC parameters, keeping booleans lowercase."""
        encoded: dict[str, str | int | float] = {}
        for key, value in params.items():
            encoded[key] = str(value).lower() if isinstance(value, bool) else value
        return encoded

    def _rpc_url(self, method: str, params: Mapping[str, ShellyRpcScalar] | None = None) -> str:
        """Return one Shelly RPC URL for the configured backend target."""
        base = f"http://{self.settings.host}/rpc/{method}"
        if not params:
            return base
        return f"{base}?{urlencode(self._encoded_rpc_params(params))}"

    def _request_json(self, url: str) -> JsonObject:
        """Perform one requests-based JSON call."""
        auth = self._auth()
        timeout = float(self.settings.timeout_seconds)
        response = (
            self._session.get(url=url, timeout=timeout, auth=auth)
            if auth is not None
            else self._session.get(url=url, timeout=timeout)
        )
        response.raise_for_status()
        return normalized_json_object(
            response.json(),
            error_message="Shelly RPC response must be a JSON object",
        )

    def _rpc_call(self, method: str, **params: ShellyRpcScalar) -> JsonObject:
        """Call one Shelly RPC method on the configured backend target."""
        return self._request_json(self._rpc_url(method, params))

    def _pm_status(self) -> JsonObject:
        """Return one Shelly PM status payload."""
        return self._rpc_call(f"{self.settings.component}.GetStatus", id=self.settings.device_id)

    def _component_status(self, component: str, device_id: int) -> dict[str, object]:
        """Return one Shelly status payload for an arbitrary configured component."""
        return self._rpc_call(f"{str(component).strip()}.GetStatus", id=int(device_id))

    def _signal_readback_flag(self, settings: ShellySignalReadbackSettings | None) -> bool | None:
        """Return one optional normalized bool from Shelly status readback config."""
        if settings is None:
            return None
        payload = self._component_status(settings.component, settings.device_id)
        value = _mapping_path_value(payload, settings.value_path)
        normalized = bool(normalize_binary_flag(value))
        return not normalized if settings.invert else normalized


__all__ = [
    "ShellyBackendBase",
    "ShellyBackendSettings",
    "ShellyPmStatus",
    "ShellyProfileDefaults",
    "ShellyRpcScalar",
    "ShellySignalReadbackSettings",
    "_parse_switch_channel_ids",
    "_switch_channel_id",
    "load_shelly_backend_settings",
    "normalize_shelly_profile_name",
    "normalize_switching_mode",
    "parse_phase_selection_list",
    "phase_currents_for_selection",
    "phase_powers_for_selection",
    "resolve_shelly_profile",
    "validate_shelly_profile_role",
]
