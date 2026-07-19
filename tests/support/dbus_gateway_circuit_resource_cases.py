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
    builtins,
    install_mock,
    patch,
    rate_module,
    resource_module,
    unittest,
)


class GatewayCircuitResourceCases(GatewayAdapterContractCase):
    """Exercise circuit, connection, and resource contracts."""

    def test_circuit_breaker_states_priorities_and_timeout_detection(self) -> None:
        class _CustomDbusError(Exception):
            pass

        with patch.object(rate_module.dbus, "DBusException", _CustomDbusError, create=True):
            self.assertIs(rate_module._dbus_exception_type(), _CustomDbusError)
        with patch.object(rate_module.dbus, "DBusException", object, create=True):
            self.assertIs(rate_module._dbus_exception_type(), RuntimeError)
        with patch.object(rate_module.dbus, "DBusException", "bad", create=True):
            self.assertIs(rate_module._dbus_exception_type(), RuntimeError)
        with patch.object(rate_module, "dbus", object()):
            self.assertIs(rate_module._dbus_exception_type(), RuntimeError)
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

        with patch.object(rate_module.time, "time", return_value=1000.0):
            for _index in range(3):
                breaker.record_error(_NamedTimeout("slow"))
            self.assertEqual(breaker.state(now=1001.0), "degraded")
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

        with patch.object(rate_module.time, "time", return_value=1010.0):
            breaker.record_success(3.5, kind="write")
        self.assertEqual(breaker.last_success_at, 1010.0)
        self.assertEqual(breaker.last_error, "")
        self.assertEqual(breaker.consecutive_failures, 0)
        self.assertIn("write", breaker.latencies_by_kind)
        with patch.object(rate_module.time, "time", return_value=1011.0):
            breaker.record_success(4.5)
        self.assertIn("dbus", breaker.latencies_by_kind)
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
        with patch.object(rate_module.time, "time", return_value=3000.0):
            breaker.record_success(1.0, kind="")
            self.assertIn("dbus", breaker.latencies_by_kind)
            breaker.record_error(TimeoutError("timeout"))
            self.assertEqual(breaker.last_error, "timeout")
            self.assertIn(("dbus"), breaker.latencies_by_kind)
            self.assertGreaterEqual(breaker.health()["successes_60s"], 1)
        pruning_breaker = DbusCircuitBreaker()
        with patch.object(rate_module.time, "time", return_value=1.0):
            pruning_breaker.record_success(1.0)
            pruning_breaker.record_error(TimeoutError("timeout"))
        with patch.object(rate_module.time, "time", return_value=100.0):
            self.assertEqual(pruning_breaker.health()["successes_60s"], 0)
            self.assertEqual(pruning_breaker.health()["errors_60s"], 0)
        pruning_breaker = DbusCircuitBreaker()
        with patch.object(rate_module.time, "time", return_value=40.0):
            pruning_breaker.record_success(1.0)
            pruning_breaker.record_error(RuntimeError("plain"), kind=" write ")
        with patch.object(rate_module.time, "time", return_value=100.0):
            boundary_health = pruning_breaker.health()
        self.assertEqual(boundary_health["successes_60s"], 1)
        self.assertEqual(boundary_health["errors_60s"], 1)
        self.assertEqual(list(pruning_breaker._errors), [(40.0, "write")])
        self.assertEqual(list(pruning_breaker._successes), [(40.0, "dbus")])
        pruning_breaker = DbusCircuitBreaker()
        with patch.object(rate_module.time, "time", return_value=39.5):
            pruning_breaker.record_success(1.0)
            pruning_breaker.record_error(RuntimeError("plain"))
        with patch.object(rate_module.time, "time", return_value=40.0):
            pruning_breaker.record_success(1.0)
            pruning_breaker.record_error(RuntimeError("plain"))
        with patch.object(rate_module.time, "time", return_value=100.0):
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
        self.assertEqual(rate_module._dbus_error_name(_NamedDbusTimeout("Example.Error")), "example.error")
        self.assertEqual(rate_module._dbus_error_name(RuntimeError("plain")), "")
        self.assertEqual(rate_module._dbus_error_name(_NamedDbusTimeout(RuntimeError("bad"))), "bad")

        class _BrokenName(Exception):
            def get_dbus_name(self) -> str:
                raise RuntimeError("name unavailable")

        self.assertEqual(rate_module._dbus_error_name(_BrokenName("plain")), "")

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
            with patch.object(rate_module.time, "time", return_value=42.0):
                breaker.record_success(7.5)
        latency_window.record_latency.assert_called_once_with(7.5, now=42.0)
        kind_window.record_latency.assert_called_once_with(7.5, now=42.0)
        kind_window_factory.assert_called_once_with("dbus")
        self.assertEqual(list(breaker._successes), [(42.0, "dbus")])

        breaker = DbusCircuitBreaker()
        latency_window = MagicMock()
        kind_window = MagicMock()
        latency_window.summary.return_value = {"timeouts_60s": 1}
        breaker.latencies = latency_window
        with patch.object(breaker, "_kind_window", MagicMock(return_value=kind_window)) as kind_window_factory:
            with patch.object(rate_module.time, "time", return_value=50.0):
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
        with patch.object(rate_module.time, "time", return_value=77.0):
            health = breaker.health()

        breaker.latencies.summary.assert_called_once_with(now=77.0)
        read_window.summary.assert_called_once_with(now=77.0)
        self.assertEqual(health["degraded_until"], 20.0)
        self.assertEqual(health["operations"], {"read": read_summary})
        self.assertEqual(health["avg_latency_ms"], 1.5)
        self.assertEqual(health["timeouts_60s"], 2)

    def test_connection_manager_delegates_private_bus_and_resets_safely(self) -> None:
        manager = DbusConnectionManager()
        fake_bus = MagicMock()
        fake_bus.get_object.return_value = "object"
        with patch.object(rate_module.dbus, "SystemBus", MagicMock(return_value=fake_bus)) as system_bus:
            self.assertIs(manager.bus(), fake_bus)
            self.assertIs(manager.bus(), fake_bus)
            system_bus.assert_called_once_with(private=True)
            self.assertEqual(manager.get_object("svc", "/Path", introspect=True), "object")
            fake_bus.get_object.assert_called_once_with("svc", "/Path", introspect=True)
            fake_bus.get_object.reset_mock()
            self.assertEqual(manager.get_object("svc", "/Other"), "object")
            fake_bus.get_object.assert_called_once_with("svc", "/Other", introspect=False)

        manager.reset()
        fake_bus.close.assert_called_once_with()
        self.assertIsNone(manager._bus)

        manager._bus = object()
        with self.assertRaisesRegex(TypeError, "^DBus bus does not provide get_object$"):
            manager.get_object("svc", "/Path")

    def test_resource_monitor_reports_procfs_style_resource_health(self) -> None:
        monitor = ResourceMonitor(pid=123)
        install_mock(monitor, "_read_system_cpu", MagicMock(side_effect=[(1000, 500), (1100, 540)]))
        install_mock(monitor, "_read_process_cpu_seconds", MagicMock(side_effect=[1.0, 1.2]))
        install_mock(
            monitor,
            "_read_meminfo",
            MagicMock(return_value={"MemTotal": 1024.0 * 1024.0, "MemAvailable": 512.0 * 1024.0}),
        )
        install_mock(
            monitor,
            "_read_process_status",
            MagicMock(return_value={"VmRSS": 1234.0, "VmHWM": 2345.0, "Threads": 3.0, "FDSize": 64.0}),
        )
        install_mock(monitor, "_open_fd_count", MagicMock(return_value=7))
        install_mock(monitor, "_loadavg", MagicMock(return_value=(0.2, 0.3, 0.4)))

        with patch.object(resource_module.time, "monotonic", side_effect=[10.0, 11.0]):
            first = monitor.snapshot()
            second = monitor.snapshot()

        self.assertEqual(first["state"], "ok")
        self.assertEqual(first["process"]["open_fds"], 7)
        self.assertAlmostEqual(second["system_cpu_pct"], 60.0)
        self.assertAlmostEqual(second["process"]["cpu_pct_one_core"], 20.0)
        self.assertEqual(resource_module.resource_state(1.1, 10.0, 100000.0), "busy")
        self.assertEqual(resource_module.resource_state(0.1, 95.0, 100000.0), "constrained")
        self.assertEqual(resource_module.resource_state(0.1, 10.0, 1000.0), "constrained")

    def test_resource_monitor_procfs_failure_edges(self) -> None:
        monitor = ResourceMonitor(pid=123)
        with patch.object(resource_module.os, "getloadavg", side_effect=OSError("missing")):
            self.assertEqual(monitor._loadavg(), (0.0, 0.0, 0.0))
        with patch.object(builtins, "open", side_effect=OSError("missing")):
            self.assertEqual(monitor._read_system_cpu(), (0, 0))
            self.assertEqual(monitor._read_process_cpu_seconds(), 0.0)
            self.assertEqual(monitor._read_meminfo(), {})
            self.assertEqual(monitor._read_process_status(), {})
        read_data = "Name:\tpython\nThreads:\t3\nVmRSS:\tbad kB\n"
        with patch.object(builtins, "open", unittest.mock.mock_open(read_data=read_data)):
            self.assertEqual(monitor._read_process_status(), {"Threads": 3.0})
        with patch.object(resource_module.os, "listdir", side_effect=OSError("missing")):
            self.assertEqual(monitor._open_fd_count(), 0)

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
        with patch.object(rate_module.dbus, "SystemBus", return_value=fake_bus) as system_bus:
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
