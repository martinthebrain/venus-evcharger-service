# SPDX-License-Identifier: GPL-3.0-or-later
"""Small controller ports that decouple controllers from the full service object."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, TypeAlias

from venus_evcharger.core.return_contracts import require_str_list, require_str_or_none
from venus_evcharger.dbus_introspection import owner_path_unusable, request_owner_introspection
from venus_evcharger.energy import EnergySourceDefinition


DbusRawValue: TypeAlias = str | float | int | None


class DbusInputRuntime(Protocol):
    """Runtime operations required by gateway-backed input readers."""

    def source_retry_ready(self, source_key: str, now: float) -> bool: ...
    def mark_recovery(self, source_key: str, message: str, *args: object) -> None: ...
    def mark_failure(self, source_key: str) -> None: ...
    def delay_source_retry(self, source_key: str, now: float) -> None: ...
    def warning_throttled(
        self,
        warning_key: str,
        interval_seconds: float,
        warning_message: str,
        *args: object,
    ) -> None: ...


class DbusInputService(Protocol):
    """Typed state boundary consumed by ``DbusInputPort`` and its readers."""

    @property
    def runtime(self) -> DbusInputRuntime: ...

    dbus_gateway_cache_path: str
    dbus_gateway_run_dir: str
    dbus_gateway_max_age_seconds: float
    auto_dbus_backoff_base_seconds: float
    auto_dbus_backoff_max_seconds: float
    auto_pv_scan_interval_seconds: float
    auto_pv_service: str
    auto_pv_service_prefix: str
    auto_pv_max_services: int
    auto_battery_scan_interval_seconds: float
    auto_battery_service: str
    auto_battery_service_prefix: str
    auto_battery_soc_path: str
    auto_battery_capacity_wh: float | None
    auto_battery_power_path: str
    auto_battery_ac_power_path: str
    auto_battery_pv_power_path: str
    auto_battery_grid_interaction_path: str
    auto_battery_operating_mode_path: str
    auto_energy_sources: tuple[EnergySourceDefinition, ...]
    auto_use_combined_battery_soc: bool
    auto_grid_service: str
    _last_dbus_ok_at: float
    _last_pv_missing_warning: object | None
    _dbus_list_backoff_until: float
    _dbus_list_failures: int
    _resolved_auto_pv_services: list[str]
    _auto_pv_last_scan: float
    _resolved_auto_battery_service: str | None
    _auto_battery_last_scan: float
    _resolved_auto_energy_services: dict[str, str]
    _auto_energy_last_scan: dict[str, float]
    _last_energy_learning_profiles: object
    _last_energy_cluster: dict[str, object]


class DbusInputReaderPort(Protocol):
    """Narrow port consumed by gateway-backed input components."""

    service: DbusInputService

    def source_retry_ready(self, source_key: str, now: float) -> bool: ...
    def mark_recovery(self, source_key: str, message: str, *args: object) -> None: ...
    def mark_failure(self, source_key: str) -> None: ...
    def delay_source_retry(self, source_key: str, now: float) -> None: ...
    def warning_throttled(
        self,
        warning_key: str,
        interval_seconds: float,
        warning_message: str,
        *args: object,
    ) -> None: ...
    def gateway_cache_path(self) -> str: ...
    def gateway_run_dir(self) -> str: ...
    def gateway_max_age_seconds(self) -> float: ...
    def energy_learning_profiles(self) -> object: ...
    def store_energy_learning_profiles(self, profiles: Mapping[str, object]) -> None: ...
    def store_energy_cluster(self, payload: Mapping[str, object]) -> None: ...
    def path_unusable(self, service_name: str, path: str) -> tuple[bool, str]: ...
    def request_introspection(
        self,
        service_name: str,
        path: str,
        *,
        priority: int,
        reason: str,
        source: str,
    ) -> bool: ...


class _DbusInputControllerMethods(Protocol):
    def get_dbus_value(self, service_name: str, path: str) -> DbusRawValue: ...
    def list_dbus_services(self) -> list[str]: ...
    def invalidate_auto_pv_services(self) -> None: ...
    def resolve_auto_pv_services(self) -> list[str]: ...
    def invalidate_auto_battery_service(self) -> None: ...
    def invalidate_energy_source_service(
        self,
        source_id: str,
        *,
        expected_service: str | None = None,
    ) -> bool: ...
    def resolve_auto_battery_service(self) -> str | None: ...


class DbusInputPort:
    """Expose the DBus-input surface needed by ``DbusInputController``."""

    _controller: _DbusInputControllerMethods | None

    def __init__(self, service: DbusInputService) -> None:
        self.service = service
        self._controller = None

    def bind_controller(self, controller: _DbusInputControllerMethods) -> None:
        self._controller = controller

    def _bound_controller(self) -> _DbusInputControllerMethods:
        controller = self._controller
        if controller is None:
            raise RuntimeError("DBus input controller is not bound")
        return controller

    def source_retry_ready(self, source_key: str, now: float) -> bool:
        return bool(self.service.runtime.source_retry_ready(source_key, now))

    def mark_recovery(self, source_key: str, message: str, *args: object) -> None:
        self.service.runtime.mark_recovery(source_key, message, *args)

    def mark_failure(self, source_key: str) -> None:
        self.service.runtime.mark_failure(source_key)

    def delay_source_retry(self, source_key: str, now: float) -> None:
        self.service.runtime.delay_source_retry(source_key, now)

    def warning_throttled(
        self,
        warning_key: str,
        interval_seconds: float,
        warning_message: str,
        *args: object,
    ) -> None:
        self.service.runtime.warning_throttled(warning_key, interval_seconds, warning_message, *args)

    def gateway_cache_path(self) -> str:
        value = self.service.dbus_gateway_cache_path
        return str(value).strip() if value else ""

    def gateway_run_dir(self) -> str:
        value = self.service.dbus_gateway_run_dir
        return str(value).strip() if value else ""

    def gateway_max_age_seconds(self) -> float:
        value = self.service.dbus_gateway_max_age_seconds
        return 10.0 if not value else max(0.0, float(value))

    def energy_learning_profiles(self) -> object:
        return self.service._last_energy_learning_profiles

    def store_energy_learning_profiles(self, profiles: Mapping[str, object]) -> None:
        self.service._last_energy_learning_profiles = dict(profiles)

    def store_energy_cluster(self, payload: Mapping[str, object]) -> None:
        self.service._last_energy_cluster = dict(payload)

    def path_unusable(self, service_name: str, path: str) -> tuple[bool, str]:
        return owner_path_unusable(self.service, service_name, path)

    def request_introspection(
        self,
        service_name: str,
        path: str,
        *,
        priority: int,
        reason: str,
        source: str,
    ) -> bool:
        return request_owner_introspection(
            self.service,
            service_name,
            path,
            priority=priority,
            reason=reason,
            source=source,
        )

    def get_dbus_value(self, service_name: str, path: str) -> DbusRawValue:
        return self._bound_controller().get_dbus_value(service_name, path)

    def list_dbus_services(self) -> list[str]:
        services = self._bound_controller().list_dbus_services()
        return require_str_list(services, "list_dbus_services")

    def invalidate_auto_pv_services(self) -> None:
        self._bound_controller().invalidate_auto_pv_services()

    def resolve_auto_pv_services(self) -> list[str]:
        services = self._bound_controller().resolve_auto_pv_services()
        return require_str_list(services, "resolve_auto_pv_services")

    def invalidate_auto_battery_service(self) -> None:
        self._bound_controller().invalidate_auto_battery_service()

    def invalidate_energy_source_service(
        self,
        source_id: str,
        *,
        expected_service: str | None = None,
    ) -> bool:
        return self._bound_controller().invalidate_energy_source_service(
            source_id,
            expected_service=expected_service,
        )

    def resolve_auto_battery_service(self) -> str | None:
        service = self._bound_controller().resolve_auto_battery_service()
        return require_str_or_none(service, "resolve_auto_battery_service")
