# SPDX-License-Identifier: GPL-3.0-or-later
"""Adapter-owned execution of generic Shelly configuration operations."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as xml_et
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, TypeGuard, TypeVar

import dbus

from venus_evcharger.core.shared import coerce_dbus_numeric
from venus_evcharger.dbus_adapter.contracts import CommandOutcome
from venus_evcharger.dbus_adapter.write.protocols import SemanticWriteAdapter
from venus_evcharger.ipc.command_types import CommandMapping
from venus_evcharger.ipc.generic_shelly_configuration import (
    DisableMatchingGenericShellyOnceOperation,
    parse_disable_matching_generic_shelly_once,
)
from venus_evcharger.ports.generic_shelly_configuration import normalize_mac_address

_DEFAULT_SERVICE = "com.victronenergy.shelly"
_SERVICE_CONFIG_KEY = "GenericShellyService"
_DEVICES_PATH = "/Devices"
_DBUS_TIMEOUT_SECONDS = 1.0
_PROGRESS_FIELDS = frozenset(("phase", "devices", "cursor", "matched_device"))

GenericShellyPhase = Literal["discover", "identify", "enabled", "disable"]
_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class _Progress:
    phase: GenericShellyPhase
    devices: tuple[str, ...]
    cursor: int
    matched_device: str


class GenericShellyConfigurationExecutor:
    """Advance one persistent configuration request by one DBus operation."""

    def __init__(self, adapter: SemanticWriteAdapter) -> None:
        self._adapter = adapter

    def process(self, command: CommandMapping, command_file: str) -> CommandOutcome:
        operation = _operation(command)
        progress = _progress(command)
        if operation is None or progress is None or not command_file:
            return "dropped"
        service = self._service()
        if not service:
            logging.warning("Dropping generic Shelly configuration because its adapter service is disabled")
            return "dropped"
        handlers: dict[GenericShellyPhase, Callable[[], CommandOutcome]] = {
            "discover": lambda: self._discover(command, command_file, service),
            "identify": lambda: self._identify(command, command_file, service, operation, progress),
            "enabled": lambda: self._read_enabled(command, command_file, service, operation, progress),
            "disable": lambda: self._disable(service, operation, progress),
        }
        return handlers[progress.phase]()

    def _discover(self, command: CommandMapping, command_file: str, service: str) -> CommandOutcome:
        devices = self._introspect_devices(service)
        if not devices:
            logging.info("No generic Shelly devices are currently registered")
            return "applied"
        return self._rewrite(command_file, command, phase="identify", devices=list(devices), cursor=0)

    def _identify(
        self,
        command: CommandMapping,
        command_file: str,
        service: str,
        operation: DisableMatchingGenericShellyOnceOperation,
        progress: _Progress,
    ) -> CommandOutcome:
        if progress.cursor >= len(progress.devices):
            logging.info("No generic Shelly device matched the configured identity")
            return "applied"
        device = progress.devices[progress.cursor]
        if self._device_matches(service, device, operation):
            return self._rewrite(command_file, command, phase="enabled", matched_device=device)
        return self._rewrite(command_file, command, cursor=progress.cursor + 1)

    def _device_matches(
        self,
        service: str,
        device: str,
        operation: DisableMatchingGenericShellyOnceOperation,
    ) -> bool:
        selector = operation.request.selector
        if selector.kind == "mac" and _serial_matches_mac(device, selector.value):
            return True
        identity_path = f"{_DEVICES_PATH}/{device}/{_identity_field(selector.kind)}"
        return _identity_matches(selector.kind, self._read_value(service, identity_path), selector.value)

    def _read_enabled(
        self,
        command: CommandMapping,
        command_file: str,
        service: str,
        operation: DisableMatchingGenericShellyOnceOperation,
        progress: _Progress,
    ) -> CommandOutcome:
        if not progress.matched_device:
            return "dropped"
        enabled = coerce_dbus_numeric(self._read_value(service, self._enabled_path(operation, progress)))
        if enabled == 0:
            logging.info("Matched generic Shelly channel is already disabled")
            return "applied"
        if enabled not in (0, 1):
            logging.warning("Dropping generic Shelly configuration because Enabled is not binary")
            return "dropped"
        return self._rewrite(command_file, command, phase="disable")

    def _disable(
        self,
        service: str,
        operation: DisableMatchingGenericShellyOnceOperation,
        progress: _Progress,
    ) -> CommandOutcome:
        if not progress.matched_device:
            return "dropped"
        path = self._enabled_path(operation, progress)
        self._write_value(service, path, 0)
        logging.info("Disabled matched generic Shelly channel through the gateway")
        return "applied"

    def _introspect_devices(self, service: str) -> tuple[str, ...]:
        xml_data = self._timed(
            "introspection",
            lambda: self._introspect_now(service, _DEVICES_PATH),
        )
        try:
            root = xml_et.fromstring(str(xml_data))
        except xml_et.ParseError:
            logging.warning("Generic Shelly discovery returned malformed introspection XML")
            return ()
        return tuple(
            name
            for node in root.findall("node")
            if (name := str(node.attrib.get("name") or "").strip())
        )

    def _introspect_now(self, service: str, path: str) -> object:
        obj = self._adapter.connection.get_object(service, path, introspect=False)
        interface = dbus.Interface(obj, "org.freedesktop.DBus.Introspectable")
        return interface.Introspect(timeout=_DBUS_TIMEOUT_SECONDS)

    def _read_value(self, service: str, path: str) -> object:
        return self._timed("read", lambda: self._read_value_now(service, path))

    def _read_value_now(self, service: str, path: str) -> object:
        obj = self._adapter.connection.get_object(service, path, introspect=False)
        interface = dbus.Interface(obj, "com.victronenergy.BusItem")
        return interface.GetValue(timeout=_DBUS_TIMEOUT_SECONDS)

    def _write_value(self, service: str, path: str, value: object) -> None:
        self._timed("write", lambda: self._write_value_now(service, path, value))

    def _write_value_now(self, service: str, path: str, value: object) -> None:
        obj = self._adapter.connection.get_object(service, path, introspect=False)
        interface = dbus.Interface(obj, "com.victronenergy.BusItem")
        interface.SetValue(value, timeout=_DBUS_TIMEOUT_SECONDS)

    def _timed(self, kind: str, operation: Callable[[], _T]) -> _T:
        return self._adapter.timed_dbus_operation(kind, operation)

    def _service(self) -> str:
        return str(self._adapter.config["DEFAULT"].get(_SERVICE_CONFIG_KEY, _DEFAULT_SERVICE)).strip()

    def _rewrite(self, command_file: str, command: CommandMapping, **changes: object) -> CommandOutcome:
        self._adapter.json_writer.write(command_file, {**dict(command), **changes})
        return "deferred"

    @staticmethod
    def _enabled_path(
        operation: DisableMatchingGenericShellyOnceOperation,
        progress: _Progress,
    ) -> str:
        return f"{_DEVICES_PATH}/{progress.matched_device}/{operation.request.channel}/Enabled"


def _operation(command: CommandMapping) -> DisableMatchingGenericShellyOnceOperation | None:
    envelope = {key: value for key, value in command.items() if key not in _PROGRESS_FIELDS}
    return parse_disable_matching_generic_shelly_once(envelope)


def _progress(command: CommandMapping) -> _Progress | None:
    phase = command.get("phase", "discover")
    devices = command.get("devices", ())
    cursor = command.get("cursor", 0)
    matched_device = command.get("matched_device", "")
    if not _is_phase(phase):
        return None
    normalized_devices = _normalized_devices(devices)
    if normalized_devices is None or not _valid_cursor(cursor):
        return None
    if not isinstance(matched_device, str):
        return None
    return _Progress(phase, normalized_devices, cursor, matched_device.strip())


def _normalized_devices(value: object) -> tuple[str, ...] | None:
    return tuple(value) if _is_device_sequence(value) else None


def _valid_cursor(value: object) -> TypeGuard[int]:
    return type(value) is int and value >= 0


def _identity_field(kind: str) -> str:
    return "Ip" if kind == "ip" else "Mac"


def _identity_matches(kind: str, candidate: object, expected: str) -> bool:
    if kind == "ip":
        return str(candidate or "").strip() == expected
    try:
        return normalize_mac_address(str(candidate or "")) == expected
    except ValueError:
        return False


def _serial_matches_mac(serial: str, expected: str) -> bool:
    try:
        return normalize_mac_address(serial) == expected
    except ValueError:
        return False


def _is_phase(value: object) -> TypeGuard[GenericShellyPhase]:
    return isinstance(value, str) and value in ("discover", "identify", "enabled", "disable")


def _is_device_sequence(value: object) -> TypeGuard[list[str] | tuple[str, ...]]:
    if not isinstance(value, (list, tuple)):
        return False
    return all(isinstance(item, str) and bool(item.strip()) for item in value)
