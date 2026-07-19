# SPDX-License-Identifier: GPL-3.0-or-later
"""Native SimpleEVSE WB/DIN Modbus charger backend."""

from __future__ import annotations

import configparser
import math
from dataclasses import dataclass

from .config_file import fixed_supported_phase_selections, load_required_backend_config, validate_fixed_phase_selection
from .modbus_client import ModbusClient
from .modbus_transport import (
    ModbusTransport,
    ModbusTransportSettings,
    create_modbus_transport,
    load_modbus_transport_settings,
)
from .models import (
    ChargerState,
    PhaseSelection,
)
from .native_modbus_backend import initialize_native_modbus_backend, native_modbus_client

__all__ = (
    "SimpleEvseChargerBackend",
    "SimpleEvseChargerSettings",
    "create_modbus_transport",
    "load_simpleevse_charger_settings",
)


_SIMPLEEVSE_SUPPORTED_PHASE_SELECTIONS: tuple[PhaseSelection, ...] = ("P1",)
_SIMPLEEVSE_CURRENT_REGISTER = 1000
_SIMPLEEVSE_ACTUAL_CURRENT_REGISTER = 1001
_SIMPLEEVSE_VEHICLE_STATE_REGISTER = 1002
_SIMPLEEVSE_CONTROL_REGISTER = 1004
_SIMPLEEVSE_FIRMWARE_REGISTER = 1005
_SIMPLEEVSE_EVSE_STATE_REGISTER = 1006
_SIMPLEEVSE_STATUS_REGISTER = 1007
_SIMPLEEVSE_MAX_CURRENT_AMPS = 80
_SIMPLEEVSE_DISABLE_NOW_BIT = 0x0001
_SIMPLEEVSE_STATUS_RELAY_ON_BIT = 0x0001
_SIMPLEEVSE_STATUS_DIODE_CHECK_FAIL_BIT = 0x0002
_SIMPLEEVSE_STATUS_VENT_REQUIRED_FAIL_BIT = 0x0004
_SIMPLEEVSE_STATUS_WAITING_PILOT_RELEASE_BIT = 0x0008
_SIMPLEEVSE_STATUS_RCD_CHECK_ERROR_BIT = 0x0010


@dataclass(frozen=True)
class SimpleEvseChargerSettings:
    """Normalized SimpleEVSE Modbus settings."""

    transport_settings: ModbusTransportSettings
    profile_name: str
    supported_phase_selections: tuple[PhaseSelection, ...]
    current_register: int
    actual_current_register: int
    vehicle_state_register: int
    control_register: int
    firmware_register: int
    evse_state_register: int
    status_register: int


def _supported_phase_selections(parser: configparser.ConfigParser) -> tuple[PhaseSelection, ...]:
    """Return one fixed configured phase layout for SimpleEVSE-backed installations."""
    return fixed_supported_phase_selections(parser, _SIMPLEEVSE_SUPPORTED_PHASE_SELECTIONS, "SimpleEVSE")


def load_simpleevse_charger_settings(service: object, config_path: str) -> SimpleEvseChargerSettings:
    """Return normalized SimpleEVSE charger settings."""
    parser = load_required_backend_config(config_path, "SimpleEVSE charger")
    return SimpleEvseChargerSettings(
        transport_settings=load_modbus_transport_settings(parser, service),
        profile_name="simpleevse",
        supported_phase_selections=_supported_phase_selections(parser),
        current_register=_SIMPLEEVSE_CURRENT_REGISTER,
        actual_current_register=_SIMPLEEVSE_ACTUAL_CURRENT_REGISTER,
        vehicle_state_register=_SIMPLEEVSE_VEHICLE_STATE_REGISTER,
        control_register=_SIMPLEEVSE_CONTROL_REGISTER,
        firmware_register=_SIMPLEEVSE_FIRMWARE_REGISTER,
        evse_state_register=_SIMPLEEVSE_EVSE_STATE_REGISTER,
        status_register=_SIMPLEEVSE_STATUS_REGISTER,
    )


def _vehicle_status_text(vehicle_state: int) -> str | None:
    """Return one normalized high-level vehicle state."""
    return {
        1: "ready",
        2: "vehicle-present",
        3: "charging",
        4: "charging-ventilation",
        5: "error",
    }.get(int(vehicle_state))


def _evse_status_text(evse_state: int) -> str | None:
    """Return one normalized EVSE state."""
    return {
        1: "idle",
        2: "ready",
        3: "disabled",
    }.get(int(evse_state))


def _fault_text(vehicle_state: int, status_bits: int) -> str | None:
    """Return one normalized SimpleEVSE fault text from status flags."""
    faults = _simpleevse_fault_tokens(status_bits)
    if int(vehicle_state) == 5 and not faults:
        faults.append("vehicle-failure")
    return ",".join(faults) if faults else None


def _simpleevse_fault_tokens(status_bits: int) -> list[str]:
    """Return normalized fault tokens from SimpleEVSE status bits."""
    faults: list[str] = []
    if status_bits & _SIMPLEEVSE_STATUS_DIODE_CHECK_FAIL_BIT:
        faults.append("diode-check-fail")
    if status_bits & _SIMPLEEVSE_STATUS_VENT_REQUIRED_FAIL_BIT:
        faults.append("vent-required-fail")
    if status_bits & _SIMPLEEVSE_STATUS_WAITING_PILOT_RELEASE_BIT:
        faults.append("pilot-release-wait")
    if status_bits & _SIMPLEEVSE_STATUS_RCD_CHECK_ERROR_BIT:
        faults.append("rcd-check-error")
    return faults


def _enabled(control_bits: int, evse_state: int) -> bool:
    """Return whether the charger is logically enabled."""
    if int(evse_state) == 3:
        return False
    return (int(control_bits) & _SIMPLEEVSE_DISABLE_NOW_BIT) == 0


def _status_text(vehicle_state: int, evse_state: int, fault_text: str | None) -> str | None:
    """Return one normalized charger status text."""
    if fault_text:
        return "error"
    vehicle_text = _vehicle_status_text(vehicle_state)
    if _non_error_status(vehicle_text):
        return vehicle_text
    return _evse_status_text(evse_state)


def _non_error_status(vehicle_text: str | None) -> bool:
    """Return whether a vehicle status may be exposed directly."""
    return vehicle_text is not None and vehicle_text != "error"


def _rounded_current_setting(amps: float) -> int:
    """Return one SimpleEVSE-compatible whole-amp current setpoint."""
    rounded = int(math.floor(float(amps) + 0.5))
    _validate_simpleevse_current(amps, rounded)
    return rounded


def _validate_simpleevse_current(amps: float, rounded: int) -> None:
    """Validate one SimpleEVSE current setpoint."""
    if rounded < 0 or rounded > _SIMPLEEVSE_MAX_CURRENT_AMPS:
        raise ValueError(
            f"Unsupported charger current '{amps}' for SimpleEVSE backend (expected 0..{_SIMPLEEVSE_MAX_CURRENT_AMPS} A)"
        )


class SimpleEvseChargerBackend:
    """Native Modbus backend for SimpleEVSE WB/DIN controllers."""

    service: object
    config_path: str
    settings: SimpleEvseChargerSettings
    _transport: ModbusTransport | None
    _client_cache: ModbusClient | None

    def __init__(self, service: object, config_path: str = "") -> None:
        initialize_native_modbus_backend(self, service, config_path, load_simpleevse_charger_settings)

    def _client(self) -> ModbusClient:
        """Return the lazily created Modbus client for this backend instance."""
        return native_modbus_client(self)

    def _read_register(self, address: int) -> int:
        """Read one 16-bit holding register from the SimpleEVSE."""
        return int(self._client().read_scalar("holding", address, "uint16"))

    def _write_register(self, address: int, value: int) -> None:
        """Write one 16-bit holding register using Modbus function 16."""
        self._client().write_multiple_registers(address, (int(value) & 0xFFFF,))

    def read_charger_state(self) -> ChargerState:
        """Read one normalized charger state from the SimpleEVSE Modbus registers."""
        fixed_phase_selection = self.settings.supported_phase_selections[0]
        current_setting = self._read_register(self.settings.current_register)
        actual_current = self._read_register(self.settings.actual_current_register)
        vehicle_state = self._read_register(self.settings.vehicle_state_register)
        control_bits = self._read_register(self.settings.control_register)
        evse_state = self._read_register(self.settings.evse_state_register)
        status_bits = self._read_register(self.settings.status_register)
        fault_text = _fault_text(vehicle_state, status_bits)
        return ChargerState(
            enabled=_enabled(control_bits, evse_state),
            current_amps=float(current_setting),
            phase_selection=fixed_phase_selection,
            actual_current_amps=float(actual_current),
            status_text=_status_text(vehicle_state, evse_state, fault_text),
            fault_text=fault_text,
        )

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable charging through control register 1004 bit0."""
        self._write_register(self.settings.control_register, 0 if bool(enabled) else _SIMPLEEVSE_DISABLE_NOW_BIT)

    def set_current(self, amps: float) -> None:
        """Apply one whole-amp current limit to register 1000."""
        self._write_register(self.settings.current_register, _rounded_current_setting(amps))

    def set_phase_selection(self, selection: PhaseSelection) -> None:
        """Reject native phase writes, except for the already configured fixed layout."""
        validate_fixed_phase_selection(selection, self.settings.supported_phase_selections[0], "SimpleEVSE")
