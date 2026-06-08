# SPDX-License-Identifier: GPL-3.0-or-later
"""Victron GX/Cerbo relay-backed normalized switch backend."""

from __future__ import annotations

import configparser
import time
from dataclasses import dataclass
from typing import Any, Literal

from .config_file import config_section
from .models import PhaseSelection, SwitchCapabilities, SwitchState, normalize_phase_selection_tuple
from venus_evcharger.core.contracts import finite_float_or_none, normalize_binary_flag

ContactMode = Literal["NO", "NC"]


@dataclass(frozen=True)
class CerboGxRelaySwitchSettings:
    """Normalized settings for one local Victron GX relay actuator."""

    relay_index: int
    contact_mode: ContactMode
    ensure_manual_function: bool
    manual_function_value: int
    verify_settle_seconds: float
    verify_retry_seconds: float
    supported_phase_selections: tuple[PhaseSelection, ...]
    requires_charge_pause_for_phase_change: bool


def _config(path: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    if not str(path).strip():
        parser.read_dict({"Adapter": {"Type": "cerbo_gx_relay_switch"}})
        return parser
    read_files = parser.read(path)
    if not read_files:
        raise FileNotFoundError(path)
    return parser


def _relay_index(value: object) -> int:
    try:
        index = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("Cerbo GX relay backend requires RelayIndex 0 or 1") from exc
    if index not in (0, 1):
        raise ValueError("Cerbo GX relay backend supports RelayIndex 0 or 1")
    return index


def _contact_mode(value: object) -> ContactMode:
    mode = str(value).strip().upper()
    if mode in {"NO", "NORMALLY_OPEN", "NORMALLY-OPEN"}:
        return "NO"
    if mode in {"NC", "NORMALLY_CLOSED", "NORMALLY-CLOSED"}:
        return "NC"
    raise ValueError("Cerbo GX relay backend requires ContactMode NO or NC")


def _positive_seconds(value: object, default: float) -> float:
    seconds = finite_float_or_none(value)
    if seconds is None or seconds < 0.0:
        return default
    return float(seconds)


def load_cerbo_gx_relay_switch_settings(config_path: str) -> CerboGxRelaySwitchSettings:
    """Return normalized Cerbo GX relay switch settings."""
    parser = _config(config_path)
    adapter = config_section(parser, "Adapter")
    capabilities = config_section(parser, "Capabilities")
    return CerboGxRelaySwitchSettings(
        relay_index=_relay_index(adapter.get("RelayIndex", "0")),
        contact_mode=_contact_mode(adapter.get("ContactMode", "NO")),
        ensure_manual_function=bool(normalize_binary_flag(adapter.get("EnsureManualFunction", "1"))),
        manual_function_value=int(adapter.get("ManualFunctionValue", "2") or 2),
        verify_settle_seconds=_positive_seconds(adapter.get("VerifySettleSeconds", "0.1"), 0.1),
        verify_retry_seconds=_positive_seconds(adapter.get("VerifyRetrySeconds", "0.2"), 0.2),
        supported_phase_selections=normalize_phase_selection_tuple(capabilities.get("SupportedPhaseSelections", "P1"), ("P1",)),
        requires_charge_pause_for_phase_change=bool(
            normalize_binary_flag(capabilities.get("RequiresChargePauseForPhaseChange", "0"))
        ),
    )


class CerboGxRelaySwitchBackend:
    """Control one Victron GX relay via Venus OS D-Bus."""

    _SYSTEM_SERVICE = "com.victronenergy.system"
    _SETTINGS_SERVICE = "com.victronenergy.settings"

    def __init__(self, service: object, config_path: str = "") -> None:
        self.service = service
        self.config_path = str(config_path).strip()
        self.settings = load_cerbo_gx_relay_switch_settings(self.config_path)
        default_selection = self.settings.supported_phase_selections[0]
        requested_selection = getattr(service, "requested_phase_selection", default_selection)
        self._selected_phase_selection: PhaseSelection = (
            requested_selection if requested_selection in self.settings.supported_phase_selections else default_selection
        )

    def capabilities(self) -> SwitchCapabilities:
        """Return the configured relay capabilities."""
        return SwitchCapabilities(
            switching_mode="contactor",
            supported_phase_selections=self.settings.supported_phase_selections,
            requires_charge_pause_for_phase_change=self.settings.requires_charge_pause_for_phase_change,
            max_direct_switch_power_w=None,
        )

    def read_switch_state(self) -> SwitchState:
        """Read one normalized switch state from ``/Relay/<idx>/State``."""
        relay_state = self._read_relay_state()
        enabled = self._enabled_from_relay_state(relay_state)
        return SwitchState(
            enabled=enabled,
            phase_selection=self._selected_phase_selection,
            feedback_closed=None,
            interlock_ok=None,
        )

    def set_enabled(self, enabled: bool) -> None:
        """Switch the configured GX relay and verify the readback."""
        if self.settings.ensure_manual_function:
            self._ensure_manual_function()
        target_state = self._relay_state_for_enabled(bool(enabled))
        self._set_relay_state(target_state)
        self._verify_relay_state(target_state)

    def set_phase_selection(self, selection: PhaseSelection) -> None:
        """Store one supported phase selection for API consistency."""
        if selection not in self.settings.supported_phase_selections:
            raise ValueError(f"Unsupported phase selection '{selection}' for Cerbo GX relay backend")
        self._selected_phase_selection = selection

    def _relay_state_for_enabled(self, enabled: bool) -> int:
        if self.settings.contact_mode == "NC":
            return 0 if enabled else 1
        return 1 if enabled else 0

    def _enabled_from_relay_state(self, relay_state: int) -> bool:
        return relay_state == self._relay_state_for_enabled(True)

    def _relay_state_path(self) -> str:
        return f"/Relay/{self.settings.relay_index}/State"

    def _manual_function_paths(self) -> tuple[str, ...]:
        path = f"/Settings/Relay/{self.settings.relay_index}/Function"
        if self.settings.relay_index == 0:
            return (path, "/Settings/Relay/Function")
        return (path,)

    def _read_relay_state(self) -> int:
        return int(self._dbus_get_value(self._SYSTEM_SERVICE, self._relay_state_path()) or 0)

    def _set_relay_state(self, state: int) -> None:
        ok = self._dbus_set_value(self._SYSTEM_SERVICE, self._relay_state_path(), int(state))
        if not ok:
            raise RuntimeError(f"DBus SetValue failed for {self._relay_state_path()} -> {state}")

    def _verify_relay_state(self, target_state: int) -> None:
        self._sleep_if_configured(self.settings.verify_settle_seconds)
        read_back = self._read_relay_state()
        if read_back == target_state:
            return
        self._sleep_if_configured(self.settings.verify_retry_seconds)
        self._set_relay_state(target_state)
        self._sleep_if_configured(self.settings.verify_settle_seconds)
        read_back = self._read_relay_state()
        if read_back != target_state:
            raise RuntimeError(f"Cerbo GX relay {self.settings.relay_index} stayed at {read_back}, expected {target_state}")

    def _ensure_manual_function(self) -> None:
        last_error: Exception | None = None
        for path in self._manual_function_paths():
            try:
                if self._manual_function_matches(path):
                    return
                if self._set_manual_function_path(path):
                    return
            except Exception as exc:
                last_error = exc
                continue
        self._raise_manual_function_error(last_error)

    def _sleep_if_configured(self, seconds: float) -> None:
        if seconds:
            time.sleep(seconds)

    def _manual_function_matches(self, path: str) -> bool:
        return int(self._dbus_get_value(self._SETTINGS_SERVICE, path)) == self.settings.manual_function_value

    def _set_manual_function_path(self, path: str) -> bool:
        ok = self._dbus_set_value(self._SETTINGS_SERVICE, path, self.settings.manual_function_value)
        return ok and self._manual_function_matches(path)

    def _raise_manual_function_error(self, last_error: Exception | None) -> None:
        message = f"Unable to set Cerbo GX relay {self.settings.relay_index} to manual function"
        if last_error is not None:
            raise RuntimeError(message) from last_error
        raise RuntimeError(message)

    def _system_bus(self) -> Any:
        bus_factory = getattr(self.service, "_get_system_bus", None)
        if callable(bus_factory):
            return bus_factory()
        import dbus

        return dbus.SystemBus()

    def _busitem(self, service: str, path: str) -> Any:
        obj = self._system_bus().get_object(service, path, introspect=False)
        if hasattr(obj, "GetValue") and hasattr(obj, "SetValue"):
            return obj
        import dbus

        return dbus.Interface(obj, "com.victronenergy.BusItem")

    def _with_dbus_retry(self, call: Any) -> Any:
        try:
            return call()
        except Exception:
            reset_bus = getattr(self.service, "_reset_system_bus", None)
            if callable(reset_bus):
                reset_bus()
            time.sleep(0.1)
            return call()

    def _dbus_get_value(self, service: str, path: str) -> Any:
        return self._with_dbus_retry(lambda: self._normalized_dbus_value(self._busitem(service, path).GetValue()))

    def _dbus_set_value(self, service: str, path: str, value: int) -> bool:
        def _set() -> bool:
            raw = self._busitem(service, path).SetValue(int(value))
            if isinstance(raw, bool):
                return raw is True
            try:
                return int(str(raw)) == 0
            except (TypeError, ValueError):
                return bool(raw) is True

        return bool(self._with_dbus_retry(_set))

    @staticmethod
    def _normalized_dbus_value(value: Any) -> Any:
        if isinstance(value, (str, int, float, bool)):
            return value
        try:
            return int(value)
        except (TypeError, ValueError):
            try:
                return float(value)
            except (TypeError, ValueError):
                return str(value)
