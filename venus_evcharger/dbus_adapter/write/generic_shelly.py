# SPDX-License-Identifier: GPL-3.0-or-later
"""Adapter-owned execution of generic Shelly configuration operations."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, TypeGuard

from venus_evcharger.core.shared import coerce_dbus_numeric
from venus_evcharger.dbus_adapter.async_broker import DbusMethodCall, dbus_call_operation
from venus_evcharger.dbus_adapter.contracts import (
    CommandCompletion,
    CommandExecution,
    CommandOutcome,
)
from venus_evcharger.dbus_adapter.introspection_xml import parse_bounded_introspection_xml
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


@dataclass(frozen=True, slots=True)
class _Progress:
    phase: GenericShellyPhase
    devices: tuple[str, ...]
    cursor: int
    matched_device: str


@dataclass(frozen=True, slots=True)
class _ExecutionContext:
    command: CommandMapping
    command_file: str
    service: str
    operation: DisableMatchingGenericShellyOnceOperation
    progress: _Progress
    completion: CommandCompletion


class GenericShellyConfigurationExecutor:
    """Advance one persistent configuration request by one DBus operation."""

    def __init__(self, adapter: SemanticWriteAdapter) -> None:
        self._adapter = adapter

    def schedule(
        self,
        command: CommandMapping,
        command_file: str,
        completion: CommandCompletion,
    ) -> CommandExecution:
        operation = _operation(command)
        progress = _progress(command)
        if operation is None or progress is None or not command_file:
            return CommandExecution.immediate("dropped")
        service = self._service()
        if not service:
            logging.warning("Dropping generic Shelly configuration because its adapter service is disabled")
            return CommandExecution.immediate("dropped")
        context = _ExecutionContext(
            command=command,
            command_file=command_file,
            service=service,
            operation=operation,
            progress=progress,
            completion=completion,
        )
        handlers: dict[GenericShellyPhase, Callable[[], CommandExecution]] = {
            "discover": lambda: self._discover(context),
            "identify": lambda: self._identify(context),
            "enabled": lambda: self._read_enabled(context),
            "disable": lambda: self._disable(context),
        }
        return handlers[progress.phase]()

    def _discover(
        self,
        context: _ExecutionContext,
    ) -> CommandExecution:
        return self._schedule_method(
            DbusMethodCall(
                service=context.service,
                path=_DEVICES_PATH,
                interface="org.freedesktop.DBus.Introspectable",
                method_name="Introspect",
                signature="",
                rate_kind="introspection",
                metric_kind="introspection",
                source=f"{context.service}{_DEVICES_PATH}",
                priority=self._priority(context),
                timeout_seconds=_DBUS_TIMEOUT_SECONDS,
                owner_path=context.command_file,
            ),
            on_success=lambda xml_data: context.completion(self._discover_outcome(context, xml_data)),
            on_error=lambda _error: context.completion("deferred"),
        )

    def _discover_outcome(
        self,
        context: _ExecutionContext,
        xml_data: object,
    ) -> CommandOutcome:
        devices = self._devices_from_xml(xml_data)
        if not devices:
            logging.info("No generic Shelly devices are currently registered")
            return "applied"
        return self._rewrite(
            context.command_file,
            context.command,
            phase="identify",
            devices=list(devices),
            cursor=0,
        )

    def _identify(
        self,
        context: _ExecutionContext,
    ) -> CommandExecution:
        progress = context.progress
        if progress.cursor >= len(progress.devices):
            logging.info("No generic Shelly device matched the configured identity")
            return CommandExecution.immediate("applied")
        device = progress.devices[progress.cursor]
        selector = context.operation.request.selector
        if selector.kind == "mac" and _serial_matches_mac(device, selector.value):
            return CommandExecution.immediate(
                self._rewrite(
                    context.command_file,
                    context.command,
                    phase="enabled",
                    matched_device=device,
                )
            )
        identity_path = f"{_DEVICES_PATH}/{device}/{_identity_field(selector.kind)}"
        return self._schedule_method(
            DbusMethodCall(
                service=context.service,
                path=identity_path,
                interface="com.victronenergy.BusItem",
                method_name="GetValue",
                signature="",
                rate_kind="read",
                metric_kind="read",
                source=f"{context.service}{identity_path}",
                priority=self._priority(context),
                timeout_seconds=_DBUS_TIMEOUT_SECONDS,
                owner_path=context.command_file,
            ),
            on_success=lambda value: context.completion(self._identity_outcome(context, device, value)),
            on_error=lambda _error: context.completion("deferred"),
        )

    def _identity_outcome(
        self,
        context: _ExecutionContext,
        device: str,
        value: object,
    ) -> CommandOutcome:
        selector = context.operation.request.selector
        if _identity_matches(selector.kind, value, selector.value):
            return self._rewrite(
                context.command_file,
                context.command,
                phase="enabled",
                matched_device=device,
            )
        return self._rewrite(
            context.command_file,
            context.command,
            cursor=context.progress.cursor + 1,
        )

    def _read_enabled(
        self,
        context: _ExecutionContext,
    ) -> CommandExecution:
        if not context.progress.matched_device:
            return CommandExecution.immediate("dropped")
        path = self._enabled_path(context.operation, context.progress)
        return self._schedule_method(
            DbusMethodCall(
                service=context.service,
                path=path,
                interface="com.victronenergy.BusItem",
                method_name="GetValue",
                signature="",
                rate_kind="read",
                metric_kind="read",
                source=f"{context.service}{path}",
                priority=self._priority(context),
                timeout_seconds=_DBUS_TIMEOUT_SECONDS,
                owner_path=context.command_file,
            ),
            on_success=lambda value: context.completion(self._enabled_outcome(context, value)),
            on_error=lambda _error: context.completion("deferred"),
        )

    def _enabled_outcome(
        self,
        context: _ExecutionContext,
        value: object,
    ) -> CommandOutcome:
        enabled = coerce_dbus_numeric(value)
        if enabled == 0:
            logging.info("Matched generic Shelly channel is already disabled")
            return "applied"
        if enabled != 1:
            logging.warning("Dropping generic Shelly configuration because Enabled is not binary")
            return "dropped"
        return self._rewrite(context.command_file, context.command, phase="disable")

    def _disable(
        self,
        context: _ExecutionContext,
    ) -> CommandExecution:
        if not context.progress.matched_device:
            return CommandExecution.immediate("dropped")
        path = self._enabled_path(context.operation, context.progress)
        return self._schedule_method(
            DbusMethodCall(
                service=context.service,
                path=path,
                interface="com.victronenergy.BusItem",
                method_name="SetValue",
                signature="v",
                rate_kind="write",
                metric_kind="write",
                source=f"{context.service}{path}",
                priority=self._priority(context),
                timeout_seconds=_DBUS_TIMEOUT_SECONDS,
                args=(0,),
                owner_path=context.command_file,
            ),
            on_success=lambda _value: context.completion(self._disabled_outcome()),
            on_error=lambda _error: context.completion("deferred"),
        )

    @staticmethod
    def _disabled_outcome() -> CommandOutcome:
        logging.info("Disabled matched generic Shelly channel through the gateway")
        return "applied"

    @staticmethod
    def _devices_from_xml(xml_data: object) -> tuple[str, ...]:
        root = parse_bounded_introspection_xml(xml_data)
        if root is None:
            logging.warning("Generic Shelly discovery returned rejected introspection XML")
            return ()
        return tuple(name for node in root.findall("node") if (name := str(node.attrib.get("name") or "").strip()))

    def _schedule_method(
        self,
        call: DbusMethodCall,
        *,
        on_success: Callable[[object], None],
        on_error: Callable[[BaseException], None],
    ) -> CommandExecution:
        self._adapter.operation_broker.submit(
            dbus_call_operation(
                self._adapter.connection,
                call,
                on_success=on_success,
                on_error=on_error,
            )
        )
        return CommandExecution.pending()

    @staticmethod
    def _priority(context: _ExecutionContext) -> str:
        return str(context.command["priority"])

    def _service(self) -> str:
        return str(self._adapter.config["DEFAULT"].get(_SERVICE_CONFIG_KEY, _DEFAULT_SERVICE)).strip()

    def _rewrite(self, command_file: str, command: CommandMapping, **changes: object) -> CommandOutcome:
        replaced = self._adapter.commands.replace_if_current(
            command_file,
            command,
            {**dict(command), **changes},
        )
        return "deferred" if replaced else "dropped"

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
    return tuple(item.strip() for item in value) if _is_device_sequence(value) else None


def _valid_cursor(value: object) -> TypeGuard[int]:
    return type(value) is int and value >= 0


def _identity_field(kind: str) -> str:
    return "Ip" if kind == "ip" else "Mac"


def _identity_matches(kind: str, candidate: object, expected: str) -> bool:
    if kind == "ip":
        return str(candidate).strip() == expected
    try:
        return normalize_mac_address(str(candidate)) == expected
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
