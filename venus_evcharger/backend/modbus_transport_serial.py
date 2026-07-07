# SPDX-License-Identifier: GPL-3.0-or-later
"""Serial RTU Modbus transport implementation and Venus tty ownership helpers."""

from __future__ import annotations

import atexit
import errno
import os
import select
import subprocess
import termios
import time

from .modbus_transport_errors import (
    ModbusPortBusyError,
    ModbusPortOwnershipError,
    ModbusResponseError,
    ModbusSlaveOfflineError,
    ModbusTimeoutError,
    ModbusTransportError,
)
from .modbus_transport_types import ModbusRequest, ModbusTransportSettings


class _VenusSerialPortOwner:
    """Own one Venus serial-starter managed tty for the lifetime of this process."""

    def __init__(self, device: str, stop_command: str, start_command: str | None) -> None:
        self.device = device
        self.stop_command = stop_command
        self.start_command = start_command
        self._owned = False
        self._release_registered = False

    def ensure_owned(self) -> None:
        """Stop Venus serial-starter once so the current process can use the tty."""
        if self._owned:
            return
        self._run_command(self.stop_command)
        self._owned = True
        if self.start_command is not None and not self._release_registered:
            atexit.register(self.release)
            self._release_registered = True

    def recover(self) -> None:
        """Re-run the stop command after a failed serial exchange."""
        if self._owned:
            self._run_command(self.stop_command)

    def release(self) -> None:
        """Return the tty to Venus serial-starter when possible."""
        if not self._owned or self.start_command is None:
            return
        try:
            self._run_command(self.start_command)
        except ModbusPortOwnershipError:
            return
        self._owned = False

    def _run_command(self, command: str) -> None:
        """Run one stop/start helper with the current tty path."""
        try:
            result = self._command_result(command)
        except FileNotFoundError as error:
            raise ModbusPortOwnershipError(
                f"Venus serial ownership helper '{command}' is unavailable for {self.device}"
            ) from error
        if result.returncode == 0:
            return
        detail = self._command_detail(result)
        suffix = f": {detail}" if detail else ""
        raise ModbusPortOwnershipError(
            f"Venus serial ownership helper '{command}' failed for {self.device}{suffix}"
        )

    def _command_result(self, command: str) -> subprocess.CompletedProcess[str]:
        """Return the subprocess result for one tty ownership helper."""
        return subprocess.run([command, self.device], check=False, capture_output=True, text=True)

    @staticmethod
    def _command_detail(result: subprocess.CompletedProcess[str]) -> str:
        """Return the best available stderr/stdout detail for one helper result."""
        return (result.stderr or result.stdout or "").strip()


def _modbus_crc(frame: bytes) -> int:
    """Return one Modbus RTU CRC16."""
    crc = 0xFFFF
    for byte in frame:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def _crc_frame(frame: bytes) -> bytes:
    """Append Modbus RTU CRC16 to one frame."""
    crc = _modbus_crc(frame)
    return frame + bytes((crc & 0xFF, (crc >> 8) & 0xFF))


def _serial_baudrate_constant(baudrate: int) -> int:
    """Return the termios baudrate constant for one supported speed."""
    constant_name = f"B{baudrate}"
    if not hasattr(termios, constant_name):
        raise ValueError(f"Unsupported Modbus serial baudrate '{baudrate}'")
    return int(getattr(termios, constant_name))


def _configured_serial_attrs(fd: int, settings: ModbusTransportSettings) -> list[int | list[bytes | int]]:
    """Return one configured termios attr list for the serial Modbus port."""
    attrs = termios.tcgetattr(fd)
    attrs[0] = 0
    attrs[1] = 0
    attrs[2] = termios.CREAD | termios.CLOCAL
    attrs[3] = 0
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 0
    baud = _serial_baudrate_constant(settings.baudrate)
    attrs[4] = baud
    attrs[5] = baud
    attrs[2] |= {5: termios.CS5, 6: termios.CS6, 7: termios.CS7, 8: termios.CS8}[settings.bytesize]
    if settings.parity == "E":
        attrs[2] |= termios.PARENB
    elif settings.parity == "O":
        attrs[2] |= termios.PARENB | termios.PARODD
    if settings.stopbits == 2:
        attrs[2] |= termios.CSTOPB
    return attrs


def _serial_port_owner(settings: ModbusTransportSettings) -> _VenusSerialPortOwner | None:
    """Return one optional serial port-owner helper for the given settings."""
    if settings.transport_kind != "serial_rtu" or settings.serial_port_owner == "none":
        return None
    if settings.device is None:
        return None
    stop_command = settings.serial_port_owner_stop_command
    if stop_command is None:
        raise ValueError("Modbus serial port ownership requires Transport.PortOwnerStopCommand")
    return _VenusSerialPortOwner(settings.device, stop_command, settings.serial_port_owner_start_command)


def _expected_rtu_response_length(function_code: int, header: bytes) -> int:
    """Return the full RTU response length from the first header bytes."""
    if len(header) < 3:
        raise ValueError("Incomplete Modbus RTU header")
    response_function = header[1]
    if response_function & 0x80:
        return 5
    if function_code in {0x01, 0x02, 0x03, 0x04}:
        return 3 + int(header[2]) + 2
    if function_code in {0x05, 0x06, 0x0F, 0x10}:
        return 8
    raise ValueError(f"Unsupported Modbus RTU function code 0x{function_code:02x}")


def _positive_serial_write_count(written: int) -> int:
    """Return a positive serial write count or raise a stable transport error."""
    if written <= 0:
        raise ModbusPortBusyError("Failed to write full Modbus RTU request")
    return written


class ModbusSerialRtuTransport:
    """Simple one-shot Modbus RTU transport over a serial character device."""

    def __init__(self, settings: ModbusTransportSettings) -> None:
        self.settings = settings
        self._port_owner = _serial_port_owner(settings)

    def exchange(self, request: ModbusRequest, *, timeout_seconds: float) -> bytes:
        """Send one Modbus RTU request and return the response PDU."""
        attempts = self._serial_attempt_count()
        last_error = None
        for attempt_index in range(attempts):  # pragma: no branch
            exchange_result, last_error = self._serial_attempt_with_recovery(
                request,
                timeout_seconds,
                attempt_index,
                attempts,
            )
            if exchange_result is not None:
                return exchange_result
        assert last_error is not None
        raise self._final_serial_exchange_error(request, last_error)

    def _serial_attempt_with_recovery(
        self,
        request: ModbusRequest,
        timeout_seconds: float,
        attempt_index: int,
        attempts: int,
    ) -> tuple[bytes | None, BaseException | None]:
        """Run one serial exchange attempt and recover before the next retry."""
        self._ensure_port_owned()
        exchange_result, last_error = self._exchange_attempt(request, timeout_seconds)
        if exchange_result is not None or self._serial_retry_exhausted(attempt_index, attempts):
            return exchange_result, last_error
        assert last_error is not None
        self._recover_after_failure(last_error)
        return None, last_error

    def _exchange_attempt(self, request: ModbusRequest, timeout_seconds: float) -> tuple[bytes | None, BaseException | None]:
        """Return one exchange result or the normalized recoverable error."""
        try:
            return self._exchange_once(request, timeout_seconds), None
        except (ModbusPortBusyError, ModbusPortOwnershipError):
            raise
        except (ModbusTimeoutError, ModbusResponseError) as error:
            return None, error
        except OSError as error:
            return None, self._normalized_serial_os_error(error)

    def _serial_attempt_count(self) -> int:
        """Return the number of serial exchange attempts including retries."""
        return max(1, self.settings.serial_retry_count + 1)

    @staticmethod
    def _serial_retry_exhausted(attempt_index: int, attempts: int) -> bool:
        """Return whether the current serial exchange attempt was the last one."""
        return attempt_index >= (attempts - 1)

    def _final_serial_exchange_error(self, request: ModbusRequest, last_error: BaseException) -> BaseException:
        """Return the final transport error after all serial retries are exhausted."""
        if isinstance(last_error, ModbusTimeoutError):
            return ModbusSlaveOfflineError(
                f"Modbus slave {request.unit_id} on {self.settings.device} did not respond"
            )
        return last_error

    def _exchange_once(self, request: ModbusRequest, timeout_seconds: float) -> bytes:
        """Perform one single Modbus RTU exchange without retry handling."""
        assert self.settings.device is not None
        frame = _crc_frame(bytes((request.unit_id, request.function_code)) + request.payload)
        fd = os.open(self.settings.device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            termios.tcsetattr(fd, termios.TCSANOW, _configured_serial_attrs(fd, self.settings))
            termios.tcflush(fd, termios.TCIOFLUSH)
            self._write_all(fd, frame)
            header = self._read_exact(fd, 3, timeout_seconds)
            total_length = _expected_rtu_response_length(request.function_code, header)
            response = header + self._read_exact(fd, total_length - len(header), timeout_seconds)
        finally:
            os.close(fd)
        if len(response) < 5:
            raise ModbusTimeoutError("Incomplete Modbus RTU response")
        payload = response[:-2]
        expected_crc = _modbus_crc(payload)
        received_crc = response[-2] | (response[-1] << 8)
        if received_crc != expected_crc:
            raise ModbusResponseError("Invalid Modbus RTU CRC")
        return payload[1:]

    def _ensure_port_owned(self) -> None:
        """Claim the tty from Venus serial-starter when configured."""
        if self._port_owner is not None:
            self._port_owner.ensure_owned()

    def _recover_after_failure(self, error: BaseException) -> None:
        """Sleep briefly and refresh Venus tty ownership before one retry."""
        if self._port_owner is not None:
            self._port_owner.recover()
        if self.settings.serial_retry_delay_seconds > 0.0:
            time.sleep(self.settings.serial_retry_delay_seconds)

    @staticmethod
    def _write_all(fd: int, frame: bytes) -> None:
        """Write one full Modbus RTU frame to the serial file descriptor."""
        total_written = 0
        while total_written < len(frame):
            written = _positive_serial_write_count(os.write(fd, frame[total_written:]))
            total_written += written

    @staticmethod
    def _normalized_serial_os_error(error: OSError) -> BaseException:
        """Return one stable Modbus transport error for serial OS-layer failures."""
        if error.errno in {errno.EBUSY, errno.EACCES, errno.EPERM}:
            return ModbusPortBusyError(str(error))
        return ModbusTransportError(str(error))

    @staticmethod
    def _read_exact(fd: int, size: int, timeout_seconds: float) -> bytes:
        """Read exactly size bytes from one serial file descriptor."""
        chunks = bytearray()
        deadline = time.monotonic() + timeout_seconds
        while len(chunks) < size:
            remaining = max(0.0, deadline - time.monotonic())
            readable, _, _ = select.select([fd], [], [], remaining)
            if not readable:
                raise ModbusTimeoutError("Timed out waiting for Modbus RTU response")
            chunk = os.read(fd, size - len(chunks))
            if not chunk:
                raise ModbusTimeoutError("Modbus RTU transport closed before full response was received")
            chunks.extend(chunk)
        return bytes(chunks)
