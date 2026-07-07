# SPDX-License-Identifier: GPL-3.0-or-later
"""Small Modbus client helpers built on top of the transport layer."""

from __future__ import annotations

import struct
from typing import Iterable, Literal

from .modbus_transport import ModbusRequest, ModbusTransport


_READ_BITS_FUNCTIONS = {"coil": 0x01, "discrete": 0x02}
_READ_REGISTERS_FUNCTIONS = {"holding": 0x03, "input": 0x04}
_WRITE_SINGLE_COIL = 0x05
_WRITE_SINGLE_REGISTER = 0x06
_WRITE_MULTIPLE_REGISTERS = 0x10
_BYTE_ORDER: Literal["big"] = "big"
_DEFAULT_WORD_ORDER = "big"
_SUPPORTED_WORD_ORDERS = frozenset({_DEFAULT_WORD_ORDER, "little"})


class ModbusProtocolError(ValueError):
    """Raised when one Modbus response is malformed."""


class ModbusDeviceError(RuntimeError):
    """Raised when one Modbus device responds with an exception frame."""


def register_count(data_type: str) -> int:
    """Return how many 16-bit registers are needed for one normalized data type."""
    normalized = str(data_type).strip().lower()
    if normalized in {"uint32", "int32", "float32"}:
        return 2
    return 1


def decode_register_value(
    registers: tuple[int, ...],
    data_type: str,
    word_order: str = _DEFAULT_WORD_ORDER,
) -> float | int | bool:
    """Decode one register tuple into the configured scalar type."""
    normalized_type = str(data_type).strip().lower()
    normalized_word_order = _normalized_word_order(word_order)
    ordered = _ordered_registers(registers, normalized_word_order)
    if not ordered:
        raise ValueError("Modbus register decode requires at least one register")
    payload = b"".join(_u16_bytes(register) for register in ordered)
    return _decoded_register_payload(ordered, payload, normalized_type, data_type)


def encode_register_value(
    value: float | int | bool,
    data_type: str,
    word_order: str = _DEFAULT_WORD_ORDER,
) -> tuple[int, ...]:
    """Encode one scalar value into Modbus register words."""
    normalized_type = str(data_type).strip().lower()
    normalized_word_order = _normalized_word_order(word_order)
    registers = _encoded_registers(value, normalized_type, data_type)
    if normalized_word_order == "little":
        return tuple(reversed(registers))
    return registers


def _normalized_word_order(word_order: str) -> str:
    """Return one explicit Modbus word order or reject malformed values."""
    normalized = str(word_order).strip()
    if normalized not in _SUPPORTED_WORD_ORDERS:
        raise ValueError(f"Unsupported Modbus word order '{word_order}'")
    return normalized


def _ordered_registers(registers: tuple[int, ...], word_order: str) -> tuple[int, ...]:
    """Return register words in the requested decode order."""
    if word_order != "little" or len(registers) < 2:
        return registers
    return tuple(reversed(registers))


def _decoded_register_payload(
    ordered: tuple[int, ...],
    payload: bytes,
    normalized_type: str,
    data_type: str,
) -> float | int | bool:
    """Return one decoded scalar from ordered register words."""
    decoder = _REGISTER_DECODERS.get(normalized_type)
    if decoder is None:
        raise ValueError(f"Unsupported Modbus data type '{data_type}'")
    return decoder(ordered, payload)


def _decoded_int16(value: int) -> int:
    """Return one signed 16-bit integer from an unsigned register value."""
    return value - 0x10000 if value & 0x8000 else value


def _encoded_registers(value: float | int | bool, normalized_type: str, data_type: str) -> tuple[int, ...]:
    """Return register words for one encoded scalar value."""
    encoder = _REGISTER_ENCODERS.get(normalized_type)
    if encoder is None:
        raise ValueError(f"Unsupported Modbus data type '{data_type}'")
    return encoder(value)


def _payload_registers(payload: bytes) -> tuple[int, int]:
    """Return the two 16-bit register words contained in one four-byte payload."""
    if len(payload) != 4:
        raise ValueError("Modbus 32-bit payload requires exactly four bytes")
    return _uint_from_bytes(payload[:2]), _uint_from_bytes(payload[2:])


def _uint_from_bytes(payload: bytes) -> int:
    """Return one unsigned big-endian integer from bytes."""
    return int.from_bytes(payload, _BYTE_ORDER)


def _int_from_bytes(payload: bytes) -> int:
    """Return one signed big-endian integer from bytes."""
    return int.from_bytes(payload, _BYTE_ORDER, signed=True)


def _u16_bytes(value: int) -> bytes:
    """Return one unsigned 16-bit big-endian payload."""
    return int(value).to_bytes(2, _BYTE_ORDER)


def _u32_bytes(value: int) -> bytes:
    """Return one unsigned 32-bit big-endian payload."""
    return int(value).to_bytes(4, _BYTE_ORDER)


def _i32_bytes(value: int) -> bytes:
    """Return one signed 32-bit big-endian payload."""
    return int(value).to_bytes(4, _BYTE_ORDER, signed=True)


def _decode_bool_register(ordered: tuple[int, ...], _payload: bytes) -> bool:
    """Return one bool decoded from the first register word."""
    return bool(ordered[0])


def _decode_uint16_register(ordered: tuple[int, ...], _payload: bytes) -> int:
    """Return one unsigned 16-bit integer decoded from the first register word."""
    return ordered[0]


def _decode_int16_register(ordered: tuple[int, ...], _payload: bytes) -> int:
    """Return one signed 16-bit integer decoded from the first register word."""
    return _decoded_int16(ordered[0])


def _decode_uint32_register(_ordered: tuple[int, ...], payload: bytes) -> int:
    """Return one unsigned 32-bit integer decoded from the payload bytes."""
    return _uint_from_bytes(payload)


def _decode_int32_register(_ordered: tuple[int, ...], payload: bytes) -> int:
    """Return one signed 32-bit integer decoded from the payload bytes."""
    return _int_from_bytes(payload)


def _decode_float32_register(_ordered: tuple[int, ...], payload: bytes) -> float:
    """Return one IEEE754 float decoded from the payload bytes."""
    return float(struct.unpack(">f", payload)[0])


def _encode_bool_register(value: float | int | bool) -> tuple[int, ...]:
    """Return one encoded bool register tuple."""
    return (1 if bool(value) else 0,)


def _encode_16bit_register(value: float | int | bool) -> tuple[int, ...]:
    """Return one encoded single-register integer tuple."""
    return (int(value) & 0xFFFF,)


def _encode_uint32_register(value: float | int | bool) -> tuple[int, ...]:
    """Return one encoded unsigned 32-bit register tuple."""
    return _payload_registers(_u32_bytes(int(value)))


def _encode_int32_register(value: float | int | bool) -> tuple[int, ...]:
    """Return one encoded signed 32-bit register tuple."""
    return _payload_registers(_i32_bytes(int(value)))


def _encode_float32_register(value: float | int | bool) -> tuple[int, ...]:
    """Return one encoded IEEE754 float register tuple."""
    return _payload_registers(struct.pack(">f", float(value)))


_REGISTER_DECODERS = {
    "bool": _decode_bool_register,
    "uint16": _decode_uint16_register,
    "int16": _decode_int16_register,
    "uint32": _decode_uint32_register,
    "int32": _decode_int32_register,
    "float32": _decode_float32_register,
}

_REGISTER_ENCODERS = {
    "bool": _encode_bool_register,
    "uint16": _encode_16bit_register,
    "int16": _encode_16bit_register,
    "uint32": _encode_uint32_register,
    "int32": _encode_int32_register,
    "float32": _encode_float32_register,
}


class ModbusClient:
    """Minimal Modbus client that reads/writes coils and registers through one transport."""

    def __init__(self, transport: ModbusTransport, unit_id: int, timeout_seconds: float) -> None:
        self.transport = transport
        self.unit_id = int(unit_id)
        self.timeout_seconds = float(timeout_seconds)

    def _response_pdu(self, function_code: int, payload: bytes) -> bytes:
        """Perform one Modbus transaction and validate the returned function code."""
        response = self.transport.exchange(
            ModbusRequest(unit_id=self.unit_id, function_code=function_code, payload=payload),
            timeout_seconds=self.timeout_seconds,
        )
        if not response:
            raise ModbusProtocolError("Empty Modbus response")
        response_function = response[0]
        if response_function == (function_code | 0x80):
            code = response[1] if len(response) > 1 else -1
            raise ModbusDeviceError(f"Modbus exception response 0x{code:02x}")
        if response_function != function_code:
            raise ModbusProtocolError(
                f"Unexpected Modbus response function 0x{response_function:02x} for request 0x{function_code:02x}"
            )
        return response[1:]

    def _read_bits(self, register_type: str, address: int, count: int) -> tuple[bool, ...]:
        """Read one coil/discrete bit range."""
        function_code = _READ_BITS_FUNCTIONS[register_type]
        payload = _u16_bytes(int(address)) + _u16_bytes(int(count))
        response_data = self._response_pdu(function_code, payload)
        if not response_data:
            raise ModbusProtocolError("Missing Modbus bit byte-count")
        byte_count = response_data[0]
        if len(response_data) != 1 + byte_count:
            raise ModbusProtocolError("Incomplete Modbus bit response")
        raw_bytes = response_data[1:]
        values: list[bool] = []
        for index in range(count):
            byte_index = index // 8
            bit_index = index % 8
            values.append(bool((raw_bytes[byte_index] >> bit_index) & 0x01))
        return tuple(values)

    def _read_registers(self, register_type: str, address: int, count: int) -> tuple[int, ...]:
        """Read one holding/input register range."""
        function_code = _READ_REGISTERS_FUNCTIONS[register_type]
        payload = _u16_bytes(int(address)) + _u16_bytes(int(count))
        response_data = self._response_pdu(function_code, payload)
        if not response_data:
            raise ModbusProtocolError("Missing Modbus register byte-count")
        byte_count = response_data[0]
        if len(response_data) != 1 + byte_count or byte_count != count * 2:
            raise ModbusProtocolError("Incomplete Modbus register response")
        register_bytes = response_data[1:]
        return tuple(
            _uint_from_bytes(register_bytes[index : index + 2])
            for index in range(0, byte_count, 2)
        )

    def read_scalar(
        self,
        register_type: str,
        address: int,
        data_type: str,
        word_order: str = _DEFAULT_WORD_ORDER,
    ) -> float | int | bool:
        """Read one scalar coil/discrete/register value."""
        normalized_type = str(data_type).strip().lower()
        normalized_register_type = str(register_type).strip().lower()
        if normalized_register_type in _READ_BITS_FUNCTIONS:
            value = self._read_bits(normalized_register_type, address, 1)[0]
            return bool(value) if normalized_type == "bool" else int(value)
        registers = self._read_registers(normalized_register_type, address, register_count(normalized_type))
        return decode_register_value(registers, normalized_type, word_order)

    def write_single_coil(self, address: int, value: bool) -> None:
        """Write one Modbus coil."""
        encoded = 0xFF00 if bool(value) else 0x0000
        payload = _u16_bytes(int(address)) + _u16_bytes(encoded)
        response_data = self._response_pdu(_WRITE_SINGLE_COIL, payload)
        if len(response_data) != 4:
            raise ModbusProtocolError("Unexpected Modbus coil write response length")

    def write_single_register(self, address: int, value: int) -> None:
        """Write one Modbus holding register."""
        payload = _u16_bytes(int(address)) + _u16_bytes(int(value) & 0xFFFF)
        response_data = self._response_pdu(_WRITE_SINGLE_REGISTER, payload)
        if len(response_data) != 4:
            raise ModbusProtocolError("Unexpected Modbus register write response length")

    def write_multiple_registers(self, address: int, values: Iterable[int]) -> None:
        """Write one contiguous Modbus holding register range."""
        registers = tuple(int(value) & 0xFFFF for value in values)
        payload_bytes = b"".join(_u16_bytes(register) for register in registers)
        payload = (
            _u16_bytes(int(address))
            + _u16_bytes(len(registers))
            + bytes((len(payload_bytes),))
            + payload_bytes
        )
        response_data = self._response_pdu(_WRITE_MULTIPLE_REGISTERS, payload)
        if len(response_data) != 4:
            raise ModbusProtocolError("Unexpected Modbus multi-register write response length")
