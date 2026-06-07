# SPDX-License-Identifier: GPL-3.0-or-later
"""Generic HTTP/JSON template charger backend."""

from __future__ import annotations

from dataclasses import dataclass

from .config_file import backend_request_timeout_seconds
from .template_support import (
    TemplateHttpBackendBase,
    TemplateAuthSettings,
    config_section,
    enabled_state_from_text,
    json_path_value,
    load_template_config,
    load_template_auth_settings,
    normalize_http_method,
    payload_object,
    resolved_url,
)
from .models import (
    ChargerState,
    PhaseSelection,
    normalize_phase_selection,
    normalize_phase_selection_tuple,
)
from venus_evcharger.core.contracts import finite_float_or_none


@dataclass(frozen=True)
class TemplateChargerSettings:
    """Normalized template-charger config loaded from one adapter file."""

    base_url: str
    auth_settings: TemplateAuthSettings
    timeout_seconds: float
    supported_phase_selections: tuple[PhaseSelection, ...]
    state_method: str
    state_url: str | None
    state_enabled_path: str | None
    state_current_path: str | None
    state_phase_selection_path: str | None
    state_actual_current_path: str | None
    state_power_watts_path: str | None
    state_energy_kwh_path: str | None
    state_status_path: str | None
    state_fault_path: str | None
    enable_method: str
    enable_url: str
    enable_json_template: str | None
    current_method: str
    current_url: str
    current_json_template: str | None
    phase_method: str
    phase_url: str | None
    phase_json_template: str | None


@dataclass(frozen=True)
class _TemplateChargerUrls:
    """Resolved HTTP endpoints used by the template-charger backend."""

    state_url: str | None
    enable_url: str
    current_url: str
    phase_url: str | None


@dataclass(frozen=True)
class _TemplateChargerStatePaths:
    """Normalized state-response paths used by charger readback."""

    enabled: str | None
    current: str | None
    phase_selection: str | None
    actual_current: str | None
    power_watts: str | None
    energy_kwh: str | None
    status: str | None
    fault: str | None


@dataclass(frozen=True)
class _TemplateChargerCachedState:
    """Cached charger values used as readback fallbacks and request context."""

    enabled: bool | None
    current_amps: float | None
    phase_selection: PhaseSelection


def _optional_config_path(section: object, key: str) -> str | None:
    """Return one optional trimmed response path from the given config section."""
    return str(getattr(section, "get")(key, "")).strip() or None


def _template_charger_timeout_seconds(adapter: object, service: object) -> float:
    """Return the normalized timeout used by the template-charger backend."""
    return backend_request_timeout_seconds(adapter, service)


def _template_charger_supported_phase_selections(capabilities: object) -> tuple[PhaseSelection, ...]:
    """Return normalized supported phase selections for the template charger."""
    return normalize_phase_selection_tuple(
        getattr(capabilities, "get")("SupportedPhaseSelections", "P1"),
        ("P1",),
    )


def _template_charger_urls(
    base_url: str,
    state_request: object,
    enable_request: object,
    current_request: object,
    phase_request: object,
) -> _TemplateChargerUrls:
    """Return resolved HTTP endpoints for charger state and command calls."""
    return _TemplateChargerUrls(
        state_url=resolved_url(base_url, getattr(state_request, "get")("Url", "")) or None,
        enable_url=resolved_url(base_url, getattr(enable_request, "get")("Url", "")),
        current_url=resolved_url(base_url, getattr(current_request, "get")("Url", "")),
        phase_url=resolved_url(base_url, getattr(phase_request, "get")("Url", "")) or None,
    )


def _template_charger_state_paths(state_response: object) -> _TemplateChargerStatePaths:
    """Return normalized state-response paths used by charger readback."""
    return _TemplateChargerStatePaths(
        enabled=_optional_config_path(state_response, "EnabledPath"),
        current=_optional_config_path(state_response, "CurrentPath"),
        phase_selection=_optional_config_path(state_response, "PhaseSelectionPath"),
        actual_current=_optional_config_path(state_response, "ActualCurrentPath"),
        power_watts=_optional_config_path(state_response, "PowerWattsPath"),
        energy_kwh=_optional_config_path(state_response, "EnergyKwhPath"),
        status=_optional_config_path(state_response, "StatusPath"),
        fault=_optional_config_path(state_response, "FaultPath"),
    )


def _template_json_template(section: object, default: str) -> str | None:
    """Return one optional rendered JSON template string."""
    return str(getattr(section, "get")("JsonTemplate", default)).strip() or None


def _template_phase_json_template(phase_request: object, phase_url: str | None) -> str | None:
    """Return the optional phase-selection JSON template when phase control exists."""
    if not phase_url:
        return None
    return _template_json_template(phase_request, '{"phase_selection": "$phase_selection"}')


def _validate_template_charger_settings(
    urls: _TemplateChargerUrls,
    supported_phase_selections: tuple[PhaseSelection, ...],
) -> None:
    """Raise when required template-charger URLs are missing."""
    if not urls.enable_url:
        raise ValueError("Template charger backend requires [EnableRequest] Url")
    if not urls.current_url:
        raise ValueError("Template charger backend requires [CurrentRequest] Url")
    if len(supported_phase_selections) > 1 and not urls.phase_url:
        raise ValueError(
            "Template charger backend with multi-phase support requires [PhaseRequest] Url"
        )


def load_template_charger_settings(service: object, config_path: str) -> TemplateChargerSettings:
    """Return normalized template-charger settings."""
    parser = load_template_config(str(config_path).strip())
    adapter = config_section(parser, "Adapter")
    capabilities = config_section(parser, "Capabilities")
    state_request = config_section(parser, "StateRequest")
    state_response = config_section(parser, "StateResponse")
    enable_request = config_section(parser, "EnableRequest")
    current_request = config_section(parser, "CurrentRequest")
    phase_request = config_section(parser, "PhaseRequest")

    base_url = str(adapter.get("BaseUrl", "")).strip()
    supported_phase_selections = _template_charger_supported_phase_selections(capabilities)
    urls = _template_charger_urls(
        base_url,
        state_request,
        enable_request,
        current_request,
        phase_request,
    )
    state_paths = _template_charger_state_paths(state_response)
    enable_json_template = _template_json_template(enable_request, '{"enabled": $enabled_json}')
    current_json_template = _template_json_template(current_request, '{"amps": $amps}')
    phase_json_template = _template_phase_json_template(phase_request, urls.phase_url)

    _validate_template_charger_settings(urls, supported_phase_selections)

    return TemplateChargerSettings(
        base_url=base_url,
        auth_settings=load_template_auth_settings(adapter, service),
        timeout_seconds=_template_charger_timeout_seconds(adapter, service),
        supported_phase_selections=supported_phase_selections,
        state_method=normalize_http_method(state_request.get("Method", "GET"), "GET"),
        state_url=urls.state_url,
        state_enabled_path=state_paths.enabled,
        state_current_path=state_paths.current,
        state_phase_selection_path=state_paths.phase_selection,
        state_actual_current_path=state_paths.actual_current,
        state_power_watts_path=state_paths.power_watts,
        state_energy_kwh_path=state_paths.energy_kwh,
        state_status_path=state_paths.status,
        state_fault_path=state_paths.fault,
        enable_method=normalize_http_method(enable_request.get("Method", "POST"), "POST"),
        enable_url=urls.enable_url,
        enable_json_template=enable_json_template,
        current_method=normalize_http_method(current_request.get("Method", "POST"), "POST"),
        current_url=urls.current_url,
        current_json_template=current_json_template,
        phase_method=normalize_http_method(phase_request.get("Method", "POST"), "POST"),
        phase_url=urls.phase_url,
        phase_json_template=phase_json_template,
    )


class TemplateChargerBackend(TemplateHttpBackendBase):
    """Direct charger-control backend driven by one normalized HTTP/JSON surface."""

    def __init__(self, service: object, config_path: str = "") -> None:
        self.service = service
        self.config_path = str(config_path).strip()
        self.settings = load_template_charger_settings(service, self.config_path)
        super().__init__(service, self.settings.timeout_seconds, auth_settings=self.settings.auth_settings)
        default_phase_selection = self.settings.supported_phase_selections[0]
        self._enabled_state_cache: bool | None = None
        self._current_amps_cache: float | None = None
        self._phase_selection_cache: PhaseSelection = normalize_phase_selection(
            getattr(service, "requested_phase_selection", default_phase_selection),
            default_phase_selection,
        )
        if self._phase_selection_cache not in self.settings.supported_phase_selections:
            self._phase_selection_cache = default_phase_selection

    @staticmethod
    def _context(
        *,
        enabled: bool = False,
        amps: float = 0.0,
        phase_selection: PhaseSelection = "P1",
    ) -> dict[str, str]:
        """Return one stable template context for charger command rendering."""
        return {
            "enabled_json": "true" if enabled else "false",
            "enabled_int": "1" if enabled else "0",
            "enabled_text": "on" if enabled else "off",
            "amps": f"{float(amps):g}",
            "phase_selection": str(phase_selection),
        }

    @staticmethod
    def _enabled_state(value: object) -> bool | None:
        """Return one normalized optional enabled-state from one response scalar."""
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        return enabled_state_from_text(str(value).strip().lower())

    @staticmethod
    def _optional_text(value: object) -> str | None:
        """Return one trimmed optional text field from arbitrary JSON scalars."""
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _normalized_phase_selection(
        value: object,
        default: PhaseSelection,
        supported: tuple[PhaseSelection, ...],
    ) -> PhaseSelection:
        """Return one supported charger phase selection."""
        normalized = normalize_phase_selection(value, default)
        return normalized if normalized in supported else default

    def _cached_state(self) -> _TemplateChargerCachedState:
        """Return cached charger state used as readback fallback and request context."""
        return _TemplateChargerCachedState(
            enabled=self._enabled_state_cache,
            current_amps=self._current_amps_cache,
            phase_selection=self._phase_selection_cache,
        )

    def _state_context(self, cached: _TemplateChargerCachedState) -> dict[str, str]:
        """Return request context derived from the current cached charger state."""
        return self._context(
            enabled=bool(cached.enabled) if cached.enabled is not None else False,
            amps=cached.current_amps or 0.0,
            phase_selection=cached.phase_selection,
        )

    @staticmethod
    def _payload_float(payload: object, path: str | None) -> float | None:
        """Return one optional numeric charger-state value from the payload."""
        if path is None:
            return None
        return finite_float_or_none(json_path_value(payload_object(payload), path))

    def _payload_phase_selection(
        self,
        payload: object,
        cached: _TemplateChargerCachedState,
    ) -> PhaseSelection:
        """Return one supported phase selection from readback or cache."""
        path = self.settings.state_phase_selection_path
        if path is None:
            return cached.phase_selection
        return self._normalized_phase_selection(
            json_path_value(payload_object(payload), path),
            cached.phase_selection,
            self.settings.supported_phase_selections,
        )

    def _payload_text(self, payload: object, path: str | None) -> str | None:
        """Return one optional text value from the charger-state payload."""
        if path is None:
            return None
        return self._optional_text(json_path_value(payload_object(payload), path))

    def _state_from_payload(
        self,
        payload: object,
        cached: _TemplateChargerCachedState,
    ) -> ChargerState:
        """Return normalized charger state by merging readback with cached defaults."""
        enabled = cached.enabled
        if self.settings.state_enabled_path is not None:
            enabled = self._enabled_state(
                json_path_value(payload_object(payload), self.settings.state_enabled_path)
            )
        current_amps = (
            cached.current_amps
            if self.settings.state_current_path is None
            else self._payload_float(payload, self.settings.state_current_path)
        )
        return ChargerState(
            enabled=enabled,
            current_amps=current_amps,
            phase_selection=self._payload_phase_selection(payload, cached),
            actual_current_amps=self._payload_float(payload, self.settings.state_actual_current_path),
            power_w=self._payload_float(payload, self.settings.state_power_watts_path),
            energy_kwh=self._payload_float(payload, self.settings.state_energy_kwh_path),
            status_text=self._payload_text(payload, self.settings.state_status_path),
            fault_text=self._payload_text(payload, self.settings.state_fault_path),
        )

    def _remember_charger_state(self, state: ChargerState) -> ChargerState:
        """Persist readback into command caches used by later fallback reads."""
        self._enabled_state_cache = state.enabled
        self._current_amps_cache = state.current_amps
        self._phase_selection_cache = state.phase_selection or self._phase_selection_cache
        return state

    def read_charger_state(self) -> ChargerState:
        """Return one normalized charger state from readback or cached commands."""
        cached = self._cached_state()
        if self.settings.state_url is None:
            return self._remember_charger_state(
                ChargerState(
                    enabled=cached.enabled,
                    current_amps=cached.current_amps,
                    phase_selection=cached.phase_selection,
                )
            )
        payload = self._perform_request(
            self.settings.state_method,
            self.settings.state_url,
            context=self._state_context(cached),
        )
        return self._remember_charger_state(self._state_from_payload(payload, cached))

    def set_enabled(self, enabled: bool) -> None:
        """Apply one charger enable/disable request."""
        self._perform_request(
            self.settings.enable_method,
            self.settings.enable_url,
            context=self._context(enabled=bool(enabled)),
            json_template=self.settings.enable_json_template,
        )
        self._enabled_state_cache = bool(enabled)

    def set_current(self, amps: float) -> None:
        """Apply one charger current request."""
        normalized_amps = finite_float_or_none(amps)
        if normalized_amps is None or normalized_amps < 0.0:
            raise ValueError(f"Unsupported charger current '{amps}'")
        self._perform_request(
            self.settings.current_method,
            self.settings.current_url,
            context=self._context(amps=float(normalized_amps)),
            json_template=self.settings.current_json_template,
        )
        self._current_amps_cache = float(normalized_amps)

    def set_phase_selection(self, selection: PhaseSelection) -> None:
        """Apply one supported charger phase-selection request."""
        if selection not in self.settings.supported_phase_selections:
            raise ValueError(f"Unsupported phase selection '{selection}' for template charger backend")
        if self.settings.phase_url is None:
            return
        self._perform_request(
            self.settings.phase_method,
            self.settings.phase_url,
            context=self._context(phase_selection=selection),
            json_template=self.settings.phase_json_template,
        )
        self._phase_selection_cache = selection
