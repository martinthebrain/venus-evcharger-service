# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed component doubles for auto-input helper contract tests."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from venus_evcharger.dbus_gateway import GatewayReadKey
from venus_evcharger.energy import EnergySourceDefinition
from venus_evcharger.inputs.helper.config_runtime import AutoInputHelperSettings, load_auto_input_helper_settings
from venus_evcharger.inputs.helper.contracts import Snapshot

ROOT = Path(__file__).resolve().parents[2]


def run_callback(callback: Callable[[], object]) -> object:
    return callback()


def helper_settings(*, parent_pid: int | None = None) -> AutoInputHelperSettings:
    return load_auto_input_helper_settings(
        str(ROOT / "deploy/venus/config.venus_evcharger.ini"),
        "/tmp/auto-input-helper-test.json",
        parent_pid,
        3,
        "test-instance",
    )


class FakeGatewayClientPaths:
    def __init__(self, cache_path: str = "/run/fake-dbus-cache.json") -> None:
        self.cache_path = cache_path


class FakeGatewayClient:
    def __init__(self, cache_path: str = "/run/fake-dbus-cache.json") -> None:
        self.paths = FakeGatewayClientPaths(cache_path)
        self.commands: list[dict[str, object]] = []
        self.read_requests: list[tuple[object, str, str, str]] = []
        self.enqueue_error: OSError | None = None
        self.read_error: OSError | None = None

    def enqueue_command(self, command: Mapping[str, object]) -> str:
        if self.enqueue_error is not None:
            raise self.enqueue_error
        self.commands.append(dict(command))
        return f"command-{len(self.commands)}"

    def request_read_key(
        self,
        key: object,
        *,
        priority: str = "read",
        source: str = "core",
        reason: str = "",
    ) -> None:
        if self.read_error is not None:
            raise self.read_error
        self.read_requests.append((key, priority, source, reason))


class FakeGateway:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], float | int | None] = {}
        self.semantic: dict[GatewayReadKey, float | int | None] = {}
        self.services: list[str] = []
        self.requests: list[tuple[str, str, int, str]] = []
        self.service_refreshes = 0
        self.retry_ready: dict[str, bool] = {}
        self.delayed: list[str] = []

    def cached_value(self, service_name: str, path: str) -> float | int | None:
        return self.values.get((service_name, path))

    def semantic_value(self, key: GatewayReadKey, *, reason: str) -> float | int | None:
        del reason
        return self.semantic.get(key)

    def service_names(self) -> list[str]:
        return list(self.services)

    def service_available(self, service_name: str) -> bool:
        return service_name in self.services

    def request_value(self, service_name: str, path: str, *, priority: int, reason: str) -> None:
        self.requests.append((service_name, path, priority, reason))

    def request_service_refresh(self) -> bool:
        self.service_refreshes += 1
        return True

    def source_retry_ready(self, key: str) -> bool:
        return self.retry_ready.get(key, True)

    def delay_source_retry(self, key: str) -> None:
        self.delayed.append(key)


class FakeSources:
    def __init__(self) -> None:
        self.pv: float | None = 100.0
        self.battery: Snapshot = {"battery_soc": 50.0}
        self.grid: float | None = -20.0

    def pv_power(self) -> float | None:
        return self.pv

    def battery_snapshot(self) -> Snapshot:
        return dict(self.battery)

    def grid_power(self) -> float | None:
        return self.grid


class MemoryWriter:
    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []

    def write(self, payload: Mapping[str, object]) -> None:
        self.payloads.append(dict(payload))


class FakeSnapshots:
    def __init__(self) -> None:
        self.refreshed: list[tuple[str, float | None]] = []
        self.refresh_all_calls = 0
        self.lifecycle: list[str] = []
        self.heartbeat_calls = 0
        self.refresh_error: Exception | None = None
        self.heartbeat_error: Exception | None = None

    def refresh_source(self, source_name: str, now: float | None = None) -> None:
        if self.refresh_error is not None:
            raise self.refresh_error
        self.refreshed.append((source_name, now))

    def refresh_all(self, now: float | None = None) -> None:
        del now
        self.refresh_all_calls += 1

    def heartbeat(self) -> bool:
        if self.heartbeat_error is not None:
            raise self.heartbeat_error
        self.heartbeat_calls += 1
        return True

    def write_lifecycle(self, state: str, now: float | None = None) -> None:
        del now
        self.lifecycle.append(state)


class FakeCatalog:
    def __init__(self, primary: EnergySourceDefinition) -> None:
        self.primary = primary
        self.readable: set[tuple[str, str]] = set()

    def primary_source(self) -> EnergySourceDefinition:
        return self.primary

    def primary_service_prefix(self) -> str:
        return self.primary.service_prefix

    def source_has_readable_data(self, source: EnergySourceDefinition, service_name: str) -> bool:
        return (source.source_id, service_name) in self.readable

    def battery_service_has_soc(self, service_name: str) -> bool:
        return (self.primary.source_id, service_name) in self.readable


class FakeResolver:
    def __init__(self, service_name: str = "com.victronenergy.battery.test") -> None:
        self.service_name = service_name
        self.invalidations = 0
        self.resolve_error: Exception | None = None

    def resolve(self, source: EnergySourceDefinition) -> str:
        del source
        if self.resolve_error is not None:
            raise self.resolve_error
        return self.service_name

    def invalidate_primary(self) -> None:
        self.invalidations += 1


class FakePvGrid:
    def __init__(self, services: list[str] | None = None) -> None:
        self.services = services or []
        self.resolve_error: Exception | None = None

    def resolve_pv_services(self) -> list[str]:
        if self.resolve_error is not None:
            raise self.resolve_error
        return list(self.services)


class FakeLoop:
    def __init__(self) -> None:
        self.run_calls = 0
        self.quit_calls = 0

    def run(self) -> None:
        self.run_calls += 1

    def quit(self) -> None:
        self.quit_calls += 1


class WarningRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, float, str, tuple[object, ...]]] = []

    def __call__(self, key: str, interval_seconds: float, message: str, *args: object) -> None:
        self.calls.append((key, interval_seconds, message, args))
