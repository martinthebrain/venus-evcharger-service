# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway adapter circuit, connection, and resource contracts."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    DbusCircuitBreaker,
    DbusConnectionManager,
    GatewayAdapterContractCase,
    MagicMock,
    ResourceMonitor,
    TickHealth,
    dbus_errors_module,
    install_mock,
    patch,
    rate_module,
)
from venus_evcharger.dbus_adapter.async_request import DbusWireRequest
import venus_evcharger.dbus_adapter.connection as connection_module
from venus_evcharger.dbus_adapter.resource_pressure import resource_state
from venus_evcharger.dbus_adapter.resources import ResourceMonitorSettings


class GatewayCircuitResourceCases(GatewayAdapterContractCase):
    """Exercise circuit, connection, and resource contracts."""

    def test_circuit_breaker_states_priorities_and_timeout_detection(self) -> None:
        class _CustomDbusError(Exception):
            pass

        with patch.object(dbus_errors_module.dbus, "DBusException", _CustomDbusError, create=True):
            self.assertIs(dbus_errors_module._dbus_exception_type(), _CustomDbusError)
        with patch.object(dbus_errors_module.dbus, "DBusException", object, create=True):
            self.assertIs(dbus_errors_module._dbus_exception_type(), RuntimeError)
        with patch.object(dbus_errors_module.dbus, "DBusException", "bad", create=True):
            self.assertIs(dbus_errors_module._dbus_exception_type(), RuntimeError)
        with patch.object(dbus_errors_module, "dbus", object()):
            self.assertIs(dbus_errors_module._dbus_exception_type(), RuntimeError)
        self.assertEqual(rate_module._normalized_kind(" read "), "read")
        self.assertEqual(rate_module._normalized_kind("  "), "dbus")
        self.assertEqual(rate_module._normalized_priority(" USER "), "user")
        self.assertEqual(rate_module._normalized_priority("  "), "diagnostic")

        breaker = DbusCircuitBreaker(degraded_seconds=30.0, protective_seconds=60.0)
        self.assertEqual(breaker.degraded_seconds, 30.0)
        self.assertEqual(breaker.protective_seconds, 60.0)
        self.assertEqual(breaker.last_success_at, 0.0)
        self.assertEqual(breaker.last_error, "")
        self.assertEqual(breaker.consecutive_failures, 0)
        self.assertEqual(breaker.state(now=100.0), "ok")
        self.assertTrue(breaker.allows_priority("diagnostic"))
        self.assertTrue(breaker.allows_priority(""))

        class _NamedTimeout(Exception):
            def get_dbus_name(self) -> str:
                return "org.freedesktop.DBus.Error.NoReply"

        class _BrokenName(Exception):
            def get_dbus_name(self) -> str:
                raise RuntimeError("name unavailable")

        with (
            patch.object(rate_module.time, "time", return_value=1000.0),
            patch.object(rate_module.time, "monotonic", return_value=100.0),
        ):
            for _index in range(3):
                breaker.record_error(_NamedTimeout("slow"))
            self.assertEqual(breaker.state(now=1001.0), "degraded")
            self.assertEqual(breaker.state(monotonic_at=101.0), "degraded")
            self.assertFalse(breaker.allows_priority("discovery"))
            self.assertTrue(breaker.allows_priority("optional"))
            for _index in range(2):
                breaker.record_error(TimeoutError("timeout"))
            self.assertEqual(breaker.consecutive_failures, 5)
            self.assertEqual(breaker.state(now=1001.0), "degraded")
            for _index in range(3):
                breaker.record_error(TimeoutError("timeout"))
            self.assertEqual(breaker.state(now=1001.0), "protective")
            self.assertFalse(breaker.allows_priority("optional"))
            self.assertTrue(breaker.allows_priority("read"))

        with (
            patch.object(rate_module.time, "time", return_value=1010.0),
            patch.object(rate_module.time, "monotonic", return_value=110.0),
        ):
            breaker.record_success(3.5, kind="write")
        self.assertEqual(breaker.last_success_at, 1010.0)
        self.assertEqual(breaker.last_error, "")
        self.assertEqual(breaker.consecutive_failures, 0)
        self.assertIn("write", breaker.latencies_by_kind)
        with (
            patch.object(rate_module.time, "time", return_value=1011.0),
            patch.object(rate_module.time, "monotonic", return_value=111.0),
        ):
            breaker.record_success(4.5)
        self.assertIn("dbus", breaker.latencies_by_kind)
        with (
            patch.object(rate_module.time, "time", return_value=1100.0),
            patch.object(rate_module.time, "monotonic", return_value=200.0),
        ):
            breaker.record_error(_BrokenName("plain"))
            health = breaker.health()
            self.assertEqual(health["state"], breaker.state())
            self.assertEqual(health["last_success_at"], breaker.last_success_at)
            self.assertEqual(health["last_error"], "plain")
            self.assertIn("avg_latency_ms", health)
            self.assertIn("operations", health)
            self.assertIn("write", health["operations"])
            self.assertEqual(health["errors_60s"], 1)
            self.assertEqual(health["consecutive_failures"], 1)
        self.assertEqual(breaker.state(now=2000.0), "ok")
        with (
            patch.object(rate_module.time, "time", return_value=3000.0),
            patch.object(rate_module.time, "monotonic", return_value=300.0),
        ):
            breaker.record_success(1.0, kind="")
            self.assertIn("dbus", breaker.latencies_by_kind)
            breaker.record_error(TimeoutError("timeout"))
            self.assertEqual(breaker.last_error, "timeout")
            self.assertIn(("dbus"), breaker.latencies_by_kind)
            self.assertGreaterEqual(breaker.health()["successes_60s"], 1)
        pruning_breaker = DbusCircuitBreaker()
        with (
            patch.object(rate_module.time, "time", return_value=1.0),
            patch.object(rate_module.time, "monotonic", return_value=1.0),
        ):
            pruning_breaker.record_success(1.0)
            pruning_breaker.record_error(TimeoutError("timeout"))
        with (
            patch.object(rate_module.time, "time", return_value=100.0),
            patch.object(rate_module.time, "monotonic", return_value=100.0),
        ):
            self.assertEqual(pruning_breaker.health()["successes_60s"], 0)
            self.assertEqual(pruning_breaker.health()["errors_60s"], 0)
        pruning_breaker = DbusCircuitBreaker()
        with (
            patch.object(rate_module.time, "time", return_value=40.0),
            patch.object(rate_module.time, "monotonic", return_value=40.0),
        ):
            pruning_breaker.record_success(1.0)
            pruning_breaker.record_error(RuntimeError("plain"), kind=" write ")
        with (
            patch.object(rate_module.time, "time", return_value=100.0),
            patch.object(rate_module.time, "monotonic", return_value=100.0),
        ):
            boundary_health = pruning_breaker.health()
        self.assertEqual(boundary_health["successes_60s"], 1)
        self.assertEqual(boundary_health["errors_60s"], 1)
        self.assertEqual(list(pruning_breaker._errors), [(40.0, "write")])
        self.assertEqual(list(pruning_breaker._successes), [(40.0, "dbus")])
        pruning_breaker = DbusCircuitBreaker()
        with (
            patch.object(rate_module.time, "time", return_value=39.5),
            patch.object(rate_module.time, "monotonic", return_value=39.5),
        ):
            pruning_breaker.record_success(1.0)
            pruning_breaker.record_error(RuntimeError("plain"))
        with (
            patch.object(rate_module.time, "time", return_value=40.0),
            patch.object(rate_module.time, "monotonic", return_value=40.0),
        ):
            pruning_breaker.record_success(1.0)
            pruning_breaker.record_error(RuntimeError("plain"))
        with (
            patch.object(rate_module.time, "time", return_value=100.0),
            patch.object(rate_module.time, "monotonic", return_value=100.0),
        ):
            narrowed_health = pruning_breaker.health()
        self.assertEqual(narrowed_health["successes_60s"], 1)
        self.assertEqual(narrowed_health["errors_60s"], 1)
        self.assertEqual(list(pruning_breaker._successes), [(40.0, "dbus")])
        self.assertEqual(list(pruning_breaker._errors), [(40.0, "dbus")])
        clamped_breaker = DbusCircuitBreaker(degraded_seconds=0.0, protective_seconds=-1.0)
        self.assertEqual(clamped_breaker.degraded_seconds, 1.0)
        self.assertEqual(clamped_breaker.protective_seconds, 1.0)

    def test_circuit_breaker_timeout_name_and_priority_contracts(self) -> None:
        class _NamedDbusTimeout(Exception):
            def __init__(self, name: object) -> None:
                super().__init__("plain")
                self.name = name

            def get_dbus_name(self) -> object:
                return self.name

        self.assertTrue(DbusCircuitBreaker._looks_like_timeout(TimeoutError("timed out")))
        self.assertTrue(DbusCircuitBreaker._looks_like_timeout(RuntimeError("NoReply from dbus")))
        self.assertTrue(DbusCircuitBreaker._looks_like_timeout(RuntimeError("no_reply from dbus")))
        self.assertTrue(DbusCircuitBreaker._looks_like_timeout(_NamedDbusTimeout("org.freedesktop.DBus.Error.NoReply")))
        self.assertFalse(DbusCircuitBreaker._looks_like_timeout(RuntimeError("plain failure")))
        self.assertEqual(
            dbus_errors_module._dbus_error_name(_NamedDbusTimeout("Example.Error")),
            "example.error",
        )
        self.assertEqual(dbus_errors_module._dbus_error_name(RuntimeError("plain")), "")
        self.assertEqual(
            dbus_errors_module._dbus_error_name(_NamedDbusTimeout(RuntimeError("bad"))),
            "bad",
        )

        class _BrokenName(Exception):
            def get_dbus_name(self) -> str:
                raise RuntimeError("name unavailable")

        self.assertEqual(dbus_errors_module._dbus_error_name(_BrokenName("plain")), "")

        breaker = DbusCircuitBreaker()
        breaker.protective_until = 100.0
        breaker.degraded_until = 200.0
        self.assertEqual(breaker.state(now=50.0), "protective")
        self.assertEqual(breaker.state(now=100.0), "degraded")
        self.assertEqual(breaker.state(now=150.0), "degraded")
        self.assertEqual(breaker.state(now=200.0), "ok")
        self.assertEqual(breaker.state(now=250.0), "ok")
        self.assertIs(breaker._kind_window("read"), breaker._kind_window("read"))
        self.assertIs(breaker._kind_window(""), breaker._kind_window("dbus"))
        with patch.object(breaker, "state", return_value="protective"):
            self.assertTrue(breaker.allows_priority("safety"))
            self.assertTrue(breaker.allows_priority("USER"))
            self.assertTrue(breaker.allows_priority("read"))
            self.assertFalse(breaker.allows_priority("optional"))
            self.assertFalse(breaker.allows_priority("unknown"))
        with patch.object(breaker, "state", return_value="degraded"):
            self.assertTrue(breaker.allows_priority("optional"))
            self.assertFalse(breaker.allows_priority("discovery"))

    def test_circuit_breaker_default_kind_and_health_summary_contracts(self) -> None:
        default_breaker = DbusCircuitBreaker()
        self.assertEqual(default_breaker.degraded_seconds, 60.0)
        self.assertEqual(default_breaker.protective_seconds, 180.0)
        self.assertEqual(default_breaker.degraded_until, 0.0)
        self.assertEqual(default_breaker.protective_until, 0.0)

        breaker = DbusCircuitBreaker()
        latency_window = MagicMock()
        kind_window = MagicMock()
        breaker.latencies = latency_window
        with patch.object(breaker, "_kind_window", MagicMock(return_value=kind_window)) as kind_window_factory:
            with (
                patch.object(rate_module.time, "time", return_value=420.0),
                patch.object(rate_module.time, "monotonic", return_value=42.0),
            ):
                breaker.record_success(7.5)
        latency_window.record_latency.assert_called_once_with(7.5, now=42.0)
        kind_window.record_latency.assert_called_once_with(7.5, now=42.0)
        kind_window_factory.assert_called_once_with("dbus")
        self.assertEqual(list(breaker._successes), [(42.0, "dbus")])
        self.assertEqual(breaker.last_success_at, 420.0)

        breaker = DbusCircuitBreaker()
        latency_window = MagicMock()
        kind_window = MagicMock()
        latency_window.summary.return_value = {"timeouts_60s": 1}
        breaker.latencies = latency_window
        with patch.object(breaker, "_kind_window", MagicMock(return_value=kind_window)) as kind_window_factory:
            with (
                patch.object(rate_module.time, "time", return_value=500.0),
                patch.object(rate_module.time, "monotonic", return_value=50.0),
            ):
                breaker.record_error(TimeoutError("timeout"))
        latency_window.record_timeout.assert_called_once_with(now=50.0)
        latency_window.summary.assert_called_once_with(now=50.0)
        kind_window.record_timeout.assert_called_once_with(now=50.0)
        kind_window_factory.assert_called_once_with("dbus")
        self.assertEqual(list(breaker._errors), [(50.0, "dbus")])

        breaker = DbusCircuitBreaker()
        breaker.degraded_until = 10.0
        breaker.protective_until = 20.0
        main_summary = {"avg_latency_ms": 1.5, "timeouts_60s": 2}
        read_summary = {"avg_latency_ms": 3.0, "timeouts_60s": 4}
        breaker.latencies = MagicMock()
        breaker.latencies.summary.return_value = main_summary
        read_window = MagicMock()
        read_window.summary.return_value = read_summary
        breaker.latencies_by_kind = {"read": read_window}
        with (
            patch.object(rate_module.time, "time", return_value=770.0),
            patch.object(rate_module.time, "monotonic", return_value=77.0),
        ):
            health = breaker.health()

        breaker.latencies.summary.assert_called_once_with(now=77.0)
        read_window.summary.assert_called_once_with(now=77.0)
        self.assertEqual(health["degraded_until"], 20.0)
        self.assertEqual(health["operations"], {"read": read_summary})
        self.assertEqual(health["avg_latency_ms"], 1.5)
        self.assertEqual(health["timeouts_60s"], 2)

    def test_protective_trigger_evidence_survives_recovery_and_timeout_pruning(self) -> None:
        breaker = DbusCircuitBreaker(protective_seconds=180.0)
        source = "com.example.energy/Ac/Power"
        with (
            patch.object(rate_module.time, "time", return_value=1000.0),
            patch.object(rate_module.time, "monotonic", return_value=100.0),
        ):
            for _index in range(6):
                breaker.record_error(
                    TimeoutError("sensitive timeout detail"),
                    kind="optional_read",
                    source=source,
                    latency_ms=750.0,
                )
            triggered = breaker.health()

        expected = {
            "triggered_at": 1000.0,
            "protective_until": 1180.0,
            "timeout_count_60s": 6,
            "operation_kind": "optional_read",
            "source": source,
            "error_code": "timeout",
            "latency_ms": 750.0,
        }
        self.assertEqual(triggered["active_protective_trigger"], expected)
        self.assertEqual(triggered["last_protective_trigger"], expected)
        self.assertNotIn("sensitive timeout detail", str(expected))

        with (
            patch.object(rate_module.time, "time", return_value=1010.0),
            patch.object(rate_module.time, "monotonic", return_value=110.0),
        ):
            breaker.record_success(2.0, kind="read", source="healthy/source")
            recovered = breaker.health()
        self.assertEqual(recovered["last_error"], "")
        self.assertEqual(recovered["active_protective_trigger"], expected)

        with (
            patch.object(rate_module.time, "time", return_value=1070.0),
            patch.object(rate_module.time, "monotonic", return_value=170.0),
        ):
            pruned = breaker.health()
        self.assertEqual(pruned["timeouts_60s"], 0)
        self.assertEqual(pruned["active_protective_trigger"], expected)

        with (
            patch.object(rate_module.time, "time", return_value=1180.0),
            patch.object(rate_module.time, "monotonic", return_value=280.0),
        ):
            expired = breaker.health()
        self.assertEqual(expired["state"], "ok")
        self.assertIsNone(expired["active_protective_trigger"])
        self.assertEqual(expired["last_protective_trigger"], expected)

    def test_circuit_source_attribution_slowdown_and_monotonic_deadlines(self) -> None:
        breaker = DbusCircuitBreaker()
        source = "svc.optional/Power"
        with (
            patch.object(rate_module.time, "time", return_value=1000.0),
            patch.object(rate_module.time, "monotonic", return_value=100.0),
        ):
            for latency_ms in (100.0, 150.0, 200.0):
                breaker.record_success(
                    latency_ms,
                    kind="optional_read",
                    source=source,
                )
            self.assertEqual(
                breaker.optional_source_interval_factor(source),
                1.0,
            )
            breaker.record_success(
                300.0,
                kind="optional_read",
                source=source,
            )
            self.assertEqual(
                breaker.optional_source_interval_factor(source),
                rate_module.OPTIONAL_SLOW_SOURCE_INTERVAL_FACTOR,
            )
            breaker.record_success(
                800.0,
                kind="optional_read",
                source=source,
            )
            self.assertEqual(
                breaker.optional_source_interval_factor(source),
                rate_module.OPTIONAL_VERY_SLOW_SOURCE_INTERVAL_FACTOR,
            )
            breaker.record_error(
                TimeoutError("source timeout"),
                kind="optional_read",
                source=source,
                latency_ms=1000.0,
            )
            breaker.record_error(
                RuntimeError("plain"),
                kind="optional_read",
                source=source,
                latency_ms=50.0,
            )
            breaker.record_optional_source_failure(
                TimeoutError("night source timeout"),
                source="svc.night/Power",
                latency_ms=900.0,
            )
            breaker.record_optional_source_failure(
                RuntimeError("optional plain"),
                source="svc.night/Power",
                latency_ms=60.0,
            )
            breaker.record_optional_source_failure(
                TimeoutError("unattributed"),
                source="",
                latency_ms=70.0,
            )
            health = breaker.health()

        source_health = health["operation_sources"]["optional_read"][source]
        self.assertEqual(source_health["samples_60s"], 7)
        self.assertEqual(source_health["timeouts_60s"], 1)
        night_health = health["operation_sources"]["optional_read"]["svc.night/Power"]
        self.assertEqual(night_health["samples_60s"], 2)
        self.assertEqual(night_health["timeouts_60s"], 1)
        self.assertEqual(health["operations"]["optional_read"]["samples_60s"], 10)
        self.assertEqual(health["errors_60s"], 2)

        deadlines = DbusCircuitBreaker()
        with (
            patch.object(rate_module.time, "time", return_value=2000.0),
            patch.object(rate_module.time, "monotonic", return_value=200.0),
        ):
            deadlines.degraded_until = 2010.0
            deadlines.protective_until = 2005.0
        self.assertEqual(deadlines.state(monotonic_at=204.0), "protective")
        self.assertEqual(deadlines.state(monotonic_at=205.0), "degraded")
        self.assertEqual(deadlines.state(monotonic_at=210.0), "ok")
        deadlines.degraded_until = 0.0
        deadlines.protective_until = 0.0
        self.assertEqual(deadlines.state(monotonic_at=0.0), "ok")

    def test_connection_manager_delegates_private_bus_and_resets_safely(self) -> None:
        manager = DbusConnectionManager()
        fake_bus = MagicMock()
        pending = object()
        fake_bus.call_async.return_value = pending
        reply = MagicMock()
        error = MagicMock()
        with patch.object(
            connection_module.dbus,
            "SystemBus",
            MagicMock(return_value=fake_bus),
        ) as system_bus:
            self.assertIs(manager.bus(), fake_bus)
            self.assertIs(manager.bus(), fake_bus)
            manager.connect()
            system_bus.assert_called_once_with(private=True)
            self.assertIs(
                manager.send_async(
                    DbusWireRequest(
                        service="svc",
                        path="/Path",
                        interface="interface",
                        method_name="SetValue",
                        signature="v",
                        timeout_seconds=0.75,
                        args=(1,),
                    ),
                    reply,
                    error,
                ),
                pending,
            )
            fake_bus.call_async.assert_called_once_with(
                "svc",
                "/Path",
                "interface",
                "SetValue",
                "v",
                (1,),
                reply,
                error,
                timeout=0.75,
                require_main_loop=True,
            )

        manager.reset()
        fake_bus.close.assert_called_once_with()
        self.assertIsNone(manager._bus)

        manager._bus = object()
        with self.assertRaisesRegex(TypeError, "^DBus bus does not provide call_async$"):
            manager.send_async(
                DbusWireRequest(
                    service="svc",
                    path="/Path",
                    interface="interface",
                    method_name="GetValue",
                    signature="",
                    timeout_seconds=1.0,
                ),
                reply,
                error,
            )

    def test_resource_monitor_reports_procfs_style_resource_health(self) -> None:
        reader = MagicMock()
        reader.system_cpu.side_effect = [(1000, 500), (1100, 540)]
        reader.process_cpu_seconds.side_effect = [1.0, 1.2]
        reader.meminfo.return_value = {
            "MemTotal": 1024.0 * 1024.0,
            "MemAvailable": 512.0 * 1024.0,
        }
        reader.process_status.return_value = {
            "VmRSS": 1234.0,
            "VmHWM": 2345.0,
            "Threads": 3.0,
            "FDSize": 64.0,
        }
        reader.open_fd_count.return_value = 7
        reader.load_average.return_value = (0.2, 0.3, 0.4)
        reader.cpu_count.return_value = 1
        monitor = ResourceMonitor(
            pid=123,
            settings=ResourceMonitorSettings(sample_interval_seconds=0.0),
            reader=reader,
            monotonic=MagicMock(side_effect=[10.0, 11.0]),
        )

        first = monitor.snapshot()
        second = monitor.snapshot()

        self.assertEqual(first["state"], "ok")
        self.assertEqual(first["process"]["open_fds"], 7)
        self.assertAlmostEqual(second["system_cpu_pct"], 60.0)
        self.assertAlmostEqual(second["process"]["cpu_pct_one_core"], 20.0)
        self.assertEqual(resource_state(1.1, 10.0, 100000.0), "busy")
        self.assertEqual(resource_state(0.1, 95.0, 100000.0), "constrained")
        self.assertEqual(resource_state(0.1, 10.0, 1000.0), "constrained")

    def test_tick_health_rolls_recent_tick_durations(self) -> None:
        health = TickHealth(window_seconds=10.0)
        health.record(duration_ms=5.0, expected_interval_s=1.0, now=100.0)
        health.record(duration_ms=2500.0, expected_interval_s=1.0, now=101.0)
        snapshot = health.snapshot(now=102.0)
        self.assertEqual(snapshot["tick_count_60s"], 2)
        self.assertEqual(snapshot["late_ticks_60s"], 1)
        self.assertEqual(snapshot["max_tick_gap_ms_60s"], 1000.0)
        self.assertEqual(snapshot["late_tick_gap_count_60s"], 0)
        health.record(duration_ms=1.0, expected_interval_s=1.0, now=104.5)
        self.assertEqual(health.snapshot(now=104.5)["late_tick_gap_count_60s"], 1)
        health.record(duration_ms=1.0, expected_interval_s=1.0, now=120.0)
        self.assertEqual(health.snapshot(now=120.0)["tick_count_60s"], 1)

    def test_connection_manager_creates_private_bus_and_resets_best_effort(self) -> None:
        manager = DbusConnectionManager()
        fake_bus = MagicMock()
        with patch.object(
            connection_module.dbus,
            "SystemBus",
            return_value=fake_bus,
        ) as system_bus:
            self.assertIs(manager.bus(), fake_bus)
            self.assertIs(manager.bus(), fake_bus)
            system_bus.assert_called_once_with(private=True)
        manager.reset()
        fake_bus.close.assert_called_once()
        self.assertIsNone(manager._bus)

        bad_bus = MagicMock()
        bad_bus.close.side_effect = RuntimeError("already closed")
        manager._bus = bad_bus
        manager.reset()
        self.assertIsNone(manager._bus)

        manager._bus = object()
        manager.reset()
        self.assertIsNone(manager._bus)
