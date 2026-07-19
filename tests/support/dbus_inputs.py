# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed scenario fakes for gateway-backed input component tests."""

from __future__ import annotations

from dataclasses import dataclass, field

from venus_evcharger.dbus_gateway import GatewayReadKey
from venus_evcharger.energy import EnergySourceDefinition
from venus_evcharger.ports.dbus import DbusRawValue


@dataclass
class DbusRuntimeFake:
    ready: dict[str, bool] = field(default_factory=lambda: dict[str, bool]())
    recoveries: list[tuple[str, str, tuple[object, ...]]] = field(
        default_factory=lambda: list[tuple[str, str, tuple[object, ...]]]()
    )
    failures: list[str] = field(default_factory=lambda: list[str]())
    delayed: list[tuple[str, float]] = field(
        default_factory=lambda: list[tuple[str, float]]()
    )
    warnings: list[tuple[str, float, str, tuple[object, ...]]] = field(
        default_factory=lambda: list[tuple[str, float, str, tuple[object, ...]]]()
    )

    def source_retry_ready(self, source_key: str, now: float) -> bool:
        del now
        return self.ready.get(source_key, True)

    def mark_recovery(self, source_key: str, message: str, *args: object) -> None:
        self.recoveries.append((source_key, message, args))

    def mark_failure(self, source_key: str) -> None:
        self.failures.append(source_key)

    def delay_source_retry(self, source_key: str, now: float) -> None:
        self.delayed.append((source_key, now))

    def warning_throttled(
        self,
        warning_key: str,
        interval_seconds: float,
        warning_message: str,
        *args: object,
    ) -> None:
        self.warnings.append((warning_key, interval_seconds, warning_message, args))


@dataclass
class DbusInputServiceFake:
    runtime: DbusRuntimeFake = field(default_factory=DbusRuntimeFake)
    dbus_gateway_cache_path: str = ""
    dbus_gateway_run_dir: str = ""
    dbus_gateway_max_age_seconds: float = 10.0
    auto_dbus_backoff_base_seconds: float = 5.0
    auto_dbus_backoff_max_seconds: float = 60.0
    auto_pv_scan_interval_seconds: float = 30.0
    auto_pv_service: str = ""
    auto_pv_service_prefix: str = "com.victronenergy.pvinverter."
    auto_pv_max_services: int = 8
    auto_battery_scan_interval_seconds: float = 30.0
    auto_battery_service: str = ""
    auto_battery_service_prefix: str = "com.victronenergy.battery."
    auto_battery_soc_path: str = "/Soc"
    auto_battery_capacity_wh: float | None = None
    auto_battery_power_path: str = ""
    auto_battery_ac_power_path: str = ""
    auto_battery_pv_power_path: str = ""
    auto_battery_grid_interaction_path: str = ""
    auto_battery_operating_mode_path: str = ""
    auto_energy_sources: tuple[EnergySourceDefinition, ...] = ()
    auto_use_combined_battery_soc: bool = True
    auto_grid_service: str = "com.victronenergy.system"
    _last_dbus_ok_at: float = 0.0
    _last_pv_missing_warning: object | None = None
    _dbus_list_backoff_until: float = 0.0
    _dbus_list_failures: int = 0
    _resolved_auto_pv_services: list[str] = field(default_factory=lambda: list[str]())
    _auto_pv_last_scan: float = 0.0
    _resolved_auto_battery_service: str | None = None
    _auto_battery_last_scan: float = 0.0
    _resolved_auto_energy_services: dict[str, str] = field(
        default_factory=lambda: dict[str, str]()
    )
    _auto_energy_last_scan: dict[str, float] = field(
        default_factory=lambda: dict[str, float]()
    )
    _last_energy_learning_profiles: object = None
    _last_energy_cluster: dict[str, object] = field(
        default_factory=lambda: dict[str, object]()
    )
    dbus_introspection_snapshot_path: str = ""
    dbus_introspection_max_age_seconds: float = 900.0
    dbus_introspection_request_path: str = ""
    _dbus_introspection_snapshot_cache: dict[str, object] = field(
        default_factory=lambda: dict[str, object]()
    )
    _dbus_introspection_snapshot_loaded_at: float = 0.0


@dataclass
class DbusInputControllerFake:
    """Complete controller-side contract used by DBus input port scenarios."""

    raw_value: DbusRawValue = 12.0
    services: list[str] = field(default_factory=lambda: ["service.a"])
    pv_services: list[str] = field(default_factory=lambda: ["pv.a"])
    battery_service: str | None = "battery.a"
    invalidated_pv: int = 0
    invalidated_battery: int = 0
    invalidated_energy: list[tuple[str, str | None]] = field(default_factory=list)

    def get_dbus_value(self, service_name: str, path: str) -> DbusRawValue:
        del service_name, path
        return self.raw_value

    def list_dbus_services(self) -> list[str]:
        return list(self.services)

    def invalidate_auto_pv_services(self) -> None:
        self.invalidated_pv += 1

    def resolve_auto_pv_services(self) -> list[str]:
        return list(self.pv_services)

    def invalidate_auto_battery_service(self) -> None:
        self.invalidated_battery += 1

    def invalidate_energy_source_service(
        self,
        source_id: str,
        *,
        expected_service: str | None = None,
    ) -> bool:
        self.invalidated_energy.append((source_id, expected_service))
        return True

    def resolve_auto_battery_service(self) -> str | None:
        return self.battery_service


GatewayResult = float | int | None | Exception


@dataclass
class GatewayReaderFake:
    semantic_results: dict[GatewayReadKey, GatewayResult] = field(
        default_factory=lambda: dict[GatewayReadKey, GatewayResult]()
    )
    raw_results: dict[tuple[str, str], list[GatewayResult]] = field(
        default_factory=lambda: dict[tuple[str, str], list[GatewayResult]]()
    )
    services: list[str] = field(default_factory=lambda: list[str]())
    semantic_reads: list[tuple[GatewayReadKey, str]] = field(
        default_factory=lambda: list[tuple[GatewayReadKey, str]]()
    )
    raw_reads: list[tuple[str, str]] = field(
        default_factory=lambda: list[tuple[str, str]]()
    )
    service_list_calls: int = 0

    @staticmethod
    def _result(value: GatewayResult) -> float | int | None:
        if isinstance(value, Exception):
            raise value
        return value

    def read_semantic_value(self, key: GatewayReadKey, *, reason: str) -> float | int | None:
        self.semantic_reads.append((key, reason))
        return self._result(self.semantic_results.get(key))

    def get_dbus_value(self, service_name: str, path: str) -> float | int | None:
        self.raw_reads.append((service_name, path))
        queue = self.raw_results.get((service_name, path), [None])
        value = queue.pop(0) if len(queue) > 1 else queue[0]
        return self._result(value)

    def list_dbus_services(self) -> list[str]:
        self.service_list_calls += 1
        return list(self.services)


@dataclass
class SourceHealthFake:
    ready: dict[str, bool] = field(default_factory=lambda: dict[str, bool]())
    retry_checks: list[tuple[str, float]] = field(default_factory=lambda: list[tuple[str, float]]())
    recoveries: list[tuple[str, str, tuple[object, ...]]] = field(
        default_factory=lambda: list[tuple[str, str, tuple[object, ...]]]()
    )
    failures: list[tuple[str, float, str, float, str, tuple[object, ...]]] = field(
        default_factory=lambda: list[
            tuple[str, float, str, float, str, tuple[object, ...]]
        ]()
    )

    def retry_ready(self, source_key: str, now: float) -> bool:
        self.retry_checks.append((source_key, now))
        return self.ready.get(source_key, True)

    def recovered(self, source_key: str, message: str, *args: object) -> None:
        self.recoveries.append((source_key, message, args))

    def failed(
        self,
        source_key: str,
        now: float,
        warning_key: str,
        warning_interval: float,
        warning_message: str,
        *args: object,
    ) -> None:
        self.failures.append(
            (source_key, now, warning_key, warning_interval, warning_message, args)
        )


@dataclass
class EnergyServiceResolverFake:
    primary: EnergySourceDefinition
    services: dict[str, str]
    skipped_paths: set[tuple[str, str]] = field(
        default_factory=lambda: set[tuple[str, str]]()
    )
    invalidations: int = 0
    energy_invalidations: list[tuple[str, str | None]] = field(
        default_factory=lambda: list[tuple[str, str | None]]()
    )

    def resolve_energy_source_service(self, source: EnergySourceDefinition) -> str:
        return self.services[source.source_id]

    def primary_energy_source(self) -> EnergySourceDefinition:
        return self.primary

    def invalidate_auto_battery_service(self) -> None:
        self.invalidations += 1

    def invalidate_energy_source_service(
        self,
        source_id: str,
        *,
        expected_service: str | None = None,
    ) -> bool:
        self.energy_invalidations.append((source_id, expected_service))
        cached_service = self.services.get(source_id)
        if cached_service is None:
            return False
        if expected_service is not None and cached_service != expected_service:
            return False
        del self.services[source_id]
        return True

    def introspection_says_skip(self, service_name: str, path: str, *, priority: int) -> bool:
        del priority
        return (service_name, path) in self.skipped_paths
