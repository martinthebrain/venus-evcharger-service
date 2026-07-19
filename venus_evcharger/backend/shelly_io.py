# SPDX-License-Identifier: GPL-3.0-or-later
"""Shelly HTTP and relay-worker helpers for the Venus EV charger service."""

from __future__ import annotations

from collections.abc import Callable

from venus_evcharger.backend.shelly_io_capabilities import ShellyCapabilities
from venus_evcharger.backend.shelly_io_ports import (
    require_shelly_capability_host,
    require_shelly_lifecycle_host,
    require_shelly_readback_cache_host,
    require_shelly_readback_host,
    require_shelly_request_host,
    require_shelly_runtime_host,
    require_shelly_transport_host,
    require_shelly_worker_host,
)
from venus_evcharger.backend.shelly_io_requests import ShellyRequestClient
from venus_evcharger.backend.shelly_io_runtime import ShellyChargerRuntime
from venus_evcharger.backend.shelly_io_runtime_cache import ShellyRuntimeCache
from venus_evcharger.backend.shelly_io_split import ShellyBackendReadback
from venus_evcharger.backend.shelly_io_types import (
    JsonObject,
    PendingRelayCommand,
    ShellyEnergyData,
    ShellyPmStatus,
    ShellyRpcScalar,
    _phase_currents_for_selection,
    _phase_powers_for_selection,
    require_session,
    _single_phase_vector,
)
from venus_evcharger.backend.shelly_io_worker import ShellyWorker
from venus_evcharger.backend.shelly_io_worker_lifecycle import ShellyWorkerLifecycle
from venus_evcharger.backend.shelly_io_worker_transport import ShellyWorkerTransport


class ShellyIoController:
    """Composition root for Shelly requests, readback, runtime, and worker I/O."""

    def __init__(self, service: object) -> None:
        self.service = service
        runtime_host = require_shelly_runtime_host(service)
        self.clock: Callable[[], float] = runtime_host.time_now
        self.requests = ShellyRequestClient(require_shelly_request_host(service))
        self.capabilities = ShellyCapabilities(require_shelly_capability_host(service), self.clock)
        readback_cache_host = require_shelly_readback_cache_host(service)
        self.runtime_cache = ShellyRuntimeCache(readback_cache_host._readback_store, self.clock)
        self.runtime = ShellyChargerRuntime(
            runtime_host,
            self.runtime_cache,
            self.capabilities,
            self.clock,
        )
        self.readback = ShellyBackendReadback(
            require_shelly_readback_host(service),
            self.capabilities,
            self.runtime,
            self.runtime_cache,
            self.clock,
        )
        self.transport = ShellyWorkerTransport(require_shelly_transport_host(service))
        self.worker = ShellyWorker(
            require_shelly_worker_host(service),
            self.requests,
            self.capabilities,
            self.runtime,
            self.transport,
            self.clock,
            lambda: self._fetch_pm_status(worker=True),
        )
        self.lifecycle = ShellyWorkerLifecycle(
            require_shelly_lifecycle_host(service),
            self.transport,
            self.clock,
            self.worker.io_worker_loop,
        )

    def request(self, url: str) -> JsonObject:
        return self.requests.request(url)

    def request_with_session(self, session: object, url: str) -> JsonObject:
        return self.requests.request_with_session(require_session(session), url)

    def rpc_call(self, method: str, **params: ShellyRpcScalar) -> JsonObject:
        return self.requests.rpc_call(method, **params)

    def rpc_call_with_session(
        self,
        session: object,
        method: str,
        **params: ShellyRpcScalar,
    ) -> JsonObject:
        return self.requests.rpc_call_with_session(require_session(session), method, **params)

    def fetch_pm_status(self) -> JsonObject:
        return self._fetch_pm_status(worker=False)

    def worker_fetch_pm_status(self) -> JsonObject:
        return self._fetch_pm_status(worker=True)

    def _fetch_pm_status(self, *, worker: bool) -> JsonObject:
        now = self.clock()
        charger_state = self.runtime.read_charger_state_best_effort(now=now)
        if self.capabilities.uses_split_backends():
            return self.readback.read_pm_status(charger_state, now=now)
        if worker:
            return self.requests.worker_fetch_pm_status_rpc()
        return self.requests.fetch_pm_status_rpc()

    def set_relay(self, on: bool) -> JsonObject:
        backend = self.capabilities.split_enable_backend()
        if backend is not None:
            backend.set_enabled(bool(on))
            return {"output": bool(on)}
        return self.requests.set_relay_rpc(on)

    def build_local_pm_status(self, relay_on: bool) -> ShellyPmStatus:
        return self.worker.build_local_pm_status(relay_on)

    def publish_local_pm_status(self, relay_on: bool, now: float | None = None) -> ShellyPmStatus:
        return self.worker.publish_local_pm_status(relay_on, now)

    def queue_relay_command(self, relay_on: bool, now: float | None = None) -> None:
        self.worker.queue_relay_command(relay_on, now)

    def peek_pending_relay_command(self) -> PendingRelayCommand:
        return self.worker.peek_pending_relay_command()

    def clear_pending_relay_command(self, relay_on: bool) -> None:
        self.worker.clear_pending_relay_command(relay_on)

    def worker_apply_pending_relay_command(self) -> None:
        self.worker.worker_apply_pending_relay_command()

    def io_worker_once(self) -> None:
        self.worker.io_worker_once()

    def io_worker_loop(self) -> None:
        self.worker.io_worker_loop()

    def start_io_worker(self) -> None:
        self.lifecycle.start_io_worker()

    def phase_selection_requires_pause(self) -> bool:
        return self.capabilities.phase_selection_requires_pause()

    def set_phase_selection(self, selection: object) -> str:
        return self.capabilities.set_phase_selection(selection)

__all__ = [
    "JsonObject",
    "PendingRelayCommand",
    "ShellyEnergyData",
    "ShellyIoController",
    "ShellyPmStatus",
    "ShellyRpcScalar",
    "_phase_currents_for_selection",
    "_phase_powers_for_selection",
    "_single_phase_vector",
]
