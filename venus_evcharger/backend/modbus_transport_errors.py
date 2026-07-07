# SPDX-License-Identifier: GPL-3.0-or-later
"""Stable Modbus transport error types shared by concrete transports."""

from __future__ import annotations


class ModbusTransportError(RuntimeError):
    """Base error for Modbus transport failures."""


class ModbusPortBusyError(ModbusTransportError):
    """Raised when the serial Modbus port cannot be accessed exclusively enough."""


class ModbusPortOwnershipError(ModbusTransportError):
    """Raised when Venus serial-starter ownership handoff fails."""


class ModbusTimeoutError(ModbusTransportError):
    """Raised when a Modbus request times out before a full response arrives."""


class ModbusResponseError(ModbusTransportError):
    """Raised when a Modbus response is malformed or otherwise unusable."""


class ModbusSlaveOfflineError(ModbusTimeoutError):
    """Raised when repeated Modbus timeouts indicate an offline slave."""
