# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed contracts shared by the explicit bootstrap components."""

from __future__ import annotations

import configparser
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


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


def require_gobject_timers(gobject: object) -> GobjectTimersPort:
    """Return the GLib timer facade or fail at the bootstrap boundary."""
    if not isinstance(gobject, GobjectTimersPort):
        raise TypeError("bootstrap gobject module does not implement GobjectTimersPort")
    return gobject
