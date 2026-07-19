# SPDX-License-Identifier: GPL-3.0-or-later
"""Scenarios proving backend readbacks stay complete and immutable."""

from __future__ import annotations

import math
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from venus_evcharger.backend.models import ChargerState, SwitchState
from venus_evcharger.backend.shelly_io_capabilities import ShellyCapabilities
from venus_evcharger.backend.shelly_io_runtime import ShellyChargerRuntime
from venus_evcharger.backend.shelly_io_runtime_cache import ShellyRuntimeCache
from venus_evcharger.ports.readback import TimedChargerState
from venus_evcharger.readback_store import InMemoryReadbackStore
from venus_evcharger.update.relay import build_relay_foundation
from venus_evcharger.update.relay_charger_transport import ChargerTransportTracker
from venus_evcharger.update.readback_resolver import ReadbackResolver
from venus_evcharger.update.state import UpdateStateController


def _phase_values(power: float, voltage: float, _phase: object, _mode: object) -> dict[str, dict[str, float]]:
    current = 0.0 if voltage == 0.0 else power / voltage
    return {
        "L1": {"power": power, "voltage": voltage, "current": current},
        "L2": {"power": 0.0, "voltage": voltage, "current": 0.0},
        "L3": {"power": 0.0, "voltage": voltage, "current": 0.0},
    }


def _service(now: float = 100.0) -> SimpleNamespace:
    store = InMemoryReadbackStore()
    clock = lambda: now
    service = SimpleNamespace(
        _readback_store=store,
        time_now=clock,
    )
    settings = SimpleNamespace(_worker_poll_interval_seconds=1.0, auto_shelly_soft_fail_seconds=5.0)
    service._readback_resolver = ReadbackResolver(store, settings, clock=clock)
    return service


def _charger_runtime(service: SimpleNamespace) -> tuple[ShellyChargerRuntime, MagicMock]:
    capabilities = MagicMock()
    cache = ShellyRuntimeCache(service._readback_store, service.time_now)
    return ShellyChargerRuntime(service, cache, capabilities, service.time_now), capabilities


class TestBackendReadbackSnapshotContracts(unittest.TestCase):
    def test_charger_producer_stores_the_exact_complete_state(self) -> None:
        service = _service()
        runtime, _capabilities = _charger_runtime(service)
        state = ChargerState(
            enabled=True,
            current_amps=12.0,
            phase_selection="P1_P2_P3",
            actual_current_amps=11.8,
            power_w=8150.0,
            energy_kwh=42.5,
            status_text="charging",
            fault_text=None,
        )

        runtime._store_runtime_charger_snapshot(state, 95.0)
        snapshot = service._readback_store.snapshot().charger

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertIs(snapshot.state, state)
        self.assertEqual(snapshot.captured_at, 95.0)

    def test_cached_charger_fallback_never_rebuilds_from_mutating_fields(self) -> None:
        service = _service()
        runtime, _capabilities = _charger_runtime(service)
        self.assertIsNone(runtime.cache.cached_charger_state(now=100.0, max_age_seconds=5.0))

        state = ChargerState(True, 10.0, "P1", 9.5, 2185.0, 3.0, "charging", None)
        runtime._store_runtime_charger_snapshot(state, 95.0)

        service._last_charger_state_enabled = False
        service._last_charger_state_current_amps = 1.0
        service._last_charger_state_power_w = 10.0
        service._last_charger_state_status = "fault"

        self.assertIs(
            runtime.cache.cached_charger_state(now=100.0, max_age_seconds=5.0),
            state,
        )
        self.assertIsNone(runtime.cache.cached_charger_state(now=100.001, max_age_seconds=5.0))
        self.assertIs(runtime.cache.cached_charger_state(now=90.0, max_age_seconds=5.0), state)
        self.assertIs(runtime.cache.cached_charger_state(max_age_seconds=6.0), state)
        self.assertIs(runtime.cache.cached_charger_state(max_age_seconds=None), state)

    def test_empty_charger_snapshot_is_not_a_usable_fallback(self) -> None:
        service = _service()
        runtime, _capabilities = _charger_runtime(service)
        empty = ChargerState(None, None, None)
        runtime._store_runtime_charger_snapshot(empty, 100.0)

        self.assertIsNone(runtime.cache.cached_charger_state(now=100.0, max_age_seconds=5.0))

    def test_non_finite_charger_snapshot_timestamp_is_not_a_usable_fallback(self) -> None:
        service = _service()
        runtime, _capabilities = _charger_runtime(service)
        state = ChargerState(True, 10.0, "P1")

        for captured_at in (math.nan, math.inf, -math.inf):
            with self.subTest(captured_at=captured_at):
                service._readback_store.replace_charger(TimedChargerState(state, captured_at))
                self.assertIsNone(runtime.cache.cached_charger_state(max_age_seconds=None))

    def test_missing_charger_snapshot_cannot_request_load(self) -> None:
        service = _service()

        health = build_relay_foundation(_phase_values).health
        self.assertFalse(health._charger_requests_load(service, 100.0))

    def test_missing_charger_backend_invalidates_its_stored_snapshot(self) -> None:
        service = _service()
        runtime, capabilities = _charger_runtime(service)
        service._readback_store.replace_charger(TimedChargerState(ChargerState(True, 10.0, "P1"), 100.0))

        capabilities.charger_state_backend.return_value = None
        self.assertIsNone(runtime._charger_read_context(100.0))

        self.assertIsNone(service._readback_store.snapshot().charger)

    def test_transport_clock_contract_preserves_explicit_service_and_fallback_times(self) -> None:
        self.assertEqual(ChargerTransportTracker.now(SimpleNamespace(), 12.5), 12.5)
        self.assertEqual(
            ChargerTransportTracker.now(SimpleNamespace(time_now=lambda: 23.5)),
            23.5,
        )
        with patch("venus_evcharger.update.relay_charger_transport.time.time", return_value=34.5):
            self.assertEqual(ChargerTransportTracker.now(SimpleNamespace()), 34.5)

    def test_local_pm_publisher_boundary_preserves_valid_result_and_invalid_fallback(self) -> None:
        runtime = SimpleNamespace(
            publish_local_pm_status=lambda _relay_on, _now: {"output": True, "apower": 42.0},
            warning_throttled=MagicMock(),
        )
        service = SimpleNamespace(runtime=runtime, auto_shelly_soft_fail_seconds=5.0)
        foundation = build_relay_foundation(_phase_values)
        controller = UpdateStateController(service, foundation.targets, foundation.health, lambda _reason: 0)
        self.assertEqual(
            controller._publish_startup_local_pm_status({"output": False}, True, 100.0),
            {"output": True, "apower": 42.0},
        )

        controller.service.runtime.publish_local_pm_status = lambda _relay_on, _now: None
        self.assertEqual(
            controller._publish_startup_local_pm_status({"output": True, "apower": 42.0}, False, 101.0),
            {"output": False, "apower": 0.0, "current": 0.0},
        )

    def test_switch_producer_replaces_and_clears_one_complete_state(self) -> None:
        service = _service(321.0)
        capabilities = ShellyCapabilities(service, service.time_now)
        state = SwitchState(True, "P1_P2", feedback_closed=True, interlock_ok=False)

        capabilities.store_runtime_switch_snapshot(state)
        snapshot = service._readback_store.snapshot().switch

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertIs(snapshot.state, state)
        self.assertEqual(snapshot.captured_at, 321.0)

        service._last_switch_feedback_closed = False
        service._last_switch_interlock_ok = True
        self.assertIs(service._readback_store.snapshot().switch, snapshot)

        capabilities.store_runtime_switch_snapshot(None, now=400.0)
        self.assertIsNone(service._readback_store.snapshot().switch)


if __name__ == "__main__":
    unittest.main()
