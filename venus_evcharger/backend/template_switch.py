# SPDX-License-Identifier: GPL-3.0-or-later
"""Generic HTTP/JSON template switch backend."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from .template_support import (
    TemplateHttpBackendBase,
    TemplateAuthSettings,
    config_section,
    enabled_state_from_text,
    json_path_value,
    load_template_config,
    load_template_auth_settings,
    normalize_http_method,
    resolved_url,
)
from .template_switch_contract import (
    ADAPTER_BASE_URL_KEY,
    CAPABILITIES_MAX_DIRECT_POWER_KEY,
    CAPABILITIES_PHASE_SELECTIONS_KEY,
    CAPABILITIES_REQUIRES_PAUSE_KEY,
    CAPABILITIES_SWITCHING_MODE_KEY,
    DEFAULT_COMMAND_METHOD,
    DEFAULT_ENABLED_PATH,
    DEFAULT_PHASE_JSON_TEMPLATE,
    DEFAULT_PHASE_METHOD,
    DEFAULT_STATE_METHOD,
    DEFAULT_SWITCH_CONFIG_PATH,
    DEFAULT_SWITCH_PHASE_SELECTION,
    DEFAULT_SWITCH_PHASE_SELECTIONS,
    DEFAULT_SWITCHING_MODE,
    REQUEST_JSON_TEMPLATE_KEY,
    REQUEST_METHOD_KEY,
    REQUEST_URL_KEY,
    STATE_RESPONSE_ENABLED_KEY,
    STATE_RESPONSE_FEEDBACK_CLOSED_KEY,
    STATE_RESPONSE_INTERLOCK_OK_KEY,
    STATE_RESPONSE_PHASE_SELECTION_KEY,
    SWITCH_CONTEXT_ENABLED_INT,
    SWITCH_CONTEXT_ENABLED_JSON,
    SWITCH_CONTEXT_ENABLED_TEXT,
    SWITCH_CONTEXT_PHASE_SELECTION,
    SWITCH_FALSE_INT,
    SWITCH_FALSE_JSON,
    SWITCH_FALSE_TEXT,
    SWITCH_TRUE_INT,
    SWITCH_TRUE_JSON,
    SWITCH_TRUE_TEXT,
)
from .config_file import backend_request_timeout_seconds
from .models import (
    PhaseSelection,
    SwitchCapabilities,
    SwitchState,
    SwitchingMode,
    normalize_phase_selection,
    normalize_phase_selection_or_none,
    normalize_phase_selection_tuple,
    normalize_switching_mode,
)
from venus_evcharger.core.contracts import finite_float_or_none, normalize_binary_flag

_normalize_switching_mode = normalize_switching_mode


@dataclass(frozen=True)
class TemplateSwitchSettings:
    """Normalized template-switch config loaded from one adapter file."""

    base_url: str
    auth_settings: TemplateAuthSettings
    timeout_seconds: float
    state_method: str
    state_url: str
    state_enabled_path: str
    state_phase_selection_path: str | None
    state_feedback_closed_path: str | None
    state_interlock_ok_path: str | None
    command_method: str
    command_url: str
    command_json_template: str | None
    phase_method: str
    phase_url: str | None
    phase_json_template: str | None
    switching_mode: SwitchingMode
    supported_phase_selections: tuple[PhaseSelection, ...]
    requires_charge_pause_for_phase_change: bool
    max_direct_switch_power_w: float | None


@dataclass(frozen=True)
class _TemplateSwitchUrls:
    """Resolved HTTP endpoints used by the template-switch backend."""

    state_url: str
    command_url: str
    phase_url: str | None


def _template_switch_timeout_seconds(adapter: Mapping[str, object], service: object) -> float:
    """Return the normalized timeout used by the template-switch backend."""
    return backend_request_timeout_seconds(adapter, service)


def _template_switch_urls(
    base_url: str,
    state_request: object,
    command_request: object,
    phase_request: object,
) -> _TemplateSwitchUrls:
    """Return resolved HTTP endpoints for state, command, and optional phase control."""
    return _TemplateSwitchUrls(
        state_url=resolved_url(base_url, _section_text(state_request, REQUEST_URL_KEY)),
        command_url=resolved_url(base_url, _section_text(command_request, REQUEST_URL_KEY)),
        phase_url=resolved_url(base_url, _section_text(phase_request, REQUEST_URL_KEY)) or None,
    )


def _section_text(section: object, key: str, default: str = "") -> str:
    """Return one stripped config value as concrete text."""
    return str(getattr(section, "get")(key, default)).strip()


def _template_switch_enabled_path(state_response: object) -> str:
    """Return the required enabled-state response path."""
    return _section_text(state_response, STATE_RESPONSE_ENABLED_KEY, DEFAULT_ENABLED_PATH)


def _template_switch_phase_path(state_response: object) -> str | None:
    """Return the optional phase-selection response path."""
    return _section_text(state_response, STATE_RESPONSE_PHASE_SELECTION_KEY) or None


def _template_switch_feedback_closed_path(state_response: object) -> str | None:
    """Return the optional feedback-closed response path."""
    return _section_text(state_response, STATE_RESPONSE_FEEDBACK_CLOSED_KEY) or None


def _template_switch_interlock_ok_path(state_response: object) -> str | None:
    """Return the optional interlock-ok response path."""
    return _section_text(state_response, STATE_RESPONSE_INTERLOCK_OK_KEY) or None


def _template_switch_json_template(section: object, key: str = REQUEST_JSON_TEMPLATE_KEY) -> str | None:
    """Return one optional JSON template from the given config section."""
    return _section_text(section, key) or None


def _template_switch_phase_json_template(phase_request: object, phase_url: str | None) -> str | None:
    """Return the optional phase-selection JSON template when phase control is enabled."""
    if not phase_url:
        return None
    return _section_text(phase_request, REQUEST_JSON_TEMPLATE_KEY, DEFAULT_PHASE_JSON_TEMPLATE)


def _validate_template_switch_settings(
    urls: _TemplateSwitchUrls,
    state_enabled_path: str,
    supported_phase_selections: tuple[PhaseSelection, ...],
) -> None:
    """Raise when required switch request URLs or response paths are missing."""
    error = _template_switch_validation_error(
        urls,
        state_enabled_path,
        supported_phase_selections,
    )
    if error is not None:
        raise ValueError(error)


def _template_switch_validation_error(
    urls: _TemplateSwitchUrls,
    state_enabled_path: str,
    supported_phase_selections: tuple[PhaseSelection, ...],
) -> str | None:
    """Return one validation error message for invalid switch config, if any."""
    if not urls.state_url:
        return "Template switch backend requires [StateRequest] Url"
    if not urls.command_url:
        return "Template switch backend requires [CommandRequest] Url"
    if not state_enabled_path:
        return "Template switch backend requires [StateResponse] EnabledPath"
    if _multi_phase_switch_missing_phase_url(urls, supported_phase_selections):
        return "Template switch backend with multi-phase support requires [PhaseRequest] Url"
    return None


def _multi_phase_switch_missing_phase_url(
    urls: _TemplateSwitchUrls,
    supported_phase_selections: tuple[PhaseSelection, ...],
) -> bool:
    """Return whether a multi-phase switch config forgot its phase endpoint."""
    return len(supported_phase_selections) > 1 and not urls.phase_url


def load_template_switch_settings(service: object, config_path: str) -> TemplateSwitchSettings:
    """Return normalized template-switch settings."""
    parser = load_template_config(str(config_path).strip())
    adapter = config_section(parser, "Adapter")
    capabilities = config_section(parser, "Capabilities")
    state_request = config_section(parser, "StateRequest")
    state_response = config_section(parser, "StateResponse")
    command_request = config_section(parser, "CommandRequest")
    phase_request = config_section(parser, "PhaseRequest")

    base_url = str(adapter.get(ADAPTER_BASE_URL_KEY, "")).strip()
    supported_phase_selections = normalize_phase_selection_tuple(capabilities.get(CAPABILITIES_PHASE_SELECTIONS_KEY))
    switching_mode = normalize_switching_mode(capabilities.get(CAPABILITIES_SWITCHING_MODE_KEY))
    max_direct_switch_power_w = (
        None
        if switching_mode == "contactor"
        else finite_float_or_none(capabilities.get(CAPABILITIES_MAX_DIRECT_POWER_KEY))
    )
    urls = _template_switch_urls(base_url, state_request, command_request, phase_request)
    state_enabled_path = _template_switch_enabled_path(state_response)
    state_phase_selection_path = _template_switch_phase_path(state_response)
    state_feedback_closed_path = _template_switch_feedback_closed_path(state_response)
    state_interlock_ok_path = _template_switch_interlock_ok_path(state_response)
    command_json_template = _template_switch_json_template(command_request)
    phase_json_template = _template_switch_phase_json_template(phase_request, urls.phase_url)

    _validate_template_switch_settings(urls, state_enabled_path, supported_phase_selections)

    return TemplateSwitchSettings(
        base_url=base_url,
        auth_settings=load_template_auth_settings(adapter, service),
        timeout_seconds=_template_switch_timeout_seconds(adapter, service),
        state_method=normalize_http_method(state_request.get(REQUEST_METHOD_KEY), DEFAULT_STATE_METHOD),
        state_url=urls.state_url,
        state_enabled_path=state_enabled_path,
        state_phase_selection_path=state_phase_selection_path,
        state_feedback_closed_path=state_feedback_closed_path,
        state_interlock_ok_path=state_interlock_ok_path,
        command_method=normalize_http_method(
            command_request.get(REQUEST_METHOD_KEY),
            DEFAULT_COMMAND_METHOD,
        ),
        command_url=urls.command_url,
        command_json_template=command_json_template,
        phase_method=normalize_http_method(
            phase_request.get(REQUEST_METHOD_KEY),
            DEFAULT_PHASE_METHOD,
        ),
        phase_url=urls.phase_url,
        phase_json_template=phase_json_template,
        switching_mode=switching_mode,
        supported_phase_selections=supported_phase_selections,
        requires_charge_pause_for_phase_change=bool(
            normalize_binary_flag(capabilities.get(CAPABILITIES_REQUIRES_PAUSE_KEY))
        ),
        max_direct_switch_power_w=max_direct_switch_power_w,
    )


class TemplateSwitchBackend(TemplateHttpBackendBase):
    """Switch backend driven by one normalized HTTP/JSON adapter surface."""

    def __init__(self, service: object, config_path: str = DEFAULT_SWITCH_CONFIG_PATH) -> None:
        self.config_path = str(config_path).strip()
        self.settings = load_template_switch_settings(service, self.config_path)
        super().__init__(service, self.settings.timeout_seconds, auth_settings=self.settings.auth_settings)
        default_selection = self.settings.supported_phase_selections[0]
        requested_selection = normalize_phase_selection_or_none(getattr(service, "requested_phase_selection", None))
        self._selected_phase_selection: PhaseSelection = (
            requested_selection
            if requested_selection in self.settings.supported_phase_selections
            else default_selection
        )

    @staticmethod
    def _context(enabled: bool, phase_selection: PhaseSelection) -> dict[str, str]:
        """Return one stable template context for URL/body rendering."""
        return {
            SWITCH_CONTEXT_ENABLED_JSON: SWITCH_TRUE_JSON if enabled else SWITCH_FALSE_JSON,
            SWITCH_CONTEXT_ENABLED_INT: SWITCH_TRUE_INT if enabled else SWITCH_FALSE_INT,
            SWITCH_CONTEXT_ENABLED_TEXT: SWITCH_TRUE_TEXT if enabled else SWITCH_FALSE_TEXT,
            SWITCH_CONTEXT_PHASE_SELECTION: str(phase_selection),
        }

    @staticmethod
    def _disabled_context(phase_selection: PhaseSelection) -> dict[str, str]:
        """Return the stable template context for non-mutating reads and phase changes."""
        return {
            SWITCH_CONTEXT_ENABLED_JSON: SWITCH_FALSE_JSON,
            SWITCH_CONTEXT_ENABLED_INT: SWITCH_FALSE_INT,
            SWITCH_CONTEXT_ENABLED_TEXT: SWITCH_FALSE_TEXT,
            SWITCH_CONTEXT_PHASE_SELECTION: str(phase_selection),
        }

    @staticmethod
    def _enabled_state(value: object) -> bool:
        """Return one normalized boolean switch state from one response scalar."""
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        normalized = enabled_state_from_text(text)
        if normalized is not None:
            return normalized
        raise ValueError(f"Unsupported enabled-state value '{value}'")

    def capabilities(self) -> SwitchCapabilities:
        """Return configured template-switch capabilities."""
        return SwitchCapabilities(
            switching_mode=self.settings.switching_mode,
            supported_phase_selections=self.settings.supported_phase_selections,
            requires_charge_pause_for_phase_change=self.settings.requires_charge_pause_for_phase_change,
            max_direct_switch_power_w=self.settings.max_direct_switch_power_w,
        )

    @classmethod
    def _optional_state_flag(cls, payload: dict[str, object], path: str | None) -> bool | None:
        """Return one optional boolean state from the configured response path."""
        if not path:
            return None
        return cls._enabled_state(json_path_value(payload, path))

    def read_switch_state(self) -> SwitchState:
        """Read one normalized switch state from the configured state endpoint."""
        payload = self._perform_request(
            self.settings.state_method,
            self.settings.state_url,
            context=self._disabled_context(self._selected_phase_selection),
        )
        enabled = self._enabled_state(json_path_value(payload, self.settings.state_enabled_path))
        phase_selection = self._selected_phase_selection
        if self.settings.state_phase_selection_path:
            response_selection = json_path_value(payload, self.settings.state_phase_selection_path)
            phase_selection = normalize_phase_selection(response_selection, self._selected_phase_selection)
        if phase_selection not in self.settings.supported_phase_selections:
            phase_selection = self.settings.supported_phase_selections[0]
        self._selected_phase_selection = phase_selection
        return SwitchState(
            enabled=enabled,
            phase_selection=phase_selection,
            feedback_closed=self._optional_state_flag(payload, self.settings.state_feedback_closed_path),
            interlock_ok=self._optional_state_flag(payload, self.settings.state_interlock_ok_path),
        )

    def set_enabled(self, enabled: bool) -> None:
        """Apply one enable/disable request to the configured command endpoint."""
        self._perform_request(
            self.settings.command_method,
            self.settings.command_url,
            context=self._context(bool(enabled), self._selected_phase_selection),
            json_template=self.settings.command_json_template,
        )

    def set_phase_selection(self, selection: PhaseSelection) -> None:
        """Apply one supported phase selection to the optional phase endpoint."""
        if selection not in self.settings.supported_phase_selections:
            raise ValueError(f"Unsupported phase selection '{selection}' for template switch backend")
        if self.settings.phase_url is not None:
            self._perform_request(
                self.settings.phase_method,
                self.settings.phase_url,
                context=self._disabled_context(selection),
                json_template=self.settings.phase_json_template,
            )
        self._selected_phase_selection = selection


def force_contactor_switch_settings(backend: TemplateSwitchBackend) -> None:
    """Force one template-switch backend instance to contactor semantics."""
    backend.settings = replace(
        backend.settings,
        switching_mode="contactor",
        max_direct_switch_power_w=None,
    )
