# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimal Modbus transport layer for serial RTU, TCP, and UDP.

The transport code owns only byte-level exchange concerns: socket framing,
serial RTU CRC handling, timeout normalization, and Venus serial-port ownership.
Register encoding, charger semantics, and profile interpretation deliberately
live in higher layers so each transport backend remains narrowly responsible.
Failures are normalized into service-level reasons for diagnostics and retries.
"""

from __future__ import annotations

import socket
import struct

from .modbus_transport_errors import (
    ModbusPortBusyError,
    ModbusPortOwnershipError,
    ModbusResponseError,
    ModbusSlaveOfflineError,
    ModbusTimeoutError,
    ModbusTransportError,
)
from .modbus_transport_types import (
    ModbusParity,
    ModbusRequest,
    ModbusTransport,
    ModbusTransportKind,
    ModbusTransportSettings,
    SerialPortOwnerKind,
)
from .modbus_transport_serial import (
    ModbusSerialRtuTransport,
    _VenusSerialPortOwner,
    _configured_serial_attrs,
    _crc_frame,
    _expected_rtu_response_length,
    _modbus_crc,
    _positive_serial_write_count,
    _serial_baudrate_constant,
    _serial_port_owner,
)


def modbus_transport_issue_reason(error: BaseException) -> str | None:
    """Return one normalized reason label for a transport-layer Modbus failure."""
    from venus_evcharger.backend.modbus_transport_config import modbus_transport_issue_reason as _impl

    return _impl(error)


from venus_evcharger.backend.modbus_transport_config import (
    _default_modbus_serial_fields,
    _default_port_owner_fields,
    _normalized_baudrate,
    _normalized_bytesize,
    _normalized_device,
    _normalized_parity,
    _normalized_port,
    _normalized_retry_count,
    _normalized_retry_delay_seconds,
    _normalized_serial_port_owner,
    _normalized_stopbits,
    _normalized_timeout_seconds,
    _normalized_transport_kind,
    _normalized_unit_id,
    _optional_transport_command,
    _port_owner_commands,
    _required_host_port,
    _serial_transport_fields,
    _serial_transport_runtime_fields,
    _transport_runtime_fields,
    load_modbus_transport_settings,
)


__all__ = [
    "ModbusParity",
    "ModbusPortBusyError",
    "ModbusPortOwnershipError",
    "ModbusRequest",
    "ModbusResponseError",
    "ModbusSlaveOfflineError",
    "ModbusTimeoutError",
    "ModbusTransport",
    "ModbusTransportError",
    "ModbusTransportKind",
    "ModbusSerialRtuTransport",
    "ModbusTransportSettings",
    "SerialPortOwnerKind",
    "_VenusSerialPortOwner",
    "_configured_serial_attrs",
    "_crc_frame",
    "_default_modbus_serial_fields",
    "_default_port_owner_fields",
    "_expected_rtu_response_length",
    "_modbus_crc",
    "_normalized_baudrate",
    "_normalized_bytesize",
    "_normalized_device",
    "_normalized_parity",
    "_normalized_port",
    "_normalized_retry_count",
    "_normalized_retry_delay_seconds",
    "_normalized_serial_port_owner",
    "_normalized_stopbits",
    "_normalized_timeout_seconds",
    "_normalized_transport_kind",
    "_normalized_unit_id",
    "_optional_transport_command",
    "_port_owner_commands",
    "_positive_serial_write_count",
    "_required_host_port",
    "_serial_baudrate_constant",
    "_serial_port_owner",
    "_serial_transport_fields",
    "_serial_transport_runtime_fields",
    "_transport_runtime_fields",
    "load_modbus_transport_settings",
    "modbus_transport_issue_reason",
]


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    """Return exactly size bytes from one connected socket."""
    chunks = bytearray()
    while len(chunks) < size:
        chunk = sock.recv(size - len(chunks))
        if not chunk:
            raise TimeoutError("Modbus transport closed before full response was received")
        chunks.extend(chunk)
    return bytes(chunks)


class ModbusTcpTransport:
    """Simple one-shot Modbus TCP transport."""

    def __init__(self, settings: ModbusTransportSettings) -> None:
        self.settings = settings
        self._transaction_id = 0

    def exchange(self, request: ModbusRequest, *, timeout_seconds: float) -> bytes:
        """Send one Modbus TCP request and return the response PDU."""
        self._transaction_id = (self._transaction_id + 1) & 0xFFFF
        pdu = bytes((request.function_code,)) + request.payload
        adu = (
            struct.pack(">H", self._transaction_id)
            + b"\x00\x00"
            + struct.pack(">H", len(pdu) + 1)
            + bytes((request.unit_id,))
            + pdu
        )
        assert self.settings.host is not None
        assert self.settings.port is not None
        with socket.create_connection((self.settings.host, self.settings.port), timeout=timeout_seconds) as sock:
            sock.settimeout(timeout_seconds)
            sock.sendall(adu)
            header = _recv_exact(sock, 7)
            length = struct.unpack(">H", header[4:6])[0]
            body = _recv_exact(sock, max(0, length - 1))
        return body


class ModbusUdpTransport:
    """Simple one-shot Modbus UDP transport using MBAP framing."""

    def __init__(self, settings: ModbusTransportSettings) -> None:
        self.settings = settings
        self._transaction_id = 0

    def exchange(self, request: ModbusRequest, *, timeout_seconds: float) -> bytes:
        """Send one Modbus UDP request and return the response PDU."""
        self._transaction_id = (self._transaction_id + 1) & 0xFFFF
        pdu = bytes((request.function_code,)) + request.payload
        adu = (
            struct.pack(">H", self._transaction_id)
            + b"\x00\x00"
            + struct.pack(">H", len(pdu) + 1)
            + bytes((request.unit_id,))
            + pdu
        )
        assert self.settings.host is not None
        assert self.settings.port is not None
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout_seconds)
            sock.sendto(adu, (self.settings.host, self.settings.port))
            response, _ = sock.recvfrom(260)
        if len(response) < 7:
            raise TimeoutError("Incomplete Modbus UDP response")
        length = struct.unpack(">H", response[4:6])[0]
        return response[7 : 7 + max(0, length - 1)]


def create_modbus_transport(settings: ModbusTransportSettings) -> ModbusTransport:
    """Create one concrete Modbus transport from normalized settings."""
    if settings.transport_kind == "serial_rtu":
        return ModbusSerialRtuTransport(settings)
    if settings.transport_kind == "udp":
        return ModbusUdpTransport(settings)
    return ModbusTcpTransport(settings)
