# SPDX-License-Identifier: GPL-3.0-or-later
"""Generic HTTP/JSON template meter backend."""

from __future__ import annotations

from dataclasses import dataclass

from .shelly_support import (
    phase_currents_for_selection,
    phase_powers_for_selection,
)
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
from .models import MeterReading, PhaseSelection, normalize_phase_selection
from venus_evcharger.core.contracts import finite_float_or_none


@dataclass(frozen=True)
class TemplateMeterSettings:
    """Normalized template-meter config loaded from one adapter file."""

    base_url: str
    auth_settings: TemplateAuthSettings
    timeout_seconds: float
    meter_method: str
    meter_url: str
    relay_enabled_path: str | None
    power_path: str
    voltage_path: str | None
    current_path: str | None
    energy_kwh_path: str | None
    energy_wh_path: str | None
    phase_selection: PhaseSelection
    phase_selection_path: str | None
    phase_powers_path: str | None
    phase_currents_path: str | None


@dataclass(frozen=True)
class _TemplateMeterScalarValues:
    """Scalar meter values extracted from one template-meter response."""

    power_w: float
    voltage_v: float | None
    current_a: float | None
    energy_kwh: float


def _phase_vector(value: object) -> tuple[float, float, float] | None:
    """Return one 3-value phase vector when the response payload is valid."""
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    numbers = (
        _phase_vector_item(value[0]),
        _phase_vector_item(value[1]),
        _phase_vector_item(value[2]),
    )
    return _resolved_phase_vector(numbers)


def _phase_vector_item(value: object) -> float | None:
    """Return one finite numeric phase-vector item."""
    return finite_float_or_none(value)


def _resolved_phase_vector(
    numbers: tuple[float | None, float | None, float | None],
) -> tuple[float, float, float] | None:
    """Return one concrete phase vector when all three numeric items are present."""
    if None in numbers:
        return None
    first, second, third = numbers
    return float(first or 0.0), float(second or 0.0), float(third or 0.0)


def _optional_path(section_value: object) -> str | None:
    """Return one normalized optional dotted response path."""
    return str(section_value).strip() or None


def _payload_float(payload: object, path: str | None) -> float | None:
    """Return one optional numeric value from the template response payload."""
    if path is None:
        return None
    return finite_float_or_none(json_path_value(payload_object(payload), path))


def _payload_energy_kwh(payload: object, settings: TemplateMeterSettings) -> float:
    """Return the normalized cumulative energy value in kWh."""
    energy_kwh = _payload_float(payload, settings.energy_kwh_path)
    if energy_kwh is not None:
        return float(energy_kwh)
    energy_wh = _payload_float(payload, settings.energy_wh_path)
    if energy_wh is not None:
        return float(energy_wh) / 1000.0
    return 0.0


def _payload_phase_selection(payload: object, settings: TemplateMeterSettings) -> PhaseSelection:
    """Return the normalized measured phase selection from payload or config default."""
    if settings.phase_selection_path is None:
        return settings.phase_selection
    return normalize_phase_selection(
        json_path_value(payload_object(payload), settings.phase_selection_path),
        settings.phase_selection,
    )


def _payload_phase_vector(payload: object, path: str | None) -> tuple[float, float, float] | None:
    """Return one optional explicit three-value phase vector from payload."""
    if path is None:
        return None
    return _phase_vector(json_path_value(payload_object(payload), path))


def _resolved_phase_powers(
    payload: object,
    settings: TemplateMeterSettings,
    power_w: float,
    phase_selection: PhaseSelection,
    single_phase_line: object,
) -> tuple[float, float, float]:
    """Return explicit or derived phase-power values for the meter reading."""
    phase_powers_w = _payload_phase_vector(payload, settings.phase_powers_path)
    if phase_powers_w is not None:
        return phase_powers_w
    return phase_powers_for_selection(power_w, phase_selection, single_phase_line)


def _resolved_phase_currents(
    payload: object,
    settings: TemplateMeterSettings,
    current_a: float | None,
    phase_selection: PhaseSelection,
    single_phase_line: object,
) -> tuple[float, float, float] | None:
    """Return explicit or derived phase-current values for the meter reading."""
    phase_currents_a = _payload_phase_vector(payload, settings.phase_currents_path)
    if phase_currents_a is not None:
        return phase_currents_a
    return phase_currents_for_selection(current_a, phase_selection, single_phase_line)


def _meter_scalar_values(payload: object, settings: TemplateMeterSettings) -> _TemplateMeterScalarValues:
    """Return normalized scalar values from one meter response payload."""
    power_w = _payload_float(payload, settings.power_path)
    if power_w is None:
        raise ValueError(f"Invalid meter power value at '{settings.power_path}'")
    return _TemplateMeterScalarValues(
        power_w=float(power_w),
        voltage_v=_payload_float(payload, settings.voltage_path),
        current_a=_payload_float(payload, settings.current_path),
        energy_kwh=_payload_energy_kwh(payload, settings),
    )


def load_template_meter_settings(service: object, config_path: str) -> TemplateMeterSettings:
    """Return normalized template-meter settings."""
    parser = load_template_config(str(config_path).strip())
    adapter = config_section(parser, "Adapter")
    phase = config_section(parser, "Phase")
    meter_request = config_section(parser, "MeterRequest")
    meter_response = config_section(parser, "MeterResponse")

    base_url = str(adapter.get("BaseUrl", "")).strip()
    default_timeout_seconds = float(getattr(service, "shelly_request_timeout_seconds", 2.0) or 2.0)
    timeout_seconds = finite_float_or_none(
        adapter.get("RequestTimeoutSeconds", str(default_timeout_seconds))
    )
    meter_url = resolved_url(base_url, meter_request.get("Url", ""))
    power_path = str(meter_response.get("PowerPath", "power_w")).strip()
    _validate_template_meter_paths(meter_url, power_path)

    default_phase = normalize_phase_selection(
        phase.get("MeasuredPhaseSelection", phase.get("MeasuredPhase", getattr(service, "phase", "L1"))),
        "P1",
    )
    return TemplateMeterSettings(
        base_url=base_url,
        auth_settings=load_template_auth_settings(adapter, service),
        timeout_seconds=2.0 if timeout_seconds is None or timeout_seconds <= 0.0 else float(timeout_seconds),
        meter_method=normalize_http_method(meter_request.get("Method", "GET"), "GET"),
        meter_url=meter_url,
        relay_enabled_path=_optional_path(meter_response.get("RelayEnabledPath", "")),
        power_path=power_path,
        voltage_path=_optional_path(meter_response.get("VoltagePath", "")),
        current_path=_optional_path(meter_response.get("CurrentPath", "")),
        energy_kwh_path=_optional_path(meter_response.get("EnergyKwhPath", "")),
        energy_wh_path=_optional_path(meter_response.get("EnergyWhPath", "")),
        phase_selection=default_phase,
        phase_selection_path=_optional_path(meter_response.get("PhaseSelectionPath", "")),
        phase_powers_path=_optional_path(meter_response.get("PhasePowersPath", "")),
        phase_currents_path=_optional_path(meter_response.get("PhaseCurrentsPath", "")),
    )


def _validate_template_meter_paths(meter_url: str, power_path: str) -> None:
    """Validate the required request and response paths for template meter backends."""
    if not meter_url:
        raise ValueError("Template meter backend requires [MeterRequest] Url")
    if not power_path:
        raise ValueError("Template meter backend requires [MeterResponse] PowerPath")


class TemplateMeterBackend(TemplateHttpBackendBase):
    """Meter backend driven by one normalized HTTP/JSON adapter surface."""

    def __init__(self, service: object, config_path: str = "") -> None:
        self.service = service
        self.config_path = str(config_path).strip()
        self.settings = load_template_meter_settings(service, self.config_path)
        super().__init__(service, self.settings.timeout_seconds, auth_settings=self.settings.auth_settings)

    @staticmethod
    def _enabled_state(value: object) -> bool | None:
        """Return one normalized optional relay state from one response scalar."""
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        return enabled_state_from_text(str(value).strip().lower())

    def read_meter(self) -> MeterReading:
        """Read one normalized meter reading from the configured template endpoint."""
        payload = self._perform_request(
            self.settings.meter_method,
            self.settings.meter_url,
        )
        scalar_values = _meter_scalar_values(payload, self.settings)
        relay_on = (
            None
            if self.settings.relay_enabled_path is None
            else self._enabled_state(json_path_value(payload, self.settings.relay_enabled_path))
        )
        phase_selection = _payload_phase_selection(payload, self.settings)
        single_phase_line = getattr(self.service, "phase", "L1")
        phase_powers_w = _resolved_phase_powers(
            payload,
            self.settings,
            scalar_values.power_w,
            phase_selection,
            single_phase_line,
        )
        phase_currents_a = _resolved_phase_currents(
            payload,
            self.settings,
            scalar_values.current_a,
            phase_selection,
            single_phase_line,
        )

        return MeterReading(
            relay_on=relay_on,
            power_w=scalar_values.power_w,
            voltage_v=scalar_values.voltage_v,
            current_a=scalar_values.current_a,
            energy_kwh=scalar_values.energy_kwh,
            phase_selection=phase_selection,
            phase_powers_w=phase_powers_w,
            phase_currents_a=phase_currents_a,
        )
