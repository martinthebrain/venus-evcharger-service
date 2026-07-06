# SPDX-License-Identifier: GPL-3.0-or-later
"""Native go-e charger backend using the documented local HTTP API."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass

import requests

from venus_evcharger.core.contracts import finite_float_or_none

from .config_file import backend_request_timeout_seconds
from .models import ChargerState, PhaseSelection, normalize_phase_selection
from .template_support import (
    TemplateAuthSettings,
    config_section,
    load_template_auth_settings,
    load_template_config,
    _request_auth,
    _request_headers,
    resolved_url,
)


_GOE_STATUS_FILTER = "alw,amp,acu,car,err,eto,nrg,pnp"
_GOE_SUPPORTED_PHASE_SELECTIONS: tuple[PhaseSelection, ...] = ("P1",)
_GOE_CAR_STATUS_TEXT: dict[int, str] = {
    0: "error",
    1: "ready",
    2: "charging",
    3: "waiting",
    4: "complete",
    5: "error",
}
_GOE_ERROR_TEXT: dict[int, str] = {
    1: "error-fi-ac",
    2: "error-fi-dc",
    3: "error-phase",
    4: "error-overvolt",
    5: "error-overamp",
    6: "error-diode",
    7: "error-pp-invalid",
    8: "error-ground-invalid",
    9: "error-contactor-stuck",
    10: "error-contactor-miss",
    11: "error-fi-unknown",
    12: "error-unknown",
    13: "error-overtemp",
    14: "error-no-comm",
    15: "error-status-lock-stuck-open",
    16: "error-status-lock-stuck-locked",
}


@dataclass(frozen=True)
class GoEChargerSettings:
    """Normalized local go-e HTTP API settings."""

    base_url: str
    auth_settings: TemplateAuthSettings
    timeout_seconds: float
    supported_phase_selections: tuple[PhaseSelection, ...]
    state_url: str
    enable_url: str
    current_url: str
    phase_url: str | None
    status_filter: str


def _goe_timeout_seconds(adapter: Mapping[str, object], service: object) -> float:
    """Return the normalized request timeout for the go-e HTTP API."""
    return backend_request_timeout_seconds(adapter, service)


def load_goe_charger_settings(service: object, config_path: str) -> GoEChargerSettings:
    """Return normalized go-e charger backend settings."""
    parser = load_template_config(str(config_path).strip())
    adapter = config_section(parser, "Adapter")
    base_url = str(adapter.get("BaseUrl", "")).strip()
    if not base_url:
        raise ValueError("go-e charger backend requires Adapter.BaseUrl")
    return GoEChargerSettings(
        base_url=base_url,
        auth_settings=load_template_auth_settings(adapter, service),
        timeout_seconds=_goe_timeout_seconds(adapter, service),
        supported_phase_selections=_GOE_SUPPORTED_PHASE_SELECTIONS,
        state_url=resolved_url(base_url, "/api/status"),
        enable_url=resolved_url(base_url, "/api/set"),
        current_url=resolved_url(base_url, "/api/set"),
        phase_url=None,
        status_filter=_GOE_STATUS_FILTER,
    )


def _goe_auth(auth_settings: TemplateAuthSettings) -> object | None:
    """Return one optional requests-compatible auth object for the go-e API."""
    return _request_auth(auth_settings)


def _goe_headers(auth_settings: TemplateAuthSettings) -> dict[str, str] | None:
    """Return optional extra headers for the go-e HTTP requests."""
    return _request_headers(auth_settings)


def _goe_query_value(value: object) -> str:
    """Return one JSON-encoded query value for the go-e `/api/set` endpoint."""
    return json.dumps(value, separators=(",", ":"))


def _goe_payload(response_payload: object) -> dict[str, object]:
    """Return the plain status object for local or wrapped cloud responses."""
    if not isinstance(response_payload, dict):
        return {}
    data_payload = response_payload.get("data")
    if isinstance(data_payload, dict):
        return {str(key): value for key, value in data_payload.items()}
    return {str(key): value for key, value in response_payload.items()}


def _goe_optional_int(value: object) -> int | None:
    """Return one optional integer from go-e status payload values."""
    number = finite_float_or_none(value)
    return None if number is None else int(number)


def _goe_optional_bool(value: object) -> bool | None:
    """Return one optional bool from go-e status payload values."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(int(value))
    return _goe_optional_bool_text(str(value).strip().lower())


def _goe_optional_bool_text(text: str) -> bool | None:
    """Return one optional bool from normalized go-e text tokens."""
    if text in {"1", "true", "on", "yes"}:
        return True
    if text in {"0", "false", "off", "no"}:
        return False
    return None


def _goe_phase_selection(payload: dict[str, object], default: PhaseSelection) -> PhaseSelection:
    """Return one normalized observed phase selection from go-e status payload."""
    phase_count = _goe_optional_int(payload.get("pnp"))
    if phase_count is None:
        return default
    if phase_count >= 3:
        return "P1_P2_P3"
    if phase_count == 2:
        return "P1_P2"
    return "P1"


def _goe_nrg_values(payload: dict[str, object]) -> list[float] | None:
    """Return the go-e `nrg` array as a numeric list when present."""
    raw = payload.get("nrg")
    if not isinstance(raw, list):
        return None
    values: list[float] = []
    for item in raw:
        number = finite_float_or_none(item)
        if number is None:
            return None
        values.append(float(number))
    return values


def _goe_actual_current_amps(payload: dict[str, object]) -> float | None:
    """Return one measured charging current from go-e readback."""
    nrg = _goe_nrg_values(payload)
    if nrg is not None and len(nrg) >= 7:
        return max(float(nrg[4]), float(nrg[5]), float(nrg[6])) / 10.0
    acu = finite_float_or_none(payload.get("acu"))
    return None if acu is None else float(acu)


def _goe_power_w(payload: dict[str, object]) -> float | None:
    """Return one measured total charging power in watts."""
    nrg = _goe_nrg_values(payload)
    if nrg is None or len(nrg) < 12:
        return None
    return float(nrg[11]) * 10.0


def _goe_energy_kwh(payload: dict[str, object]) -> float | None:
    """Return one cumulative energy value in kWh."""
    energy_wh = finite_float_or_none(payload.get("eto"))
    return None if energy_wh is None else float(energy_wh) / 1000.0


def _goe_status_text(payload: dict[str, object]) -> str | None:
    """Return one normalized charger status text from go-e `car` state."""
    car_state = _goe_optional_int(payload.get("car"))
    return _GOE_CAR_STATUS_TEXT.get(car_state) if car_state is not None else None


def _goe_fault_text(payload: dict[str, object]) -> str | None:
    """Return one normalized charger fault text from go-e `err` state."""
    error_code = _goe_optional_int(payload.get("err"))
    if error_code is None or error_code == 0:
        car_state = _goe_optional_int(payload.get("car"))
        return "error" if car_state == 0 or car_state == 5 else None
    return _GOE_ERROR_TEXT.get(error_code, f"error-{error_code}")


def _goe_rounded_current_setting(amps: float) -> int:
    """Return one go-e-compatible whole-amp current setpoint."""
    rounded = int(math.floor(float(amps) + 0.5))
    if rounded < 6 or rounded > 32:
        raise ValueError(f"Unsupported charger current '{amps}' for go-e backend (expected 6..32 A)")
    return rounded


class GoEChargerBackend:
    """Native local HTTP backend for go-e chargers."""

    def __init__(self, service: object, config_path: str = "") -> None:
        self.service = service
        self.config_path = str(config_path).strip()
        self.settings = load_goe_charger_settings(service, self.config_path)
        session = getattr(service, "session", None)
        self._session = session if session is not None else requests.Session()
        self._observed_phase_selection: PhaseSelection = "P1"

    def _request_kwargs(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
    ) -> dict[str, object]:
        """Return one ready-to-use requests kwargs mapping."""
        kwargs: dict[str, object] = {
            "url": str(url),
            "timeout": float(self.settings.timeout_seconds),
        }
        if params:
            kwargs["params"] = params
        auth = _goe_auth(self.settings.auth_settings)
        if auth is not None:
            kwargs["auth"] = auth
        headers = _goe_headers(self.settings.auth_settings)
        if headers is not None:
            kwargs["headers"] = headers
        return kwargs

    def _status_payload(self) -> dict[str, object]:
        """Return one normalized go-e status payload."""
        response = self._session.get(
            **self._request_kwargs(
                self.settings.state_url,
                params={"filter": self.settings.status_filter},
            )
        )
        response.raise_for_status()
        return _goe_payload(response.json())

    def _set_value(self, key: str, value: object) -> None:
        """Write one documented go-e API key through `/api/set`."""
        response = self._session.get(
            **self._request_kwargs(
                self.settings.enable_url,
                params={str(key): _goe_query_value(value)},
            )
        )
        response.raise_for_status()
        payload = _goe_payload(response.json())
        ack = payload.get(str(key))
        if ack is False or isinstance(ack, str):
            raise RuntimeError(f"go-e charger rejected {key}={value!r}: {ack}")

    def read_charger_state(self) -> ChargerState:
        """Return one normalized charger state from the go-e local API."""
        payload = self._status_payload()
        phase_selection = _goe_phase_selection(payload, self._observed_phase_selection)
        self._observed_phase_selection = phase_selection
        return ChargerState(
            enabled=_goe_optional_bool(payload.get("alw")),
            current_amps=finite_float_or_none(payload.get("amp")),
            phase_selection=phase_selection,
            actual_current_amps=_goe_actual_current_amps(payload),
            power_w=_goe_power_w(payload),
            energy_kwh=_goe_energy_kwh(payload),
            status_text=_goe_status_text(payload),
            fault_text=_goe_fault_text(payload),
        )

    def set_enabled(self, enabled: bool) -> None:
        """Force go-e charging on or off via the documented `frc` key."""
        self._set_value("frc", 2 if bool(enabled) else 1)

    def set_current(self, amps: float) -> None:
        """Apply one whole-amp current setpoint via the documented `amp` key."""
        normalized_amps = finite_float_or_none(amps)
        if normalized_amps is None or normalized_amps <= 0.0:
            raise ValueError(f"Unsupported charger current '{amps}'")
        self._set_value("amp", _goe_rounded_current_setting(float(normalized_amps)))

    def set_phase_selection(self, selection: PhaseSelection) -> None:
        """Reject native phase writes until go-e documents a stable public key for it."""
        normalized = normalize_phase_selection(selection, "P1")
        if normalized == self._observed_phase_selection:
            return
        raise ValueError("go-e charger backend does not support documented native phase switching")
