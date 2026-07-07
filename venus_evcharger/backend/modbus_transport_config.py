# SPDX-License-Identifier: GPL-3.0-or-later
"""Configuration and normalization helpers for Modbus transport setup."""

from __future__ import annotations

import configparser

from venus_evcharger.backend.modbus_transport import (
    ModbusParity,
    ModbusPortBusyError,
    ModbusPortOwnershipError,
    ModbusResponseError,
    ModbusSlaveOfflineError,
    ModbusTimeoutError,
    ModbusTransportError,
    ModbusTransportKind,
    ModbusTransportSettings,
    SerialPortOwnerKind,
)


def modbus_transport_issue_reason(error: BaseException) -> str | None:
    """Return one normalized reason label for a transport-layer Modbus failure."""
    for error_type, reason in _MODBUS_TRANSPORT_ISSUE_REASONS:
        if isinstance(error, error_type):
            return reason
    return "error" if isinstance(error, (ModbusTransportError, OSError)) else None


_MODBUS_TRANSPORT_ISSUE_REASONS: tuple[tuple[type[BaseException] | tuple[type[BaseException], ...], str], ...] = (
    (ModbusPortBusyError, "busy"),
    (ModbusPortOwnershipError, "ownership"),
    (ModbusSlaveOfflineError, "offline"),
    ((ModbusTimeoutError, TimeoutError), "timeout"),
    (ModbusResponseError, "response"),
)

_PARITY_CHOICES: dict[str, ModbusParity] = {
    "N": "N",
    "E": "E",
    "O": "O",
}


def _text_or_default(value: object, default: str) -> str:
    """Return a stripped config value or a caller-owned default."""
    if value is None:
        return default
    return str(value).strip() or default


def _text_or_none(value: object) -> str | None:
    """Return a stripped config value when one is configured."""
    if value is None:
        return None
    return str(value).strip() or None


def _normalized_transport_kind(value: object) -> ModbusTransportKind:
    """Return one supported transport kind."""
    normalized = str(value).strip().lower()
    if normalized in {"serial", "rtu", "serial_rtu"}:
        return "serial_rtu"
    if normalized == "udp":
        return "udp"
    return "tcp"


def _normalized_unit_id(value: object) -> int:
    """Return one validated Modbus unit/slave identifier."""
    unit_id = int(_text_or_default(value, "1"))
    if unit_id < 0 or unit_id > 247:
        raise ValueError(f"Unsupported Modbus unit id '{value}'")
    return unit_id


def _normalized_timeout_seconds(value: object, default: float) -> float:
    """Return one validated timeout value."""
    if value is None:
        return float(default)
    try:
        timeout = float(str(value).strip())
    except (TypeError, ValueError):
        timeout = default
    if timeout <= 0.0:
        return float(default)
    return timeout


def _normalized_port(value: object, default: int) -> int:
    """Return one validated TCP/UDP port."""
    port = int(_text_or_default(value, str(default)))
    if port <= 0 or port > 65535:
        raise ValueError(f"Unsupported Modbus port '{value}'")
    return port


def _normalized_device(value: object) -> str:
    """Return one required serial device path."""
    device = "" if value is None else str(value).strip()
    if not device:
        raise ValueError("Modbus serial_rtu transport requires Transport.Device")
    return device


def _normalized_baudrate(value: object) -> int:
    """Return one validated serial baudrate."""
    baudrate = int(_text_or_default(value, "9600"))
    if baudrate <= 0:
        raise ValueError(f"Unsupported Modbus baudrate '{value}'")
    return baudrate


def _normalized_bytesize(value: object) -> int:
    """Return one validated serial bytesize."""
    bytesize = int(_text_or_default(value, "8"))
    if bytesize not in {5, 6, 7, 8}:
        raise ValueError(f"Unsupported Modbus bytesize '{value}'")
    return bytesize


def _normalized_parity(value: object) -> ModbusParity:
    """Return one validated serial parity setting."""
    parity_text = _text_or_none(value)
    if parity_text is None:
        return "N"
    parity = _PARITY_CHOICES.get(parity_text.upper())
    if parity is None:
        raise ValueError(f"Unsupported Modbus parity '{value}'")
    return parity


def _normalized_stopbits(value: object) -> int:
    """Return one validated serial stopbit count."""
    stopbits = int(_text_or_default(value, "1"))
    if stopbits not in {1, 2}:
        raise ValueError(f"Unsupported Modbus stopbits '{value}'")
    return stopbits


def _normalized_serial_port_owner(value: object) -> SerialPortOwnerKind:
    """Return one supported serial port-owner strategy."""
    normalized = str(value).strip().lower()
    if normalized in {"venus", "venus_serial_starter", "serial-starter", "victron"}:
        return "venus_serial_starter"
    return "none"


def _normalized_retry_count(value: object, default: int) -> int:
    """Return one validated non-negative retry counter."""
    try:
        retry_count = int(str(value).strip())
    except (TypeError, ValueError):
        retry_count = default
    return max(0, retry_count)


def _normalized_retry_delay_seconds(value: object, default: float) -> float:
    """Return one validated non-negative retry delay."""
    try:
        retry_delay_seconds = float(str(value).strip())
    except (TypeError, ValueError):
        retry_delay_seconds = default
    return max(0.0, retry_delay_seconds)


def _service_timeout_seconds(service: object) -> float:
    """Return the service-level Modbus request timeout default."""
    try:
        configured_timeout = getattr(service, "shelly_request_timeout_seconds")
    except AttributeError:
        return 2.0
    return _normalized_timeout_seconds(configured_timeout, 2.0)


def load_modbus_transport_settings(
    parser: configparser.ConfigParser, service: object
) -> ModbusTransportSettings:
    """Return normalized Modbus transport settings from backend config."""
    adapter = parser["Adapter"] if parser.has_section("Adapter") else parser["DEFAULT"]
    transport = parser["Transport"] if parser.has_section("Transport") else parser["DEFAULT"]
    transport_name = adapter.get("Transport", transport.get("Type"))
    transport_kind = _normalized_transport_kind(transport_name)
    default_timeout_seconds = _service_timeout_seconds(service)
    timeout_seconds = _normalized_timeout_seconds(
        transport.get("RequestTimeoutSeconds"),
        default_timeout_seconds,
    )
    unit_id = _normalized_unit_id(transport.get("UnitId", transport.get("SlaveId")))
    host = str(transport.get("Host", "")).strip() or None
    (
        port,
        device,
        baudrate,
        bytesize,
        parity,
        stopbits,
        serial_port_owner,
        serial_port_owner_stop_command,
        serial_port_owner_start_command,
        serial_retry_count,
        serial_retry_delay_seconds,
    ) = _transport_runtime_fields(transport_kind, transport, host)
    return ModbusTransportSettings(
        transport_kind=transport_kind,
        unit_id=unit_id,
        timeout_seconds=timeout_seconds,
        host=host,
        port=port,
        device=device,
        baudrate=baudrate,
        bytesize=bytesize,
        parity=parity,
        stopbits=stopbits,
        serial_port_owner=serial_port_owner,
        serial_port_owner_stop_command=serial_port_owner_stop_command,
        serial_port_owner_start_command=serial_port_owner_start_command,
        serial_retry_count=serial_retry_count,
        serial_retry_delay_seconds=serial_retry_delay_seconds,
    )


def _transport_runtime_fields(
    transport_kind: ModbusTransportKind,
    transport: configparser.SectionProxy,
    host: str | None,
) -> tuple[int | None, str | None, int, int, ModbusParity, int, SerialPortOwnerKind, str | None, str | None, int, float]:
    """Return transport-specific runtime fields for one normalized transport kind."""
    port, device, baudrate, bytesize, parity, stopbits = _default_modbus_serial_fields()
    serial_port_owner, serial_port_owner_stop_command, serial_port_owner_start_command = _default_port_owner_fields()
    serial_retry_count = 0
    serial_retry_delay_seconds = 0.2
    if transport_kind == "serial_rtu":
        (
            device,
            baudrate,
            bytesize,
            parity,
            stopbits,
            serial_port_owner,
            serial_port_owner_stop_command,
            serial_port_owner_start_command,
            serial_retry_count,
            serial_retry_delay_seconds,
        ) = _serial_transport_runtime_fields(transport)
        return (
            port,
            device,
            baudrate,
            bytesize,
            parity,
            stopbits,
            serial_port_owner,
            serial_port_owner_stop_command,
            serial_port_owner_start_command,
            serial_retry_count,
            serial_retry_delay_seconds,
        )
    port = _required_host_port(transport_kind, transport, host)
    return (
        port,
        device,
        baudrate,
        bytesize,
        parity,
        stopbits,
        serial_port_owner,
        serial_port_owner_stop_command,
        serial_port_owner_start_command,
        serial_retry_count,
        serial_retry_delay_seconds,
    )


def _default_modbus_serial_fields() -> tuple[int | None, str | None, int, int, ModbusParity, int]:
    """Return default serial-field values before transport-specific normalization."""
    return None, None, 9600, 8, "N", 1


def _default_port_owner_fields() -> tuple[SerialPortOwnerKind, str | None, str | None]:
    """Return default serial-port ownership settings."""
    return "none", None, None


def _serial_transport_fields(
    transport: configparser.SectionProxy,
) -> tuple[str, int, int, ModbusParity, int]:
    """Return normalized serial transport fields from config."""
    return (
        _normalized_device(transport.get("Device")),
        _normalized_baudrate(transport.get("Baudrate")),
        _normalized_bytesize(transport.get("Bytesize")),
        _normalized_parity(transport.get("Parity")),
        _normalized_stopbits(transport.get("StopBits")),
    )


def _port_owner_commands(transport: configparser.SectionProxy) -> tuple[str | None, str | None]:
    """Return normalized stop/start commands for serial port ownership."""
    return (
        _optional_transport_command(
            transport,
            "PortOwnerStopCommand",
            "/opt/victronenergy/serial-starter/stop-tty.sh",
        ),
        _optional_transport_command(
            transport,
            "PortOwnerStartCommand",
            "/opt/victronenergy/serial-starter/start-tty.sh",
        ),
    )


def _optional_transport_command(
    transport: configparser.SectionProxy,
    key: str,
    default: str,
) -> str | None:
    """Return one optional transport command string."""
    return str(transport.get(key, default)).strip() or None


def _serial_transport_runtime_fields(
    transport: configparser.SectionProxy,
) -> tuple[str, int, int, ModbusParity, int, SerialPortOwnerKind, str | None, str | None, int, float]:
    """Return normalized runtime fields for serial RTU transport settings."""
    device, baudrate, bytesize, parity, stopbits = _serial_transport_fields(transport)
    return (
        device,
        baudrate,
        bytesize,
        parity,
        stopbits,
        _normalized_serial_port_owner(transport.get("PortOwner")),
        *_port_owner_commands(transport),
        _normalized_retry_count(transport.get("RetryCount"), 1),
        _normalized_retry_delay_seconds(transport.get("RetryDelaySeconds"), 0.2),
    )


def _required_host_port(
    transport_kind: ModbusTransportKind,
    transport: configparser.SectionProxy,
    host: str | None,
) -> int:
    """Return the validated TCP/UDP port and require a host when needed."""
    if not host:
        raise ValueError(f"Modbus {transport_kind} transport requires Transport.Host")
    return _normalized_port(transport.get("Port"), 502)
