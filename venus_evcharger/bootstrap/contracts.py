# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed contracts shared by the explicit bootstrap components."""

from __future__ import annotations

import configparser
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


Formatter = Callable[[object, object], str] | None
MonthWindow = Callable[[configparser.ConfigParser, int, str, str], object]


@dataclass(frozen=True)
class BootstrapDependencies:
    """Pure functions and platform objects required during service bootstrap."""

    normalize_phase: Callable[[object], str]
    normalize_mode: Callable[[object], int]
    mode_uses_auto_logic: Callable[[object], bool]
    month_window: MonthWindow
    read_version: Callable[[str], str]
    gobject: object
    script_path: str
    formatters: Mapping[str, Formatter]


@runtime_checkable
class ConfigStatePort(Protocol):
    """State operations required by configuration bootstrap."""

    def load_config(self) -> configparser.ConfigParser: ...

    def validate_runtime_config(self) -> None: ...


@runtime_checkable
class RuntimeStatePort(Protocol):
    """State operation required while restoring volatile runtime state."""

    def load_runtime_state(self) -> None: ...


@runtime_checkable
class ControllerOwnerPort(Protocol):
    """Controller-owner operation required by runtime initialization."""

    def prepare_runtime_state(self) -> object: ...

    def initialize_runtime(self) -> object: ...


@runtime_checkable
class DbusServicePort(Protocol):
    """Gateway proxy operations required by DBus path registration."""

    def add_path(self, path: str, value: object, **kwargs: object) -> None: ...

    def register(self) -> None: ...


@runtime_checkable
class DbusWriteHandlerPort(Protocol):
    """Writable-path callback exposed by the Auto service role."""

    def handle_dbus_write(self, path: str, value: object) -> bool: ...


@runtime_checkable
class GobjectTimersPort(Protocol):
    """GLib timer operation required by bootstrap runtime startup."""

    def timeout_add(self, interval: int, callback: object) -> object: ...


def require_config_state(service: object) -> ConfigStatePort:
    """Return the configured state facade or fail at the bootstrap boundary."""
    state = getattr(service, "state", None)
    if not isinstance(state, ConfigStatePort):
        raise TypeError("bootstrap service state does not implement ConfigStatePort")
    return state


def require_runtime_state(service: object) -> RuntimeStatePort:
    """Return the runtime-state facade or fail at the bootstrap boundary."""
    state = getattr(service, "state", None)
    if not isinstance(state, RuntimeStatePort):
        raise TypeError("bootstrap service state does not implement RuntimeStatePort")
    return state


def require_controller_owner(service: object) -> ControllerOwnerPort:
    """Return the controller owner or fail at the bootstrap boundary."""
    owner = getattr(service, "controllers", None)
    if not isinstance(owner, ControllerOwnerPort):
        raise TypeError("bootstrap service does not expose ControllerOwnerPort")
    return owner


def require_dbus_service(service: object) -> DbusServicePort:
    """Return the gateway DBus proxy or fail at the bootstrap boundary."""
    dbus_service = getattr(service, "_dbusservice", None)
    if not isinstance(dbus_service, DbusServicePort):
        raise TypeError("bootstrap service does not expose DbusServicePort")
    return dbus_service


def require_write_handler(service: object) -> DbusWriteHandlerPort:
    """Return the writable-path handler or fail at the bootstrap boundary."""
    handler = getattr(service, "auto", None)
    if not isinstance(handler, DbusWriteHandlerPort):
        raise TypeError("bootstrap service does not expose DbusWriteHandlerPort")
    return handler


def require_gobject_timers(gobject: object) -> GobjectTimersPort:
    """Return the GLib timer facade or fail at the bootstrap boundary."""
    if not isinstance(gobject, GobjectTimersPort):
        raise TypeError("bootstrap gobject module does not implement GobjectTimersPort")
    return gobject
