# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed fixtures shared by the DBus gateway adapter scenario tests.

The production adapter imports Venus-only modules at import time.  This module
owns that host-side boundary once and provides small stateful doubles for the
interactions that the tests assert.  Individual case modules can therefore
describe behavior without rebuilding ad-hoc DBus services and sockets.
"""

from __future__ import annotations

import builtins
import configparser
import json
import logging
import tempfile
import time
import unittest
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

from tests.dbus_adapter_venus_stubs import FakeVeDbusService
from tests.support.async_dbus import install_read_responder, run_non_write_command

_fake_vedbus = ModuleType("vedbus")
setattr(_fake_vedbus, "VeDbusService", FakeVeDbusService)
_fake_dbus_mainloop = ModuleType("dbus.mainloop.glib")
setattr(_fake_dbus_mainloop, "DBusGMainLoop", MagicMock())

with patch.dict(
    "sys.modules",
    {"vedbus": _fake_vedbus, "dbus.mainloop.glib": _fake_dbus_mainloop},
):
    import venus_evcharger.dbus_adapter.rate as rate_module
    import venus_evcharger.dbus_adapter.dbus_errors as dbus_errors_module
    from venus_evcharger.dbus_adapter.connection import DbusConnectionManager
    from venus_evcharger.dbus_adapter.async_request import DbusWireRequest
    import venus_evcharger.dbus_adapter.health.backpressure as health_backpressure_module
    import venus_evcharger.dbus_adapter.health.freshness as health_freshness_module
    import venus_evcharger.dbus_adapter.health.history as health_history_module
    import venus_evcharger.dbus_adapter.health.queue as health_queue_module
    import venus_evcharger.dbus_adapter.health.slo as health_slo_module
    import venus_evcharger.dbus_adapter.jsonl as jsonl_module
    import venus_evcharger.dbus_adapter.process.config as process_config_module
    import venus_evcharger.dbus_adapter.process.health_regulation as process_health_regulation_module
    import venus_evcharger.dbus_adapter.process.health as process_health_module
    import venus_evcharger.dbus_adapter.process.introspection as introspection_module
    import venus_evcharger.dbus_adapter.process.introspection_snapshot as introspection_snapshot_module
    import venus_evcharger.dbus_adapter.process.io as process_io_module
    import venus_evcharger.dbus_adapter.process.loop as process_loop_module
    import venus_evcharger.dbus_adapter.process.runtime as runtime_module
    import venus_evcharger.dbus_adapter.process.socket as process_socket_module
    import venus_evcharger.dbus_adapter.publication.registry as publication_registry_module
    import venus_evcharger.dbus_adapter.read.executor as read_module
    import venus_evcharger.dbus_adapter.read.aggregate as read_aggregate_module
    import venus_evcharger.dbus_adapter.read.pv as read_pv_module
    import venus_evcharger.dbus_adapter.read.spec as read_spec_module
    import venus_evcharger.dbus_adapter.read.targets as read_targets_module
    import venus_evcharger.dbus_adapter.write.core as write_core_module
    import venus_evcharger.dbus_adapter.write.dispatch as write_dispatch_module
    import venus_evcharger.dbus_adapter.write.health as write_health_module
    import venus_evcharger.dbus_adapter.write.support as write_support_module
    import venus_evcharger.dbus_gateway_core as gateway_core_module
    import venus_evcharger_dbus_adapter as adapter_module
    from venus_evcharger.dbus_adapter.rate import (
        DbusCircuitBreaker,
        DbusOperationDeferred,
        DbusRateLimiter,
    )
    from venus_evcharger.dbus_adapter.resources import ResourceMonitor
    from venus_evcharger.dbus_adapter.scheduling import (
        AtomicJsonWriter,
        DbusDiscoveryManager,
        DbusReadScheduler,
    )
    from venus_evcharger.dbus_adapter.read.spec import read_spec_from_mapping
    from venus_evcharger.dbus_adapter.tick_health import TickHealth
    from venus_evcharger.dbus_gateway import (
        DbusCacheStore,
        DbusGatewayCommandInbox,
        dbus_path_key,
        gateway_paths,
        read_json_file,
    )
    from venus_evcharger.ipc.command_types import CommandFileList, CommandMapping
    from venus_evcharger.ipc.gateway_publication import (
        publish_companion_fields_command,
        publish_evcs_fields_command,
        register_companion_command,
        register_evcs_command,
    )
    from venus_evcharger.ports.gateway_publication import (
        CompanionServiceIdentity,
        EvcsServiceIdentity,
        PublicationPriority,
    )
    from venus_evcharger_dbus_adapter import DbusAdapter
    from venus_evcharger_dbus_adapter import main as adapter_main


def install_mock(target: object, name: str, mock: MagicMock) -> MagicMock:
    """Install and return a mock when the interaction itself is under test."""
    setattr(target, name, mock)
    return mock


def install_dbus_call_responder(
    connection: object,
    responder: Callable[[str, str, str, str, str, tuple[object, ...]], object],
) -> MagicMock:
    """Complete adapter-neutral DBus requests through their callbacks."""

    def send_async(
        request: DbusWireRequest,
        reply_handler: Callable[..., None],
        error_handler: Callable[[object], None],
    ) -> object:
        try:
            value = responder(
                request.service,
                request.path,
                request.interface,
                request.method_name,
                request.signature,
                request.args,
            )
        except Exception as error:
            error_handler(error)
        else:
            reply_handler(value)
        return object()

    return install_mock(
        connection,
        "send_async",
        MagicMock(side_effect=send_async),
    )


def dbus_wire_call(request: DbusWireRequest) -> tuple[str, str, str, str, str, tuple[object, ...]]:
    """Return the semantic wire fields asserted by gateway scenario tests."""
    return (
        request.service,
        request.path,
        request.interface,
        request.method_name,
        request.signature,
        request.args,
    )


def evcs_identity() -> EvcsServiceIdentity:
    """Return the canonical semantic EVCS identity used by scheduler tests."""
    return EvcsServiceIdentity(
        product_name="Test EVCS",
        custom_name="Garage",
        firmware_version="1.2.3",
        hardware_version="relay",
        serial="evcs-test-60",
        connection_name="Local controller",
        process_name="venus_evcharger_service.py",
        process_version="Python",
    )


def evcs_registration(fields: Mapping[str, object] | None = None) -> CommandMapping:
    """Build one scheduler-ready semantic EVCS registration command."""
    return register_evcs_command(evcs_identity(), fields or {"mode": 0, "connected": 1})


def evcs_publication(
    fields: Mapping[str, object] | None = None,
    *,
    priority: PublicationPriority = "live",
) -> CommandMapping:
    """Build one scheduler-ready semantic EVCS field publication."""
    return publish_evcs_fields_command(fields or {"ac_power_w": 1200.0}, priority=priority)


def observe_evcs_fields(
    adapter: DbusAdapter,
    observations: Mapping[str, tuple[object, float]],
    *,
    now: float,
    monotonic_at: float | None = None,
) -> float:
    """Apply timestamped semantic EVCS observations through the real registry."""
    monotonic_now = time.monotonic() if monotonic_at is None else float(monotonic_at)
    if not adapter.publication_registry.evcs_registered:
        with (
            patch.object(
                publication_registry_module.time,
                "time",
                return_value=now,
            ),
            patch.object(
                publication_registry_module.time,
                "monotonic",
                return_value=monotonic_now,
            ),
        ):
            outcome = adapter.write_scheduler.process_command(evcs_registration({"connected": 1}))
        if outcome != "applied":
            raise AssertionError(f"EVCS registration failed: {outcome}")
    for field, (value, age_seconds) in observations.items():
        with (
            patch.object(
                publication_registry_module.time,
                "time",
                return_value=now - age_seconds,
            ),
            patch.object(
                publication_registry_module.time,
                "monotonic",
                return_value=monotonic_now - age_seconds,
            ),
        ):
            outcome = adapter.write_scheduler.process_command(evcs_publication({field: value}))
        if outcome != "applied":
            raise AssertionError(f"EVCS publication failed for {field}: {outcome}")
    return monotonic_now


def companion_identity(service_id: str = "aggregate-grid") -> CompanionServiceIdentity:
    """Return one opaque semantic companion identity."""
    return CompanionServiceIdentity(
        service_id=service_id,
        kind="grid",
        product_name="External Grid",
        custom_name="Grid",
        firmware_version="1.2.3",
        hardware_version="virtual",
        serial=f"grid-{service_id}",
        connection_name="External energy companion",
        process_name="venus_evcharger_service.py",
        process_version="Python",
    )


def companion_registration(
    service_id: str = "aggregate-grid",
    fields: Mapping[str, object] | None = None,
) -> CommandMapping:
    """Build one scheduler-ready companion registration command."""
    return register_companion_command(companion_identity(service_id), fields or {"connected": 1})


def companion_publication(
    service_id: str = "aggregate-grid",
    fields: Mapping[str, object] | None = None,
    *,
    priority: PublicationPriority = "live",
) -> CommandMapping:
    """Build one scheduler-ready companion field publication."""
    return publish_companion_fields_command(
        service_id,
        fields or {"ac_power_w": 500.0},
        priority=priority,
    )


class RecordingDbusService(dict[str, object]):
    """In-memory VeDbusService double that records ordered value writes."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__()
        self.args = args
        self.kwargs = kwargs
        self.registered = False
        self.added_paths: dict[str, dict[str, object]] = {}
        self.writes: list[tuple[str, object]] = []

    def register(self) -> None:
        self.registered = True

    def add_path(self, path: str, value: object, **kwargs: object) -> None:
        self.added_paths[path] = {"value": value, **kwargs}
        self[path] = value

    def __setitem__(self, path: str, value: object) -> None:
        self.writes.append((path, value))
        super().__setitem__(path, value)


class SocketClientStub:
    """Context-managed socket client with deterministic receive behavior."""

    def __init__(self, payload: bytes = b"", *, error: BaseException | None = None) -> None:
        self.payload = payload
        self.error = error
        self.timeouts: list[float] = []
        self.sent: list[bytes] = []

    def __enter__(self) -> SocketClientStub:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def settimeout(self, timeout: float) -> None:
        self.timeouts.append(timeout)

    def recv(self, _size: int) -> bytes:
        if self.error is not None:
            raise self.error
        return self.payload

    def sendall(self, payload: bytes) -> None:
        self.sent.append(payload)


class SocketServerStub:
    """Socket acceptor double used by one-tick IPC scenarios."""

    def __init__(self, client: SocketClientStub, *, error: BaseException | None = None) -> None:
        self.client = client
        self.error = error
        self.accept_calls = 0

    def accept(self) -> tuple[SocketClientStub, object]:
        self.accept_calls += 1
        if self.error is not None:
            raise self.error
        return self.client, object()


@dataclass(frozen=True)
class GatewayAdapterScenario:
    """Filesystem-backed adapter fixture with an isolated runtime directory."""

    root: Path
    config_path: Path
    adapter: DbusAdapter


@contextmanager
def adapter_scenario(
    config_text: str = "[DEFAULT]\n",
    *,
    run_directory: str = "run",
) -> Iterator[GatewayAdapterScenario]:
    """Create a complete adapter scenario and clean it up after the assertion."""
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        config_path = root / "config.ini"
        config_path.write_text(config_text, encoding="utf-8")
        adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(root / run_directory)))
        yield GatewayAdapterScenario(root=root, config_path=config_path, adapter=adapter)


class GatewayAdapterContractCase(unittest.TestCase):
    """Typed assertion and scenario base for split gateway adapter cases."""

    @staticmethod
    def adapter_scenario(
        config_text: str = "[DEFAULT]\n",
        *,
        run_directory: str = "run",
    ) -> AbstractContextManager[GatewayAdapterScenario]:
        return adapter_scenario(config_text, run_directory=run_directory)


__all__ = [
    "AtomicJsonWriter",
    "Callable",
    "CommandFileList",
    "CommandMapping",
    "DbusAdapter",
    "DbusCacheStore",
    "DbusCircuitBreaker",
    "DbusGatewayCommandInbox",
    "DbusConnectionManager",
    "DbusDiscoveryManager",
    "DbusOperationDeferred",
    "DbusRateLimiter",
    "DbusReadScheduler",
    "EvcsServiceIdentity",
    "FakeVeDbusService",
    "GatewayAdapterContractCase",
    "GatewayAdapterScenario",
    "MagicMock",
    "Path",
    "RecordingDbusService",
    "ResourceMonitor",
    "SocketClientStub",
    "SocketServerStub",
    "TickHealth",
    "adapter_main",
    "adapter_module",
    "adapter_scenario",
    "builtins",
    "configparser",
    "companion_identity",
    "companion_publication",
    "companion_registration",
    "dbus_path_key",
    "evcs_identity",
    "evcs_publication",
    "evcs_registration",
    "gateway_core_module",
    "gateway_paths",
    "health_backpressure_module",
    "health_freshness_module",
    "health_history_module",
    "health_queue_module",
    "health_slo_module",
    "install_dbus_call_responder",
    "install_mock",
    "install_read_responder",
    "introspection_module",
    "introspection_snapshot_module",
    "json",
    "jsonl_module",
    "logging",
    "patch",
    "process_config_module",
    "process_health_regulation_module",
    "process_health_module",
    "process_io_module",
    "process_loop_module",
    "process_socket_module",
    "rate_module",
    "dbus_errors_module",
    "read_aggregate_module",
    "read_json_file",
    "read_module",
    "run_non_write_command",
    "read_pv_module",
    "read_spec_from_mapping",
    "read_targets_module",
    "runtime_module",
    "tempfile",
    "time",
    "unittest",
    "write_core_module",
    "write_dispatch_module",
    "write_health_module",
    "write_support_module",
]
