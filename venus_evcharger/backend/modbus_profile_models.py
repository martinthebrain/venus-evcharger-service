# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime model objects for generic Modbus charger profiles."""

from __future__ import annotations

from dataclasses import dataclass

from venus_evcharger.core.contracts import finite_float_or_none

from .modbus_client import ModbusClient, encode_register_value
from .models import ChargerState, PhaseSelection, normalize_phase_selection

ModbusReadRegisterType = str
ModbusWriteRegisterType = str
_ModbusScalar = int | float | bool | str


@dataclass(frozen=True)
class ModbusReadField:
    """One configured Modbus read mapping."""

    register_type: ModbusReadRegisterType
    address: int
    data_type: str
    scale: float
    word_order: str
    value_map: dict[int, str] | None

    def read(self, client: ModbusClient) -> object:
        """Read one normalized value from the configured Modbus source."""
        raw_value = client.read_scalar(self.register_type, self.address, self.data_type, self.word_order)
        if self.value_map is not None:
            return self._mapped_read_value(raw_value)
        return self._scaled_read_value(raw_value)

    def _mapped_read_value(self, raw_value: object) -> object:
        """Return one mapped read value when a numeric value map is configured."""
        assert self.value_map is not None
        scalar = _int_scalar(raw_value)
        mapped = self.value_map.get(scalar)
        return mapped if mapped is not None else str(scalar)

    def _scaled_read_value(self, raw_value: object) -> object:
        """Return one raw or scaled read value when no value map is configured."""
        if isinstance(raw_value, bool):
            return raw_value
        if self.scale and self.scale not in {0.0, 1.0}:
            return _float_scalar(raw_value) / float(self.scale)
        return raw_value


@dataclass(frozen=True)
class ModbusEnableWrite:
    """One configured Modbus write mapping for charger enable/disable."""

    register_type: ModbusWriteRegisterType
    address: int
    true_value: int
    false_value: int

    def write(self, client: ModbusClient, enabled: bool) -> None:
        """Apply one enable/disable value through the configured Modbus sink."""
        target_value = self.true_value if enabled else self.false_value
        if self.register_type == "coil":
            client.write_single_coil(self.address, bool(target_value))
            return
        client.write_single_register(self.address, target_value)


@dataclass(frozen=True)
class ModbusNumericWrite:
    """One configured Modbus write mapping for numeric charger values."""

    register_type: ModbusWriteRegisterType
    address: int
    data_type: str
    scale: float
    word_order: str

    def write(self, client: ModbusClient, value: float) -> None:
        """Write one scaled numeric value through the configured Modbus sink."""
        if self.register_type != "holding":
            raise ValueError("Numeric Modbus writes currently require RegisterType=holding")
        registers = self._numeric_write_registers(value)
        self._write_registers(client, registers)

    def _numeric_write_registers(self, value: float) -> tuple[int, ...]:
        """Return encoded Modbus registers for one numeric write value."""
        scaled = float(value) * float(self.scale or 1.0)
        rounded = int(round(scaled)) if self.data_type != "float32" else scaled
        return encode_register_value(rounded, self.data_type, self.word_order)

    def _write_registers(self, client: ModbusClient, registers: tuple[int, ...]) -> None:
        """Write one or many registers depending on the encoded payload width."""
        if len(registers) == 1:
            client.write_single_register(self.address, registers[0])
            return
        client.write_multiple_registers(self.address, registers)


@dataclass(frozen=True)
class ModbusPhaseWrite:
    """One configured Modbus write mapping for logical phase selection."""

    register_type: ModbusWriteRegisterType
    address: int
    data_type: str
    word_order: str
    selection_map: dict[PhaseSelection, int]

    def write(self, client: ModbusClient, selection: PhaseSelection) -> None:
        """Write one mapped phase-selection value through the configured Modbus sink."""
        if selection not in self.selection_map:
            raise ValueError(f"Unsupported phase selection '{selection}' for Modbus phase write")
        raw_value = self.selection_map[selection]
        if self.register_type == "coil":
            client.write_single_coil(self.address, bool(raw_value))
            return
        registers = encode_register_value(raw_value, self.data_type, self.word_order)
        if len(registers) == 1:
            client.write_single_register(self.address, registers[0])
            return
        client.write_multiple_registers(self.address, registers)


@dataclass(frozen=True)
class GenericModbusChargerProfile:
    """One generic register-schema profile consumed by the Modbus charger backend."""

    profile_name: str
    supported_phase_selections: tuple[PhaseSelection, ...]
    state_enabled: ModbusReadField | None
    state_current: ModbusReadField | None
    state_phase_selection: ModbusReadField | None
    state_actual_current: ModbusReadField | None
    state_power_watts: ModbusReadField | None
    state_energy_kwh: ModbusReadField | None
    state_status: ModbusReadField | None
    state_fault: ModbusReadField | None
    enable_write: ModbusEnableWrite | None
    current_write: ModbusNumericWrite
    phase_write: ModbusPhaseWrite | None
    enable_uses_current_write: bool = False
    enable_default_current_amps: float = 6.0

    def read_state(
        self,
        client: ModbusClient,
        *,
        cached_enabled: bool | None,
        cached_current_amps: float | None,
        cached_phase_selection: PhaseSelection,
    ) -> ChargerState:
        """Return one normalized charger state from configured Modbus registers."""
        enabled = _optional_field_value(self.state_enabled, client, cached_enabled)
        current_amps = _optional_float_value(self.state_current, client, cached_current_amps)
        phase_selection = self._resolved_phase_selection(client, cached_phase_selection)
        enabled = self._resolved_enabled(enabled, current_amps)
        return ChargerState(
            enabled=_optional_bool(enabled),
            current_amps=current_amps,
            phase_selection=phase_selection,
            actual_current_amps=_optional_float_value(self.state_actual_current, client, None),
            power_w=_optional_float_value(self.state_power_watts, client, None),
            energy_kwh=_optional_float_value(self.state_energy_kwh, client, None),
            status_text=_optional_text_value(self.state_status, client),
            fault_text=_optional_text_value(self.state_fault, client),
        )

    def _resolved_phase_selection(self, client: ModbusClient, cached_phase_selection: PhaseSelection) -> PhaseSelection:
        """Return one supported phase selection from cached and live inputs."""
        raw_phase_selection = _optional_field_value(self.state_phase_selection, client, None)
        if raw_phase_selection is None:
            return self._supported_phase_selection(cached_phase_selection)
        normalized = normalize_phase_selection(raw_phase_selection, cached_phase_selection)
        return self._supported_phase_selection(normalized)

    def _supported_phase_selection(self, selection: PhaseSelection) -> PhaseSelection:
        """Return one phase selection guaranteed to be supported by this profile."""
        if selection in self.supported_phase_selections:
            return selection
        return self.supported_phase_selections[0]

    def _resolved_enabled(self, enabled: object, current_amps: float | None) -> object:
        """Infer enabled state from current writes when no explicit state bit exists."""
        if enabled is not None or not self.enable_uses_current_write or current_amps is None:
            return enabled
        return current_amps > 0.0

    def set_enabled(self, client: ModbusClient, enabled: bool) -> None:
        """Apply one enable/disable command through the configured Modbus mapping."""
        if self.enable_write is None:
            raise ValueError("Configured Modbus charger profile does not expose direct enable writes")
        self.enable_write.write(client, enabled)

    def set_current(self, client: ModbusClient, amps: float) -> None:
        """Apply one current command through the configured Modbus mapping."""
        self.current_write.write(client, amps)

    def set_phase_selection(self, client: ModbusClient, selection: PhaseSelection) -> None:
        """Apply one phase-selection command when the profile exposes it."""
        if self.phase_write is None:
            raise ValueError("Configured Modbus charger profile does not expose phase selection writes")
        self.phase_write.write(client, selection)


def _int_scalar(raw_value: object) -> int:
    """Return one Modbus scalar coerced to integer for mapped reads."""
    return int(_modbus_scalar(raw_value))


def _float_scalar(raw_value: object) -> float:
    """Return one Modbus scalar coerced to float for scaled reads."""
    return float(_modbus_scalar(raw_value))


def _modbus_scalar(raw_value: object) -> _ModbusScalar:
    """Return one primitive Modbus scalar suitable for numeric coercion."""
    if isinstance(raw_value, (int, float, bool, str)):
        return raw_value
    raise TypeError(f"Unsupported Modbus scalar {type(raw_value).__name__}")


def _optional_field_value(field: ModbusReadField | None, client: ModbusClient, default: object) -> object:
    """Return one optional field value or the given default."""
    return default if field is None else field.read(client)


def _optional_float_value(field: ModbusReadField | None, client: ModbusClient, default: float | None) -> float | None:
    """Return one optional float field value."""
    if field is None:
        return default
    value = field.read(client)
    number = finite_float_or_none(value)
    return default if number is None else float(number)


def _optional_text_value(field: ModbusReadField | None, client: ModbusClient) -> str | None:
    """Return one optional text field value."""
    if field is None:
        return None
    value = field.read(client)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_bool(value: object) -> bool | None:
    """Return one optional boolean."""
    return None if value is None else bool(value)
