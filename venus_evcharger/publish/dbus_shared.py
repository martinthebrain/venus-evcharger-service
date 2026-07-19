# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed contracts shared by the DBus publish components."""

from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass
from typing import Protocol, TypeAlias, TypeGuard

PublishValue: TypeAlias = object
PublishStateEntry: TypeAlias = dict[str, PublishValue]
PhaseMeasurement: TypeAlias = dict[str, float]
PhaseData: TypeAlias = dict[str, PhaseMeasurement]
PublishServiceValueSnapshot: TypeAlias = tuple[bool, PublishValue]
AgeTimestamp: TypeAlias = float | int | None
AgeSeconds: TypeAlias = Callable[[AgeTimestamp, AgeTimestamp], int | float]


class DbusValueStore(Protocol):  # pragma: no cover - static contract
    """Minimal mapping surface exposed by the gateway-backed DBus service."""

    def __getitem__(self, path: str, /) -> PublishValue: ...

    def __setitem__(self, path: str, value: PublishValue, /) -> None: ...

    def __delitem__(self, path: str, /) -> None: ...


class PublishRuntimePort(Protocol):  # pragma: no cover - static contract
    """Runtime operations used by the DBus publisher."""

    def assert_dbus_mainloop_thread(self, operation: str = "dbus access") -> None: ...

    def dbus_publish_direct_allowed(self) -> bool: ...

    def enqueue_dbus_publish_fields(self, fields: list[tuple[str, object]], current: float) -> bool: ...

    def enqueue_dbus_publish_values(self, values: list[tuple[str, object]], current: float) -> bool: ...

    def enqueue_dbus_update_index_bump(self, current: float) -> None: ...

    def mark_failure(self, source_key: str) -> None: ...

    def source_retry_remaining(self, source_key: str, now: float | None = None) -> int: ...

    def update_is_stale(self, now: float | None = None) -> bool: ...

    def warning_throttled(
        self,
        key: str,
        interval_seconds: float,
        message: str,
        *args: object,
        **kwargs: object,
    ) -> None: ...


class PublishServicePort(Protocol):  # pragma: no cover - static contract
    """Mandatory runtime state consumed by the publish boundary."""

    _dbusservice: DbusValueStore
    _dbus_publish_state: MutableMapping[str, PublishStateEntry]
    _dbus_live_publish_interval_seconds: float
    _dbus_slow_publish_interval_seconds: float
    _last_health_code: int
    _last_health_reason: str
    _last_successful_update_at: float | None
    _last_pv_at: float | None
    _last_battery_soc_at: float | None
    _last_grid_at: float | None
    _last_dbus_ok_at: float | None
    _recovery_attempts: int
    last_status: int
    started_at: float
    virtual_set_current: float
    runtime: PublishRuntimePort


@dataclass(frozen=True)
class DbusPublishContext:
    """Dependencies shared by all publish components."""

    service: PublishServicePort
    age_seconds: AgeSeconds

    def age(self, timestamp: object, now: float) -> float:
        """Normalize a dynamic timestamp before invoking the service age policy."""
        normalized = timestamp if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool) else None
        return float(self.age_seconds(normalized, now))


def diagnostic_text(value: object) -> str:
    """Normalize an optional runtime value for outward diagnostic text."""
    return "" if value is None else str(value).strip()


def runtime_text_attribute(source: object, attribute_name: str, fallback: str = "") -> str:
    """Read one dynamic text attribute while preserving its outward representation."""
    if not hasattr(source, attribute_name):
        return fallback
    return str(getattr(source, attribute_name) or fallback)


def is_object_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    """Narrow one dynamic boundary value to a fully typed mapping."""
    return isinstance(value, Mapping)


@dataclass(frozen=True)
class LearnedDisplayCurrentInputs:
    """Stable learned charging-power inputs used for SetCurrent display derivation."""

    power_w: float
    phase_voltage_v: float
    phase_count: float
