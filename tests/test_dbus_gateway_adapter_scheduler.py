# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import tempfile
import time
import sys
import unittest
import json
import builtins
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

from venus_evcharger.dbus_gateway import DbusCommandInbox, gateway_paths, read_json_file

fake_vedbus = ModuleType("vedbus")


class _FakeVeDbusService(dict):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__()
        self.args = args
        self.kwargs = kwargs
        self.registered = False
        self.added_paths: dict[str, dict[str, object]] = {}

    def register(self) -> None:
        self.registered = True

    def add_path(self, path: str, value: object, **kwargs: object) -> None:
        self.added_paths[path] = {"value": value, **kwargs}
        self[path] = value


fake_vedbus.VeDbusService = _FakeVeDbusService  # type: ignore[attr-defined]
fake_dbus_mainloop = ModuleType("dbus.mainloop.glib")
fake_dbus_mainloop.DBusGMainLoop = MagicMock()  # type: ignore[attr-defined]

with patch.dict("sys.modules", {"vedbus": fake_vedbus, "dbus.mainloop.glib": fake_dbus_mainloop}):
    from venus_evcharger_dbus_adapter import (
        DbusAdapter,
        DbusCircuitBreaker,
        DbusConnectionManager,
        DbusDiscoveryManager,
        DbusOperationDeferred,
        DbusRateLimiter,
        DbusReadScheduler,
        ResourceMonitor,
        TickHealth,
    )
    adapter_module = sys.modules["venus_evcharger_dbus_adapter"]
    adapter_process_module = sys.modules["venus_evcharger.dbus_adapter_process"]
    write_module = sys.modules["venus_evcharger.dbus_adapter_write"]


class DbusGatewayAdapterSchedulerTests(unittest.TestCase):
    def test_coalesced_commands_use_stable_filename_and_latest_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = DbusCommandInbox(str(Path(temp_dir) / "commands"))
            first = inbox.enqueue({"kind": "set_value", "value": 1, "created_at": 10.0, "coalesce_key": "ev:/Mode"})
            second = inbox.enqueue({"kind": "set_value", "value": 2, "created_at": 20.0, "coalesce_key": "ev:/Mode"})

            self.assertEqual(first, second)
            pending = inbox.load_pending()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0][1]["value"], 2)
            self.assertEqual(pending[0][1]["created_at"], 10.0)
            self.assertEqual(pending[0][1]["lifecycle_state"], "coalesced")
            self.assertGreater(pending[0][1]["updated_at"], 0.0)
            self.assertTrue(Path(first).name.startswith("coalesced-"))

    def test_coalesce_key_overrides_explicit_command_id_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = DbusCommandInbox(str(Path(temp_dir) / "commands"))
            first = inbox.enqueue({"id": "manual-a", "kind": "set_value", "value": 1, "coalesce_key": "ev:/Mode"})
            second = inbox.enqueue({"id": "manual-b", "kind": "set_value", "value": 2, "coalesce_key": "ev:/Mode"})

            self.assertEqual(first, second)
            self.assertTrue(Path(first).name.startswith("coalesced-"))
            self.assertFalse((Path(temp_dir) / "commands" / "manual-a.json").exists())
            self.assertFalse((Path(temp_dir) / "commands" / "manual-b.json").exists())

    def test_coalesced_physical_file_keeps_higher_priority_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = DbusCommandInbox(str(Path(temp_dir) / "commands"))
            path = inbox.enqueue(
                {"kind": "set_value", "value": "off", "priority": "safety", "coalesce_key": "relay:/StartStop"}
            )
            same_path = inbox.enqueue(
                {"kind": "set_value", "value": "on", "priority": "diagnostic", "coalesce_key": "relay:/StartStop"}
            )

            self.assertEqual(path, same_path)
            self.assertEqual(inbox.load_pending()[0][1]["value"], "off")

            inbox.enqueue({"kind": "set_value", "value": "manual", "priority": "user", "coalesce_key": "relay:/StartStop"})
            self.assertEqual(inbox.load_pending()[0][1]["value"], "off")

            inbox.enqueue(
                {"kind": "set_value", "value": "new-off", "priority": "safety", "coalesce_key": "relay:/StartStop"}
            )
            self.assertEqual(inbox.load_pending()[0][1]["value"], "new-off")

    def test_rate_limiter_defers_without_sleeping(self) -> None:
        limiter = DbusRateLimiter(read_interval_seconds=10.0)
        limiter.require_due("read")
        with self.assertRaises(DbusOperationDeferred):
            limiter.require_due("read")
        limiter.mark("read", now=1.0)
        self.assertFalse(limiter.due("read", now=2.0))

    def test_circuit_breaker_states_priorities_and_timeout_detection(self) -> None:
        breaker = DbusCircuitBreaker(degraded_seconds=30.0, protective_seconds=60.0)
        self.assertEqual(breaker.state(now=100.0), "ok")
        self.assertTrue(breaker.allows_priority("diagnostic"))

        class _NamedTimeout(Exception):
            def get_dbus_name(self) -> str:
                return "org.freedesktop.DBus.Error.NoReply"

        class _BrokenName(Exception):
            def get_dbus_name(self) -> str:
                raise RuntimeError("name unavailable")

        with patch.object(adapter_module.time, "time", return_value=1000.0):
            for _index in range(3):
                breaker.record_error(_NamedTimeout("slow"))
            self.assertEqual(breaker.state(now=1001.0), "degraded")
            self.assertFalse(breaker.allows_priority("discovery"))
            self.assertTrue(breaker.allows_priority("optional"))
            for _index in range(3):
                breaker.record_error(TimeoutError("timeout"))
            self.assertEqual(breaker.state(now=1001.0), "protective")
            self.assertFalse(breaker.allows_priority("optional"))
            self.assertTrue(breaker.allows_priority("read"))

        breaker.record_success(3.5)
        breaker.record_error(_BrokenName("plain"))
        health = breaker.health()
        self.assertIn("avg_latency_ms", health)
        self.assertIn("operations", health)
        self.assertEqual(health["consecutive_failures"], 1)
        self.assertEqual(breaker.state(now=2000.0), "ok")
        with patch.object(adapter_module.time, "time", return_value=3000.0):
            breaker.record_success(1.0)
            breaker._successes.append((2000.0, 1.0))
            self.assertGreaterEqual(breaker.health()["successes_60s"], 1)
        pruning_breaker = DbusCircuitBreaker()
        pruning_breaker._successes.append((1.0, 1.0))
        pruning_breaker._prune_events(100.0)
        self.assertEqual(list(pruning_breaker._successes), [])

    def test_resource_monitor_reports_procfs_style_resource_health(self) -> None:
        monitor = ResourceMonitor(pid=123)
        monitor._read_system_cpu = MagicMock(side_effect=[(1000, 500), (1100, 540)])  # type: ignore[method-assign]
        monitor._read_process_cpu_seconds = MagicMock(side_effect=[1.0, 1.2])  # type: ignore[method-assign]
        monitor._read_meminfo = MagicMock(  # type: ignore[method-assign]
            return_value={"MemTotal": 1024.0 * 1024.0, "MemAvailable": 512.0 * 1024.0}
        )
        monitor._read_process_status = MagicMock(  # type: ignore[method-assign]
            return_value={"VmRSS": 1234.0, "VmHWM": 2345.0, "Threads": 3.0, "FDSize": 64.0}
        )
        monitor._open_fd_count = MagicMock(return_value=7)  # type: ignore[method-assign]
        monitor._loadavg = MagicMock(return_value=(0.2, 0.3, 0.4))  # type: ignore[method-assign]

        with patch.object(adapter_module.time, "monotonic", side_effect=[10.0, 11.0]):
            first = monitor.snapshot()
            second = monitor.snapshot()

        self.assertEqual(first["state"], "ok")
        self.assertEqual(first["process"]["open_fds"], 7)
        self.assertAlmostEqual(second["system_cpu_pct"], 60.0)
        self.assertAlmostEqual(second["process"]["cpu_pct_one_core"], 20.0)
        self.assertEqual(ResourceMonitor._resource_state(1.1, 10.0, 100000.0), "busy")
        self.assertEqual(ResourceMonitor._resource_state(0.1, 95.0, 100000.0), "constrained")
        self.assertEqual(ResourceMonitor._resource_state(0.1, 10.0, 1000.0), "constrained")

    def test_resource_monitor_procfs_failure_edges(self) -> None:
        monitor = ResourceMonitor(pid=123)
        with patch.object(adapter_module.os, "getloadavg", side_effect=OSError("missing")):
            self.assertEqual(monitor._loadavg(), (0.0, 0.0, 0.0))
        with patch.object(builtins, "open", side_effect=OSError("missing")):
            self.assertEqual(monitor._read_system_cpu(), (0, 0))
            self.assertEqual(monitor._read_process_cpu_seconds(), 0.0)
            self.assertEqual(monitor._read_meminfo(), {})
            self.assertEqual(monitor._read_process_status(), {})
        read_data = "Name:\tpython\nThreads:\t3\nVmRSS:\tbad kB\n"
        with patch.object(builtins, "open", unittest.mock.mock_open(read_data=read_data)):
            self.assertEqual(monitor._read_process_status(), {"Threads": 3.0})
        with patch.object(adapter_module.os, "listdir", side_effect=OSError("missing")):
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
        with patch.object(adapter_module.dbus, "SystemBus", return_value=fake_bus) as system_bus:
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

    def test_read_scheduler_tracks_due_reads_and_degraded_intervals(self) -> None:
        scheduler = DbusReadScheduler({"grid": {"interval": 2.0, "priority": "read"}})

        due = scheduler.next_due(now=10.0, circuit_state="degraded", priority_allowed=lambda _priority: True)
        self.assertIsNotNone(due)
        assert due is not None
        key, _spec, interval = due
        self.assertEqual(key, "grid")
        self.assertEqual(interval, 6.0)

        scheduler.record_success(key, now=10.0, interval=interval)
        self.assertIsNone(scheduler.next_due(now=15.0, circuit_state="ok", priority_allowed=lambda _priority: True))
        self.assertIsNotNone(scheduler.next_due(now=16.0, circuit_state="ok", priority_allowed=lambda _priority: True))
        scheduler.record_error("grid", now=20.0, interval=2.0)
        self.assertIsNone(scheduler.next_due(now=21.0, circuit_state="ok", priority_allowed=lambda _priority: True))
        self.assertEqual(DbusReadScheduler._effective_interval({"interval": 2.0}, "protective"), 10.0)
        blocked = DbusReadScheduler({"grid": {"interval": 1.0, "priority": "discovery"}})
        self.assertIsNone(blocked.next_due(now=1.0, circuit_state="ok", priority_allowed=lambda _priority: False))

        fair = DbusReadScheduler(
            {
                "grid": {"interval": 2.0, "priority": "read"},
                "pv": {"interval": 2.0, "priority": "read"},
                "battery": {"interval": 2.0, "priority": "read"},
            }
        )
        fair.next_read_at = {"grid": 100.0, "pv": 90.0, "battery": 0.0}
        due = fair.next_due(now=100.0, circuit_state="ok", priority_allowed=lambda _priority: True)
        self.assertIsNotNone(due)
        assert due is not None
        self.assertEqual(due[0], "battery")

    def test_discovery_manager_tracks_success_and_error_backoff(self) -> None:
        discovery = DbusDiscoveryManager(interval_seconds=900.0)

        self.assertTrue(discovery.due(now=100.0, priority_allowed=lambda _priority: True))
        discovery.record_success(now=100.0)
        self.assertEqual(discovery.last_success_at, 100.0)
        self.assertEqual(discovery.next_scan_at, 1000.0)
        self.assertFalse(discovery.due(now=999.0, priority_allowed=lambda _priority: True))

        discovery.record_error(RuntimeError("dbus down"), now=200.0)
        self.assertEqual(discovery.last_error, "dbus down")
        self.assertEqual(discovery.next_scan_at, 260.0)
        self.assertFalse(discovery.due(now=260.0, priority_allowed=lambda _priority: False))

    def test_read_scheduler_errors_back_off_and_success_resets_failure_count(self) -> None:
        scheduler = DbusReadScheduler({"pv": {"interval": 2.0, "priority": "read"}})
        scheduler.record_error("pv", now=100.0, interval=2.0)
        self.assertEqual(scheduler.failure_counts["pv"], 1)
        self.assertEqual(scheduler.next_read_at["pv"], 130.0)
        scheduler.record_error("pv", now=130.0, interval=2.0)
        self.assertEqual(scheduler.failure_counts["pv"], 2)
        self.assertEqual(scheduler.next_read_at["pv"], 190.0)
        scheduler.record_success("pv", now=200.0, interval=2.0)
        self.assertEqual(scheduler.failure_counts["pv"], 0)
        self.assertEqual(scheduler.next_read_at["pv"], 202.0)
        scheduler.force_due(["missing"])
        self.assertNotIn("missing", scheduler.next_read_at)

    def test_adapter_static_config_helpers_cover_defaults_and_invalid_instance(self) -> None:
        parser = adapter_module.configparser.ConfigParser()
        parser["DEFAULT"] = {
            "ServiceName": "com.example.ev",
            "DeviceInstance": "bad",
            "AutoGridL2Path": "",
            "AutoPvService": "pv.fixed",
            "AutoBatteryService": "com.victronenergy.battery.example",
            "AutoBatteryServicePrefix": "com.victronenergy.battery",
            "AutoBatterySocPath": "/Soc",
        }
        self.assertEqual(DbusAdapter._evcharger_service_name(parser["DEFAULT"]), "com.example.ev.http_60")
        specs = DbusAdapter._configured_read_specs(parser["DEFAULT"])
        self.assertEqual(specs["grid_power_w"]["paths"], ["/Ac/Grid/L1/Power", "/Ac/Grid/L3/Power"])
        self.assertEqual(specs["pv_power_w"]["service"], "pv.fixed")
        self.assertEqual(specs["battery_soc"]["service"], "")
        self.assertEqual(specs["battery_soc"]["prefix"], "com.victronenergy.battery")
        self.assertEqual(specs["battery_soc"]["aggregate"], "first-service")
        self.assertEqual(specs["battery_soc"]["path"], "/Soc")
        self.assertEqual(DbusAdapter._device_instance(parser["DEFAULT"]), 60)
        with self.assertRaises(ValueError):
            DbusAdapter._load_config("/tmp/does-not-exist-venus-evcharger.ini")

    def test_publish_desired_processes_one_path_per_tick(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nDbusGatewayLocalPublishBurstLimit=1\n", encoding="utf-8")
            paths = gateway_paths(str(Path(temp_dir) / "run"))
            adapter = DbusAdapter(str(config_path), paths=paths)
            writes: list[tuple[str, object]] = []

            class _FakeDbusService(dict):
                def __setitem__(self, key: str, value: object) -> None:
                    writes.append((key, value))
                    super().__setitem__(key, value)

            adapter._dbusservice = _FakeDbusService()
            adapter.write_scheduler.registered_paths.update({"/A", "/B"})
            command_path = adapter.commands.enqueue(
                {
                    "kind": "publish_desired",
                    "paths": {"/A": 1, "/B": 2},
                    "coalesce_key": "publish-batch",
                    "priority": "publish",
                }
            )

            self.assertTrue(adapter.write_scheduler.process_one())
            self.assertEqual(writes, [("/A", 1)])
            remaining = read_json_file(command_path, {})
            self.assertEqual(remaining["paths"], {"/B": 2})

            adapter.rate_limiter.next_at["write"] = time.monotonic()
            self.assertTrue(adapter.write_scheduler.process_one())
            self.assertEqual(writes, [("/A", 1), ("/B", 2)])
            self.assertFalse(Path(command_path).exists())

    def test_publish_desired_bursts_local_evcs_paths_without_remote_rate_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nDbusGatewayLocalPublishBurstLimit=3\n", encoding="utf-8")
            paths = gateway_paths(str(Path(temp_dir) / "run"))
            adapter = DbusAdapter(str(config_path), paths=paths)
            writes: list[tuple[str, object]] = []

            class _FakeDbusService(dict):
                def __setitem__(self, key: str, value: object) -> None:
                    writes.append((key, value))
                    super().__setitem__(key, value)

            adapter._dbusservice = _FakeDbusService()
            adapter.write_scheduler.registered_paths.update({"/A", "/B", "/C", "/D"})
            command_path = adapter.commands.enqueue(
                {
                    "kind": "publish_desired",
                    "paths": {"/A": 1, "/B": 2, "/C": 3, "/D": 4},
                    "coalesce_key": "publish-batch",
                    "priority": "publish",
                }
            )

            self.assertTrue(adapter.write_scheduler.process_one())
            self.assertEqual(writes, [("/A", 1), ("/B", 2), ("/C", 3)])
            self.assertEqual(read_json_file(command_path, {})["paths"], {"/D": 4})
            self.assertEqual(adapter.write_scheduler.health(now=time.time())["processed_commands_60s"], 0)

            self.assertTrue(adapter.write_scheduler.process_one())
            self.assertEqual(writes, [("/A", 1), ("/B", 2), ("/C", 3), ("/D", 4)])
            self.assertFalse(Path(command_path).exists())
            self.assertEqual(adapter.write_scheduler.health(now=time.time())["processed_commands_60s"], 1)

    def test_write_scheduler_registers_paths_gui_writes_and_command_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            self.assertEqual(adapter.write_scheduler.process_command({"kind": "register_service"}), "applied")
            self.assertTrue(adapter._dbusservice.registered)
            outcome = adapter.write_scheduler.process_command(
                {"kind": "register_path", "path": "/Mode", "value": 1, "writeable": True}
            )
            self.assertEqual(outcome, "applied")
            self.assertIn("/Mode", adapter.write_scheduler.registered_paths)
            self.assertEqual(adapter.write_scheduler.process_command({"kind": "register_path", "path": "/Mode"}), "applied")
            self.assertTrue(adapter.write_scheduler.handle_gui_write("/Mode", 2))
            self.assertEqual(adapter.core_commands.load_pending()[0][1]["value"], 2)

            self.assertEqual(adapter.write_scheduler.publish_command({"kind": "publish_desired", "paths": []}), "dropped")
            self.assertEqual(adapter.write_scheduler.publish_command({"kind": "publish_desired", "paths": {}}), "applied")
            self.assertEqual(adapter.write_scheduler.publish_command({"kind": "publish_desired", "paths": {"/Missing": 1}}), "dropped")
            adapter.write_scheduler.local_publish_burst_limit = 1
            adapter.write_scheduler.registered_paths.update({"/A", "/B"})
            self.assertEqual(
                adapter.write_scheduler.publish_command({"kind": "publish_desired", "paths": {"/A": 1, "/B": 2}}),
                "deferred",
            )
            self.assertEqual(adapter._dbusservice["/A"], 1)
            self.assertEqual(adapter.write_scheduler.publish_path("", 1), "applied")
            self.assertEqual(adapter.write_scheduler.publish_path("/Missing", 1), "dropped")
            self.assertEqual(adapter.write_scheduler.publish_command({"kind": "publish_value", "path": "/Missing", "value": 1}), "dropped")
            self.assertEqual(adapter.write_scheduler.process_command({"kind": "unknown"}), "dropped")
            self.assertEqual(adapter.write_scheduler.process_command({"kind": "set_value"}), "dropped")
            self.assertFalse(adapter.write_scheduler._is_expired({"deadline_s": "bad"}))
            adapter.write_scheduler.drop_stale_coalesced_commands("/tmp/none", {})
            processed = Path(adapter.paths.command_dir) / "processed.json"
            stale = Path(adapter.paths.command_dir) / "stale.json"
            stale.parent.mkdir(parents=True, exist_ok=True)

            processed.write_text(json.dumps({"kind": "publish_value", "coalesce_key": "same"}), encoding="utf-8")
            stale.write_text(json.dumps({"kind": "publish_value", "coalesce_key": "same"}), encoding="utf-8")
            adapter.write_scheduler.drop_stale_coalesced_commands(str(processed), {"coalesce_key": "same"})
            self.assertTrue(processed.exists())
            self.assertFalse(stale.exists())

            commands = [
                ("fresh-publish", {"priority": "publish", "created_at": 990.0}),
                ("old-discovery", {"priority": "discovery", "created_at": 900.0}),
            ]
            self.assertEqual(adapter.write_scheduler._select_next_command(commands)[0], "fresh-publish")
            protected = [("fresh-user", {"priority": "user", "created_at": 999.0}), *commands]
            self.assertEqual(adapter.write_scheduler._select_next_command(protected)[0], "fresh-user")
            adapter.write_scheduler.queue_class_budgets["diagnostic"] = 0
            self.assertIsNone(adapter.write_scheduler._select_next_command([("diag", {"kind": "unknown"})]))
            self.assertFalse(adapter.write_scheduler._budget_available({"queue_class": "diagnostic"}, time.time()))
            adapter.write_scheduler.queue_class_budgets["diagnostic"] = 1
            adapter.commands.enqueue(
                {
                    "kind": "publish_value",
                    "path": "/Mode",
                    "value": 1,
                    "priority": "publish",
                    "coalesce_key": "publish:/Mode",
                }
            )
            self.assertFalse(adapter.write_scheduler.process_one(include_local_publish=False))
            for command_path, _command in adapter.commands.load_pending():
                adapter.commands.remove(command_path)

            publish_burst = DbusCommandInbox.coalesce(
                [
                    (
                        "old-auto",
                        {
                            "id": "old-auto",
                            "kind": "publish_value",
                            "priority": "publish",
                            "path": "/Auto/DbusBackoffBaseSeconds",
                            "created_at": 1.0,
                            "coalesce_key": "publish:/Auto/DbusBackoffBaseSeconds",
                        },
                    ),
                    (
                        "fresh-session",
                        {
                            "id": "fresh-session",
                            "kind": "publish_value",
                            "priority": "publish",
                            "path": "/Session/Time",
                            "created_at": 2.0,
                            "coalesce_key": "publish:/Session/Time",
                        },
                    ),
                    (
                        "old-l2-energy",
                        {
                            "id": "old-l2-energy",
                            "kind": "publish_value",
                            "priority": "publish",
                            "path": "/Ac/L2/Energy/Forward",
                            "created_at": 0.5,
                            "coalesce_key": "publish:/Ac/L2/Energy/Forward",
                        },
                    ),
                ]
            )
            self.assertEqual(adapter.write_scheduler._select_next_command(publish_burst)[0], "old-l2-energy")
            self.assertIsNone(
                adapter.write_scheduler._select_next_command(publish_burst, include_local_publish=False)
            )
            adapter.write_scheduler._budget_events.append((0.0, "local-publish"))
            adapter.write_scheduler._prune_budget(time.time())
            self.assertEqual(adapter.write_scheduler._queue_class_usage_1s(), {})
            adapter.write_scheduler._processed_events.append(0.0)
            adapter.write_scheduler._prune_processed(time.time())
            self.assertEqual(adapter.write_scheduler.health(now=time.time())["processed_commands_60s"], 0)
            adapter.write_scheduler._lifecycle_events.append((0.0, "applied", "local-publish"))
            adapter.write_scheduler._prune_lifecycle(time.time())
            self.assertEqual(adapter.write_scheduler._lifecycle_counts_60s(), {})
            self.assertEqual(write_module._float_or_zero(object()), 0.0)

    def test_adapter_registers_identity_paths_before_service_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                "Host=192.0.2.10\n"
                "DeviceInstance=77\n"
                "ProductName=Test EVCS\n"
                "CustomName=Garage\n"
                "Connection=Shelly RPC\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            adapter._ensure_dbus_service()

            self.assertFalse(adapter._dbusservice.registered)
            self.assertEqual(adapter._dbusservice["/DeviceInstance"], 77)
            self.assertEqual(adapter._dbusservice["/ProductName"], "Test EVCS")
            self.assertEqual(adapter._dbusservice["/CustomName"], "Garage")
            self.assertEqual(adapter._dbusservice["/Connected"], 1)
            self.assertEqual(adapter._dbusservice["/Mgmt/Connection"], "Shelly RPC")
            self.assertIn("/DeviceInstance", adapter.write_scheduler.registered_paths)

            adapter._register_dbus_service_name()
            adapter._register_dbus_service_name()

            self.assertTrue(adapter._dbusservice.registered)

    def test_startup_registration_batch_registers_paths_before_service_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nHost=192.0.2.10\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.commands.enqueue(
                {
                    "kind": "register_path",
                    "path": "/Mode",
                    "value": 0,
                    "writeable": True,
                    "coalesce_key": "register:/Mode",
                    "priority": "publish",
                }
            )
            adapter.commands.enqueue(
                {
                    "kind": "register_path",
                    "path": "/StartStop",
                    "value": 1,
                    "writeable": True,
                    "coalesce_key": "register:/StartStop",
                    "priority": "publish",
                }
            )
            adapter.commands.enqueue(
                {
                    "kind": "register_service",
                    "coalesce_key": "register-service",
                    "priority": "publish",
                }
            )

            self.assertTrue(adapter.write_scheduler.process_one())

            self.assertTrue(adapter._dbusservice.registered)
            self.assertEqual(adapter._dbusservice["/Mode"], 0)
            self.assertEqual(adapter._dbusservice["/StartStop"], 1)
            self.assertEqual(adapter.commands.load_pending(), [])

    def test_startup_registration_batch_honors_limit_before_registering_service(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nDbusGatewayStartupRegistrationBatchLimit=2\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            for path in ("/Mode", "/StartStop", "/Status"):
                adapter.commands.enqueue(
                    {
                        "kind": "register_path",
                        "path": path,
                        "value": 0,
                        "coalesce_key": f"register:{path}",
                        "priority": "publish",
                    }
                )
            adapter.commands.enqueue({"kind": "register_service", "coalesce_key": "register-service", "priority": "publish"})

            self.assertTrue(adapter.write_scheduler.process_one())
            self.assertFalse(adapter._dbusservice.registered)
            self.assertIn("/Mode", adapter.write_scheduler.registered_paths)
            self.assertIn("/StartStop", adapter.write_scheduler.registered_paths)
            self.assertNotIn("/Status", adapter.write_scheduler.registered_paths)

            self.assertTrue(adapter.write_scheduler.process_one())
            self.assertTrue(adapter._dbusservice.registered)
            self.assertIn("/Status", adapter.write_scheduler.registered_paths)
            self.assertEqual(adapter.commands.load_pending(), [])

    def test_write_scheduler_process_one_defers_on_priority_and_exceptions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.circuit.protective_until = time.time() + 10
            path = adapter.commands.enqueue({"kind": "refresh_services", "priority": "diagnostic"})

            self.assertTrue(adapter.write_scheduler.process_one())
            self.assertTrue(Path(path).exists())

            adapter.circuit.protective_until = 0
            adapter.write_scheduler.process_command = MagicMock(side_effect=DbusOperationDeferred("write"))  # type: ignore[method-assign]
            self.assertTrue(adapter.write_scheduler.process_one())
            self.assertTrue(Path(path).exists())

            adapter.write_scheduler.process_command = MagicMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
            self.assertTrue(adapter.write_scheduler.process_one())
            self.assertTrue(Path(path).exists())

            empty_adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "empty-run")))
            self.assertFalse(empty_adapter.write_scheduler.process_one())
            empty_adapter.write_scheduler._record_lifecycle({"kind": "noop"}, "queued")
            self.assertEqual(empty_adapter.write_scheduler.health(now=time.time())["lifecycle_counts"]["queued"], 1)
            empty_adapter.command_lifecycle_path = ""
            empty_adapter.write_scheduler._record_lifecycle({"kind": "noop"}, "dropped")
            bad_lifecycle = Path(temp_dir) / "bad-lifecycle.jsonl"
            empty_adapter.command_lifecycle_path = str(bad_lifecycle)
            with patch.object(builtins, "open", side_effect=OSError("full")):
                empty_adapter.write_scheduler._record_lifecycle({"kind": "noop"}, "dropped")
            empty_adapter.command_lifecycle_path = "lifecycle-without-dir.jsonl"
            lifecycle_handle = unittest.mock.mock_open()
            with patch.object(write_module.os.path, "dirname", return_value=""), patch.object(
                builtins, "open", lifecycle_handle
            ):
                empty_adapter.write_scheduler._record_lifecycle({"kind": "noop"}, "queued")
            lifecycle_handle.assert_called_once_with("lifecycle-without-dir.jsonl", "a", encoding="utf-8")

    def test_local_publish_burst_can_run_before_non_local_scheduler_slot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\nDbusGatewayLocalPublishBurstLimit=2\nDbusIntrospectionEnabled=0\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.cache.update_services(["svc"])
            adapter._dbusservice_registered = True
            adapter.write_scheduler.registered_paths.update({"/Session/Time", "/Ac/Power"})
            writes: list[tuple[str, object]] = []

            class _FakeDbusService(dict):
                def __setitem__(self, key: str, value: object) -> None:
                    writes.append((key, value))
                    super().__setitem__(key, value)

            adapter._dbusservice = _FakeDbusService()
            adapter.commands.enqueue(
                {
                    "kind": "publish_value",
                    "path": "/Session/Time",
                    "value": 10,
                    "coalesce_key": "publish:/Session/Time",
                    "priority": "publish",
                }
            )
            adapter.commands.enqueue(
                {
                    "kind": "publish_value",
                    "path": "/Ac/Power",
                    "value": 2000,
                    "coalesce_key": "publish:/Ac/Power",
                    "priority": "publish",
                }
            )
            adapter.commands.enqueue({"kind": "refresh_services", "priority": "read"})
            adapter._poll_one_due_read_once = MagicMock(return_value=False)  # type: ignore[method-assign]
            adapter._refresh_services_if_due_once = MagicMock(return_value=False)  # type: ignore[method-assign]
            adapter._enqueue_background_introspection_if_due = MagicMock()  # type: ignore[method-assign]
            adapter.discovery.refresh_services = MagicMock(return_value=["svc"])  # type: ignore[method-assign]

            self.assertTrue(adapter._process_one_dbus_operation_once())

            self.assertCountEqual(writes, [("/Ac/Power", 2000), ("/Session/Time", 10)])
            self.assertEqual(adapter.commands.load_pending(), [])
            self.assertGreaterEqual(adapter.write_scheduler.health(now=time.time())["processed_commands_60s"], 3)

    def test_write_scheduler_set_remote_value_uses_dbus_and_updates_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            fake_iface = MagicMock()
            fake_bus = MagicMock()
            fake_bus.get_object.return_value = object()
            adapter.connection.bus = MagicMock(return_value=fake_bus)  # type: ignore[method-assign]

            with patch.object(adapter_module.dbus, "Interface", return_value=fake_iface):
                outcome = adapter.write_scheduler.set_remote_value(
                    {"service": "svc", "path": "/Set", "value": 9, "timeout": 2.0}
                )

            self.assertEqual(outcome, "applied")
            fake_iface.SetValue.assert_called_once_with(9, timeout=2.0)
            self.assertEqual(adapter.cache.values["path:svc/Set"]["value"], 9)

    def test_fast_pv_poll_uses_cached_services_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nAutoPvServicePrefix=com.victronenergy.pvinverter\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.cache.update_services(["com.victronenergy.pvinverter.http_1"])
            calls: list[tuple[str, str]] = []

            def fake_read(service: str, path: str) -> float:
                calls.append((service, path))
                return 123.0

            adapter.read_executor._read_busitem_now = fake_read  # type: ignore[method-assign]

            self.assertEqual(
                adapter.read_executor.poll_read_spec(
                    "pv",
                    {"aggregate": "services-sum", "prefix": "com.victronenergy.pvinverter", "path": "/Ac/Power"},
                ),
                "applied",
            )
            self.assertEqual(calls, [("com.victronenergy.pvinverter.http_1", "/Ac/Power")])
            self.assertEqual(adapter.cache.values["pv"]["value"], 123.0)

    def test_first_service_read_uses_discovered_battery_service(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.cache.update_services(["com.victronenergy.battery.socketcan_can1"])
            adapter.read_executor.read_busitem = MagicMock(return_value=74.0)  # type: ignore[method-assign]

            self.assertEqual(
                adapter.read_executor.poll_read_spec(
                    "battery_soc",
                    {
                        "aggregate": "first-service",
                        "prefix": "com.victronenergy.battery",
                        "path": "/Soc",
                    },
                ),
                "applied",
            )
            adapter.read_executor.read_busitem.assert_called_once_with("com.victronenergy.battery.socketcan_can1", "/Soc")
            self.assertEqual(adapter.cache.values["battery_soc"]["value"], 74.0)

    def test_read_executor_covers_refresh_sum_error_and_direct_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.rate_limiter.intervals["read"] = 0.0
            values = {
                ("svc", "/L1"): 1.5,
                ("svc", "/L2"): None,
                ("svc", "/Path"): 7,
            }
            adapter.read_executor._read_busitem_now = lambda service, path: values.get((service, path), 0.0)  # type: ignore[method-assign]
            adapter.read_executor.read_busitem = lambda service, path: values.get((service, path), 0.0)  # type: ignore[method-assign]

            self.assertEqual(
                adapter.read_executor.poll_read_spec("sum", {"aggregate": "sum", "service": "svc", "paths": ["/L1", "/L2"]}),
                "deferred",
            )
            adapter.rate_limiter.next_at["read"] = time.monotonic()
            self.assertEqual(
                adapter.read_executor.poll_read_spec("sum", {"aggregate": "sum", "service": "svc", "paths": ["/L1", "/L2"]}),
                "applied",
            )
            self.assertEqual(adapter.cache.values["sum"]["value"], 1.5)
            adapter.cache.update_services(["pv.1"])
            self.assertEqual(
                adapter.read_executor.poll_read_spec("pv", {"aggregate": "services-sum", "prefix": "pv.", "path": "/P"}),
                "applied",
            )
            self.assertEqual(adapter.read_executor.poll_read_spec("direct", {"service": "svc", "path": "/Path"}), "applied")
            self.assertEqual(adapter.read_executor.refresh_requested_value({"service": "svc", "path": "/Path"}), "applied")
            self.assertEqual(adapter.read_executor.refresh_requested_value({"key": "grid_power_w"}), "deferred")
            self.assertEqual(adapter.read_executor.refresh_requested_value({}), "dropped")

            adapter.read_executor.read_busitem = MagicMock(side_effect=RuntimeError("read failed"))  # type: ignore[method-assign]
            self.assertEqual(adapter.read_executor.poll_read_spec("bad", {"service": "svc", "path": "/Bad"}), "dropped")
            self.assertEqual(adapter.cache.values["bad"]["status"], "error")
            adapter.read_executor.read_busitem = MagicMock(side_effect=DbusOperationDeferred("read"))  # type: ignore[method-assign]
            self.assertEqual(adapter.read_executor.poll_read_spec("later", {"service": "svc", "path": "/Later"}), "deferred")
            self.assertEqual(adapter.read_executor.poll_read_spec("empty", {"aggregate": "sum", "service": "svc", "paths": []}), "applied")
            self.assertEqual(adapter.cache.values["empty"]["value"], 0.0)
            self.assertEqual(
                adapter.read_executor.poll_read_spec(
                    "battery_missing",
                    {"aggregate": "first-service", "prefix": "missing.", "path": "/Soc"},
                ),
                "dropped",
            )

    def test_read_executor_direct_dbus_busitem_uses_timed_operation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            fake_iface = MagicMock()
            fake_iface.GetValue.return_value = 4.0
            fake_bus = MagicMock()
            fake_bus.get_object.return_value = object()
            adapter.connection.bus = MagicMock(return_value=fake_bus)  # type: ignore[method-assign]
            with patch.object(adapter_module.dbus, "Interface", return_value=fake_iface):
                self.assertEqual(adapter.read_executor.read_busitem("svc", "/P"), 4.0)
            fake_bus.get_object.assert_called_once_with("svc", "/P", introspect=False)
            self.assertIsNone(adapter.read_executor.read_busitem("", "/P"))
            self.assertIsNone(adapter.read_executor.read_busitem("svc", ""))

            adapter.cache.update_services([])
            self.assertEqual(
                adapter.read_executor.poll_read_spec("missing", {"aggregate": "services-sum", "prefix": "missing.", "path": "/P"}),
                "dropped",
            )
            self.assertEqual(adapter.cache.values["missing"]["status"], "error")
            adapter.read_executor._read_busitem_now = MagicMock(return_value=None)  # type: ignore[method-assign]
            adapter.rate_limiter.next_at["read"] = time.monotonic()
            self.assertEqual(
                adapter.read_executor.poll_read_spec("explicit", {"aggregate": "services-sum", "service": "explicit", "path": "/P"}),
                "applied",
            )
            self.assertEqual(adapter.cache.values["explicit"]["value"], 0.0)

    def test_socket_client_timeout_does_not_block_tick(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            conn = MagicMock()
            conn.__enter__.return_value = conn
            conn.recv.side_effect = TimeoutError("idle")
            server = MagicMock()
            server.accept.return_value = (conn, object())
            adapter._server = server

            with patch.object(adapter_module.select, "select", return_value=([server], [], [])):
                adapter._process_socket_once()

            conn.settimeout.assert_called_once_with(0.1)
            conn.sendall.assert_not_called()

    def test_socket_payload_and_socket_poll_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            self.assertFalse(adapter._handle_socket_payload("{")["ok"])
            self.assertFalse(adapter._handle_socket_payload("[]")["ok"])
            self.assertTrue(adapter._handle_socket_payload('{"type":"snapshot"}')["ok"])
            self.assertTrue(adapter._handle_socket_payload('{"type":"health"}')["ok"])
            for request_type in ("refresh_value", "refresh_services", "publish_desired", "publish_value", "set_value"):
                self.assertTrue(adapter._handle_socket_payload(adapter_module.json.dumps({"type": request_type}))["ok"])
            self.assertFalse(adapter._handle_socket_payload('{"type":"wat"}')["ok"])

            adapter._server = None
            adapter._process_socket_once()
            server = MagicMock()
            adapter._server = server
            with patch.object(adapter_module.select, "select", return_value=([], [], [])):
                adapter._process_socket_once()
            with patch.object(adapter_module.select, "select", return_value=([server], [], [])):
                server.accept.side_effect = BlockingIOError()
                adapter._process_socket_once()

    def test_socket_process_sends_json_response(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            conn = MagicMock()
            conn.__enter__.return_value = conn
            conn.recv.return_value = b'{"type":"snapshot"}'
            server = MagicMock()
            server.accept.return_value = (conn, object())
            adapter._server = server
            with patch.object(adapter_module.select, "select", return_value=([server], [], [])):
                adapter._process_socket_once()
            conn.sendall.assert_called_once()

    def test_socket_lifecycle_creates_and_removes_unix_socket(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            Path(adapter.paths.socket_path).parent.mkdir(parents=True, exist_ok=True)
            Path(adapter.paths.socket_path).write_text("stale", encoding="utf-8")

            adapter._start_socket()
            self.assertIsNotNone(adapter._server)
            adapter._close_socket()
            self.assertIsNone(adapter._server)
            adapter._close_socket()

    def test_health_snapshot_includes_gateway_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            paths = gateway_paths(str(Path(temp_dir) / "run"))
            adapter = DbusAdapter(str(config_path), paths=paths)

            adapter.write_scheduler.registered_paths.update({"/Mode", "/StartStop"})
            adapter.commands.enqueue({"kind": "refresh_services", "priority": "read"})
            legacy_refresh = Path(paths.command_dir) / "legacy-refresh.json"
            legacy_refresh.write_text(
                '{"kind":"refresh_services","created_at":1.0,"coalesce_key":"refresh-services"}',
                encoding="utf-8",
            )
            adapter.core_commands.enqueue({"kind": "user_command", "path": "/Mode", "value": 1})
            adapter.circuit.record_success(12.5)
            adapter.cache.update_value("grid_power_w", 10.0, source="grid", now=time.time() - 1.0)
            adapter.cache.mark_error("pv_power_w", source="pv", error="offline")
            adapter._last_tick_at = 123.0
            adapter._last_tick_monotonic = time.monotonic() - 0.25
            adapter._last_tick_duration_ms = 7.5
            adapter.resource_monitor.snapshot = MagicMock(return_value={"state": "ok"})  # type: ignore[method-assign]
            adapter.tick_health.record(duration_ms=7.5, expected_interval_s=adapter.tick_seconds)

            health = adapter._health_snapshot()

            self.assertEqual(health["state"], "ok")
            self.assertEqual(health["pending_command_count"], 1)
            self.assertEqual(health["physical_command_count"], 2)
            self.assertEqual(health["core_command_count"], 1)
            self.assertEqual(health["registered_path_count"], 2)
            self.assertEqual(health["last_tick_at"], 123.0)
            self.assertEqual(health["tick_duration_ms"], 7.5)
            self.assertIn("discovery_next_scan_at", health)
            self.assertGreaterEqual(health["mainloop_heartbeat_age_s"], 0.0)
            self.assertGreater(health["last_success_at"], 0.0)
            self.assertEqual(health["last_error"], "")
            self.assertEqual(health["queues"]["pending_command_count"], 1)
            self.assertEqual(health["queues"]["physical_command_count"], 2)
            self.assertGreaterEqual(health["queues"]["oldest_command_age_s"], 0.0)
            self.assertEqual(health["queue_classes"]["discovery"]["pending"], 1)
            self.assertEqual(health["cache_freshness"]["grid_power_w_status"], "fresh")
            self.assertEqual(health["cache_freshness"]["pv_power_w_status"], "error")
            self.assertIn("core_reads_fresh", health["slo"]["checks"])
            self.assertIn(health["backpressure"]["state"], {"ok", "congested", "slow", "protective"})
            self.assertIn("core_should_throttle", health["backpressure"])
            self.assertEqual(health["resources"]["state"], "ok")
            self.assertEqual(health["adaptive_tick_seconds"], adapter.tick_seconds)
            self.assertEqual(health["min_tick_seconds"], adapter.min_tick_seconds)
            self.assertEqual(health["max_tick_seconds"], adapter.max_tick_seconds)
            self.assertEqual(health["eventloop"]["tick_duration_ms"], 7.5)

    def test_backpressure_marks_slo_violations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                "DbusGatewaySloGuiMaxAgeSeconds=1\n"
                "DbusGatewaySloCoreReadMaxAgeSeconds=1\n"
                "DbusGatewaySloQueueMaxAgeSeconds=1\n"
                "DbusGatewaySloMainloopGapMaxMs=100\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            now = time.time()
            adapter.cache.update_value(
                f"path:{adapter.service_name}/Mode",
                1,
                source=f"{adapter.service_name}/Mode",
                now=now - 5.0,
            )
            adapter.cache.update_value("grid_power_w", 10.0, source="grid", now=now - 5.0)
            monotonic_now = time.monotonic()
            adapter.tick_health.record(duration_ms=1.0, expected_interval_s=0.1, now=monotonic_now - 1.0)
            adapter.tick_health.record(duration_ms=1.0, expected_interval_s=0.1, now=monotonic_now)
            adapter.commands.enqueue({"kind": "refresh_services", "created_at": now - 5.0})

            health = adapter._health_snapshot()

            self.assertEqual(health["slo"]["state"], "violated")
            self.assertIn("gui_fresh", health["slo"]["violated"])
            self.assertIn("core_reads_fresh", health["slo"]["violated"])
            self.assertIn("queue_age_ok", health["slo"]["violated"])
            self.assertIn("mainloop_gap_ok", health["slo"]["violated"])
            self.assertNotEqual(health["backpressure"]["state"], "ok")
            self.assertTrue(health["backpressure"]["core_should_throttle"])

    def test_expired_command_is_removed_and_journaled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lifecycle_path = Path(temp_dir) / "run" / "lifecycle.jsonl"
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                f"DbusGatewayCommandLifecyclePath={lifecycle_path}\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            command_path = adapter.commands.enqueue(
                {
                    "kind": "refresh_services",
                    "created_at": time.time() - 10.0,
                    "deadline_s": 1.0,
                    "priority": "read",
                }
            )

            self.assertTrue(adapter.write_scheduler.process_one())

            self.assertFalse(Path(command_path).exists())
            health = adapter.write_scheduler.health(now=time.time())
            self.assertEqual(health["lifecycle_counts"]["expired"], 1)
            self.assertIn('"state":"expired"', lifecycle_path.read_text(encoding="utf-8"))

    def test_gui_publish_burst_drains_large_local_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                "DbusGatewayLocalPublishBurstLimit=250\n"
                "DbusGatewayLocalPublishTickBudgetMs=10000\n"
                "DbusGatewayQueueBudgetLocalPublish=250\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter._dbusservice_registered = True
            writes: list[tuple[str, object]] = []

            class _FakeDbusService(dict):
                def __setitem__(self, key: str, value: object) -> None:
                    writes.append((key, value))
                    super().__setitem__(key, value)

            adapter._dbusservice = _FakeDbusService()
            for index in range(200):
                path = f"/LoadTest/{index}"
                adapter.write_scheduler.registered_paths.add(path)
                adapter.commands.enqueue(
                    {
                        "kind": "publish_value",
                        "path": path,
                        "value": index,
                        "priority": "publish",
                        "coalesce_key": f"publish:{path}",
                    }
                )

            processed = adapter.write_scheduler.process_local_publish_burst(200)

            self.assertEqual(processed, 200)
            self.assertEqual(len(writes), 200)
            self.assertEqual(adapter.commands.load_pending(), [])

    def test_gui_publish_burst_stops_at_tick_time_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                "DbusGatewayLocalPublishBurstLimit=10\n"
                "DbusGatewayLocalPublishTickBudgetMs=1\n"
                "DbusGatewayQueueBudgetLocalPublish=10\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter._dbusservice_registered = True
            adapter._timed_local_publish = MagicMock(side_effect=lambda operation: operation())  # type: ignore[method-assign]
            writes: list[tuple[str, object]] = []

            class _FakeDbusService(dict):
                def __setitem__(self, key: str, value: object) -> None:
                    writes.append((key, value))
                    super().__setitem__(key, value)

            adapter._dbusservice = _FakeDbusService()
            for index in range(5):
                path = f"/BudgetTest/{index}"
                adapter.write_scheduler.registered_paths.add(path)
                adapter.commands.enqueue(
                    {
                        "kind": "publish_value",
                        "path": path,
                        "value": index,
                        "priority": "publish",
                        "coalesce_key": f"publish:{path}",
                    }
                )

            with patch.object(write_module.time, "monotonic", side_effect=[0.0, 0.0, 0.002]):
                processed = adapter.write_scheduler.process_local_publish_burst()

            self.assertEqual(processed, 1)
            self.assertEqual(len(writes), 1)
            self.assertEqual(len(adapter.commands.load_pending()), 4)

    def test_local_publish_burst_skip_and_defer_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nDbusGatewayLocalPublishBurstLimit=3\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            self.assertEqual(adapter.write_scheduler.process_local_publish_burst(), 0)
            adapter._dbusservice_registered = True
            adapter.commands.enqueue({"kind": "set_value", "service": "svc", "path": "/A", "priority": "user"})
            self.assertEqual(adapter.write_scheduler.process_local_publish_burst(), 0)
            adapter.commands.enqueue(
                {
                    "kind": "publish_value",
                    "path": "/Missing",
                    "value": 1,
                    "priority": "publish",
                    "coalesce_key": "publish:/Missing",
                }
            )
            self.assertEqual(adapter.write_scheduler.process_local_publish_burst(), 1)
            adapter.write_scheduler.process_command = MagicMock(return_value="deferred")  # type: ignore[method-assign]
            adapter.commands.enqueue(
                {
                    "kind": "publish_value",
                    "path": "/Later",
                    "value": 1,
                    "priority": "publish",
                    "coalesce_key": "publish:/Later",
                }
            )
            self.assertEqual(adapter.write_scheduler.process_local_publish_burst(), 0)
            self.assertIsNotNone(adapter.write_scheduler._next_local_publish_command())
            for command_path, _command in adapter.commands.load_pending():
                adapter.commands.remove(command_path)
            self.assertIsNone(adapter.write_scheduler._next_local_publish_command())

    def test_local_publish_timer_fallback_records_success_and_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter._timed_local_publish = None  # type: ignore[method-assign]

            self.assertEqual(adapter.write_scheduler._timed_local_publish(lambda: "ok"), "ok")
            self.assertGreater(adapter.circuit.health()["successes_60s"], 0)

            with self.assertRaises(RuntimeError):
                adapter.write_scheduler._timed_local_publish(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
            self.assertGreater(adapter.circuit.health()["errors_60s"], 0)

    def test_startup_registration_batch_stops_at_tick_time_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                "DbusGatewayStartupRegistrationBatchLimit=10\n"
                "DbusGatewayStartupRegistrationTickBudgetMs=1\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter._dbusservice = _FakeVeDbusService()
            for index in range(5):
                adapter.commands.enqueue(
                    {
                        "kind": "register_path",
                        "path": f"/RegisterBudget/{index}",
                        "value": index,
                    }
                )
            commands = adapter.write_scheduler._prioritized_commands(DbusCommandInbox.coalesce(adapter.commands.load_pending()))

            with patch.object(write_module.time, "monotonic", side_effect=[0.0, 0.0, 0.002, 0.002]):
                self.assertTrue(adapter.write_scheduler.process_startup_registration_batch(commands))

            self.assertEqual(len(adapter.write_scheduler.registered_paths), 1)
            self.assertEqual(len(adapter.commands.load_pending()), 4)

            mixed = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-mixed")))
            mixed.write_scheduler.register_path = MagicMock(side_effect=["deferred", "applied"])  # type: ignore[method-assign]
            self.assertTrue(
                mixed.write_scheduler.process_startup_registration_batch(
                    [
                        ("deferred", {"kind": "register_path", "path": "/Deferred", "priority": "publish"}),
                        ("service", {"kind": "register_service", "priority": "publish"}),
                        ("applied", {"kind": "register_path", "path": "/Applied", "priority": "publish"}),
                    ]
                )
            )
            self.assertEqual(mixed.write_scheduler.register_path.call_count, 2)
            service_then_path = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-service-then-path")))
            service_then_path.write_scheduler.register_path = MagicMock(return_value="applied")  # type: ignore[method-assign]
            self.assertTrue(
                service_then_path.write_scheduler.process_startup_registration_batch(
                    [
                        ("service", {"kind": "register_service", "priority": "publish"}),
                        ("path", {"kind": "register_path", "path": "/AfterService", "priority": "publish"}),
                    ]
                )
            )
            unknown_then_path = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-unknown-then-path")))
            unknown_then_path.write_scheduler.register_path = MagicMock(return_value="applied")  # type: ignore[method-assign]
            self.assertTrue(
                unknown_then_path.write_scheduler.process_startup_registration_batch(
                    [
                        ("unknown", {"kind": "unknown", "priority": "publish"}),
                        ("path", {"kind": "register_path", "path": "/AfterUnknown", "priority": "publish"}),
                    ]
                )
            )

    def test_startup_registration_service_only_and_zero_limit_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            commands = [("svc", {"kind": "register_service", "priority": "publish"})]
            self.assertTrue(adapter.write_scheduler.process_startup_registration_batch(commands))
            self.assertTrue(adapter._dbusservice.registered)

            limited = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-limited")))
            limited.write_scheduler.startup_registration_batch_limit = 0
            limited.commands.enqueue({"kind": "register_path", "path": "/A", "priority": "publish"})
            limited_commands = limited.write_scheduler._prioritized_commands(DbusCommandInbox.coalesce(limited.commands.load_pending()))
            self.assertFalse(limited.write_scheduler.process_startup_registration_batch(limited_commands))

            deferred_service = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-deferred-service")))
            deferred_service.write_scheduler.process_command = MagicMock(return_value="deferred")  # type: ignore[method-assign]
            self.assertFalse(
                deferred_service.write_scheduler.process_startup_registration_batch(
                    [("svc", {"kind": "register_service", "priority": "publish"})]
                )
            )

    def test_queue_class_budget_defers_over_budget_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                "DbusGatewayQueueBudgetRemoteWrite=1\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            first = {"kind": "set_value", "service": "svc", "path": "/A", "created_at": 1.0, "priority": "user"}
            second = {"kind": "set_value", "service": "svc", "path": "/B", "created_at": 2.0, "priority": "user"}

            self.assertIs(adapter.write_scheduler._select_next_command([("a", first), ("b", second)])[1], first)
            adapter.write_scheduler._record_budget(first)
            self.assertIsNone(adapter.write_scheduler._select_next_command([("a", first), ("b", second)]))

            adapter.write_scheduler._budget_events.clear()
            self.assertIs(adapter.write_scheduler._select_next_command([("a", first), ("b", second)])[1], first)
            health = adapter.write_scheduler.health(now=time.time())
            self.assertEqual(health["queue_class_budgets"]["remote-write"], 1)

    def test_slo_regulation_adjusts_burst_reads_and_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                "DbusGatewayLocalPublishBurstLimit=2\n"
                "DbusGatewaySloQueueMaxAgeSeconds=1\n"
                "DbusGatewaySloCoreReadMaxAgeSeconds=1\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            now = time.time()
            adapter.commands.enqueue({"kind": "publish_value", "path": "/Mode", "created_at": now - 10.0})
            adapter.cache.update_value("grid_power_w", 1.0, source="grid", now=now - 10.0)
            adapter.read_scheduler.next_read_at = {key: now + 1000.0 for key in adapter.read_scheduler.specs}

            adapter._apply_slo_regulation()

            self.assertGreater(adapter.write_scheduler.dynamic_local_publish_burst_limit, 2)
            self.assertEqual(adapter.read_scheduler.next_read_at["grid_power_w"], 0.0)
            self.assertEqual(adapter.read_scheduler.next_read_at["pv_power_w"], 0.0)
            self.assertEqual(adapter.read_scheduler.next_read_at["battery_soc"], 0.0)

            adapter.circuit.degraded_until = time.time() + 60.0
            adapter.discovery.next_scan_at = 0.0
            adapter._apply_slo_regulation()

            self.assertGreater(adapter.discovery.next_scan_at, time.time())

    def test_health_history_log_records_small_operational_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            health_log = Path(temp_dir) / "run" / "health-history.jsonl"
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                f"DbusGatewayHealthLogPath={health_log}\n"
                "DbusGatewayHealthLogIntervalSeconds=0.01\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.cache.write_snapshot_files = MagicMock()  # type: ignore[method-assign]

            adapter._publish_cache()

            payload = json.loads(health_log.read_text(encoding="utf-8").strip())
            self.assertIn("backpressure", payload)
            self.assertIn("queue_oldest_age_s", payload)
            self.assertIn("cache_freshness", payload)

            adapter.health_log_path = "health-history-without-dir.jsonl"
            adapter._last_health_log_monotonic = 0.0
            log_handle = unittest.mock.mock_open()
            with patch.object(adapter_module.os.path, "dirname", return_value=""), patch.object(
                builtins, "open", log_handle
            ):
                adapter._append_health_log({"state": "ok"})
            log_handle.assert_called_once_with("health-history-without-dir.jsonl", "a", encoding="utf-8")

            adapter._last_health_log_monotonic = 0.0
            with patch.object(builtins, "open", side_effect=OSError("full")):
                adapter._append_health_log({"state": "ok"})

    def test_queue_age_uses_updated_at_for_coalesced_activity(self) -> None:
        commands = [
            ("fresh", {"created_at": 10.0, "updated_at": 95.0}),
            ("old", {"created_at": 90.0}),
            ("bad", {"created_at": "bad"}),
        ]

        self.assertEqual(DbusAdapter._oldest_command_age(commands, 100.0), 10.0)

    def test_adaptive_tick_uses_fast_default_and_slows_under_pressure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            self.assertEqual(adapter.min_tick_seconds, 0.2)
            self.assertEqual(adapter.tick_seconds, 0.2)
            self.assertEqual(adapter._adaptive_tick_seconds(circuit_state="ok", resource_state="ok"), 0.2)
            self.assertAlmostEqual(adapter._adaptive_tick_seconds(circuit_state="ok", resource_state="busy"), 0.3)
            self.assertEqual(adapter._adaptive_tick_seconds(circuit_state="degraded", resource_state="ok"), 0.5)
            self.assertEqual(adapter._adaptive_tick_seconds(circuit_state="ok", resource_state="constrained"), 1.0)
            self.assertEqual(adapter._adaptive_tick_seconds(circuit_state="protective", resource_state="ok"), 1.0)

            adapter.resource_monitor.snapshot = MagicMock(return_value={"state": "busy"})  # type: ignore[method-assign]
            adapter._update_adaptive_tick()

            self.assertAlmostEqual(adapter.tick_seconds, 0.3)
            self.assertEqual(adapter._last_resource_snapshot["state"], "busy")

    def test_tick_skips_work_until_adaptive_interval_is_due(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter._next_work_tick_monotonic = time.monotonic() + 10.0
            adapter._process_socket_once = MagicMock()  # type: ignore[method-assign]
            adapter._process_one_dbus_operation_once = MagicMock()  # type: ignore[method-assign]
            adapter._publish_cache = MagicMock()  # type: ignore[method-assign]

            self.assertTrue(adapter._tick())

            adapter._process_socket_once.assert_not_called()
            adapter._process_one_dbus_operation_once.assert_not_called()
            adapter._publish_cache.assert_not_called()
            adapter.tick_health.record(duration_ms=10000.0, expected_interval_s=0.1, now=time.monotonic())
            adapter._update_adaptive_tick()
            self.assertAlmostEqual(adapter.tick_seconds, 0.3)

    def test_gateway_processes_legacy_introspection_request_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            request_path = Path(temp_dir) / "requests.json"
            snapshot_path = Path(temp_dir) / "map.json"
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                f"DbusIntrospectionRequestPath={request_path}\n"
                f"DbusIntrospectionSnapshotPath={snapshot_path}\n",
                encoding="utf-8",
            )
            request_path.write_text(
                json.dumps(
                    {
                        "requests": [
                            {
                                "service": "com.victronenergy.system",
                                "path": "/Ac/Grid/L1/Power",
                                "priority": 100,
                                "source": "test",
                                "reason": "unit",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            adapter._process_introspection_requests_once()

            pending = adapter.commands.load_pending()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0][1]["kind"], "introspect")
            self.assertEqual(pending[0][1]["coalesce_key"], "introspect:com.victronenergy.system:/Ac/Grid/L1/Power")
            self.assertEqual(json.loads(request_path.read_text(encoding="utf-8")), {"requests": []})

    def test_gateway_introspection_request_and_background_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            request_path = Path(temp_dir) / "requests.json"
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                f"DbusIntrospectionRequestPath={request_path}\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.dbus_introspection_enabled = False
            adapter._process_introspection_requests_once()
            self.assertEqual(adapter.commands.load_pending(), [])

            adapter.dbus_introspection_enabled = True
            adapter.dbus_introspection_request_path = ""
            self.assertEqual(adapter._read_introspection_request_payload(), {})
            adapter.dbus_introspection_request_path = str(request_path)
            request_path.write_text("[]", encoding="utf-8")
            self.assertEqual(adapter._read_introspection_request_payload(), {})
            request_path.write_text("{", encoding="utf-8")
            self.assertEqual(adapter._read_introspection_request_payload(), {})
            request_path.write_text(json.dumps({"requests": "bad"}), encoding="utf-8")
            adapter._process_introspection_requests_once()
            self.assertEqual(adapter.commands.load_pending(), [])
            request_path.write_text(json.dumps({"requests": ["bad", {}, {"service": "", "path": "/P"}]}), encoding="utf-8")
            adapter._process_introspection_requests_once()
            self.assertEqual(adapter.commands.load_pending(), [])
            request_path.write_text(json.dumps({"requests": [{"service": "svc", "path": "/P"}]}), encoding="utf-8")
            adapter._process_introspection_requests_once()
            self.assertEqual(adapter._introspection_queue_depth, 1)
            clear_globals = adapter._clear_introspection_request_payload.__globals__
            with patch.dict(
                clear_globals,
                {"write_text_atomically": MagicMock(side_effect=RuntimeError("readonly"))},
            ), patch.object(clear_globals["logging"], "debug") as debug:
                adapter._clear_introspection_request_payload()
            debug.assert_called_once()

            background = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-bg")))
            background._enqueue_introspection_command = MagicMock()  # type: ignore[method-assign]
            background._enqueue_background_introspection_if_due()
            background._enqueue_introspection_command.assert_not_called()
            background.cache.update_services(["com.victronenergy.battery.tty1", "com.victronenergy.pvinverter.http_1"])
            background.circuit.protective_until = time.time() + 10.0
            background._enqueue_background_introspection_if_due()
            background._enqueue_introspection_command.assert_not_called()
            background.circuit.protective_until = 0.0
            background._last_introspection_full_scan_at = 0.0
            background._enqueue_background_introspection_if_due()
            self.assertGreater(background._enqueue_introspection_command.call_count, 0)
            self.assertEqual(
                background._configured_or_prefixed_services(
                    "UnusedExplicit",
                    "UnusedPrefix",
                    "com.victronenergy.pvinverter",
                ),
                ["com.victronenergy.pvinverter.http_1"],
            )
            background.config["DEFAULT"]["UnusedExplicit"] = "com.victronenergy.pvinverter.missing"
            self.assertEqual(
                background._configured_or_prefixed_services(
                    "UnusedExplicit",
                    "UnusedPrefix",
                    "com.victronenergy.pvinverter",
                ),
                [],
            )

            quiet_config = Path(temp_dir) / "quiet.ini"
            quiet_config.write_text(
                "[DEFAULT]\n"
                "AutoGridService=\n"
                "AutoGridL1Path=\n"
                "AutoGridL2Path=\n"
                "AutoGridL3Path=\n"
                "AutoBatterySocPath=\n"
                "AutoPvPath=\n",
                encoding="utf-8",
            )
            quiet_background = DbusAdapter(str(quiet_config), paths=gateway_paths(str(Path(temp_dir) / "run-quiet-bg")))
            quiet_background.cache.update_services(["com.victronenergy.battery.tty1", "com.victronenergy.pvinverter.http_1"])
            self.assertEqual(quiet_background._background_introspection_specs(), [])

    def test_gateway_writes_legacy_introspection_snapshot_from_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = Path(temp_dir) / "map.json"
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                f"DbusIntrospectionSnapshotPath={snapshot_path}\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            xml = "<node><interface name='com.victronenergy.BusItem'/><node name='Child'/></node>"
            adapter.cache.update_value(
                "introspection:com.victronenergy.system:/Ac/Grid",
                xml,
                source="com.victronenergy.system/Ac/Grid",
                confidence=0.7,
                now=123.0,
            )

            adapter._write_introspection_snapshot()

            payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
            finding = payload["services"]["com.victronenergy.system"]["paths"]["/Ac/Grid"]
            self.assertEqual(payload["worker_state"], "gateway")
            self.assertEqual(finding["status"], "fresh")
            self.assertEqual(finding["interfaces"], ["com.victronenergy.BusItem"])
            self.assertEqual(finding["children"], ["Child"])
            adapter.cache.mark_error("introspection:bad-key", source="bad", error="bad", now=124.0)
            adapter.cache.mark_error("introspection::/NoService", source="bad", error="bad", now=124.5)
            adapter.cache.mark_error("introspection:svc:/Broken", source="svc/Broken", error="offline", now=125.0)
            snapshot = adapter._introspection_services_snapshot(200.0)
            self.assertNotIn("", snapshot)
            self.assertEqual(snapshot["svc"]["paths"]["/Broken"]["status"], "unresponsive-backoff")
            self.assertEqual(adapter._parse_introspection_xml("<bad"), ([], []))

            adapter.dbus_introspection_enabled = False
            adapter._write_introspection_snapshot()

    def test_gateway_introspection_snapshot_logs_write_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = Path(temp_dir) / "map.json"
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                f"DbusIntrospectionSnapshotPath={snapshot_path}\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.dbus_introspection_enabled = True
            method_globals = adapter._write_introspection_snapshot.__globals__

            with (
                patch.dict(method_globals, {"write_text_atomically": MagicMock(side_effect=OSError("readonly"))}),
                patch.object(method_globals["logging"], "debug") as debug_log,
            ):
                adapter._write_introspection_snapshot()

        debug_log.assert_called_once()

    def test_tick_and_dbus_operation_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            adapter._stop = True
            adapter._close_socket = MagicMock()  # type: ignore[method-assign]
            self.assertFalse(adapter._tick())
            adapter._close_socket.assert_called_once()

            adapter._stop = False
            adapter._process_socket_once = MagicMock(side_effect=RuntimeError("tick failed"))  # type: ignore[method-assign]
            self.assertTrue(adapter._tick())
            self.assertEqual(adapter.circuit.last_error, "tick failed")

            adapter._poll_one_due_read_once = MagicMock(return_value=True)  # type: ignore[method-assign]
            adapter.write_scheduler.process_one = MagicMock(return_value=True)  # type: ignore[method-assign]
            adapter._refresh_services_if_due_once = MagicMock(return_value=True)  # type: ignore[method-assign]
            self.assertTrue(adapter._process_one_dbus_operation_once())
            adapter._refresh_services_if_due_once.assert_called_once()
            adapter._poll_one_due_read_once.assert_not_called()

            adapter.cache.update_services(["svc"])
            adapter._refresh_services_if_due_once = MagicMock(return_value=True)  # type: ignore[method-assign]
            adapter._poll_one_due_read_once = MagicMock(return_value=True)  # type: ignore[method-assign]
            adapter.write_scheduler.process_one = MagicMock(return_value=True)  # type: ignore[method-assign]
            self.assertTrue(adapter._process_one_dbus_operation_once())
            adapter._poll_one_due_read_once.assert_called_once()
            adapter.write_scheduler.process_one.assert_not_called()
            adapter._poll_one_due_read_once = MagicMock(return_value=False)  # type: ignore[method-assign]
            adapter.write_scheduler.process_one = MagicMock(return_value=True)  # type: ignore[method-assign]
            self.assertTrue(adapter._process_one_dbus_operation_once())
            adapter._prefer_read_next = True
            adapter._poll_one_due_read_once = MagicMock(return_value=False)  # type: ignore[method-assign]
            adapter.write_scheduler.process_one = MagicMock(return_value=False)  # type: ignore[method-assign]
            adapter._refresh_services_if_due_once = MagicMock(return_value=False)  # type: ignore[method-assign]
            self.assertFalse(adapter._process_one_dbus_operation_once())
            adapter._prefer_read_next = False
            adapter.write_scheduler.process_one = MagicMock(return_value=False)  # type: ignore[method-assign]
            adapter._poll_one_due_read_once = MagicMock(return_value=True)  # type: ignore[method-assign]
            self.assertTrue(adapter._process_one_dbus_operation_once())
            adapter.write_scheduler.process_one = MagicMock(return_value=False)  # type: ignore[method-assign]
            adapter._poll_one_due_read_once = MagicMock(return_value=False)  # type: ignore[method-assign]
            adapter._refresh_services_if_due_once = MagicMock(return_value=True)  # type: ignore[method-assign]
            self.assertTrue(adapter._process_one_dbus_operation_once())

            adapter._process_socket_once = MagicMock()  # type: ignore[method-assign]
            adapter._process_one_dbus_operation_once = MagicMock()  # type: ignore[method-assign]
            adapter._process_introspection_requests_once = MagicMock()  # type: ignore[method-assign]
            adapter._publish_cache = MagicMock()  # type: ignore[method-assign]
            adapter._next_work_tick_monotonic = 0.0
            self.assertTrue(adapter._tick())
            adapter._process_socket_once.assert_called_once()
            adapter._process_introspection_requests_once.assert_called_once()
            adapter._process_one_dbus_operation_once.assert_called_once()
            adapter._publish_cache.assert_called_once()

    def test_health_log_backpressure_and_publish_failure_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.health_log_path = ""
            adapter._append_health_log({"state": "ok"})
            adapter.health_log_path = str(Path(temp_dir) / "health.log")
            adapter.health_log_interval_seconds = 0.01
            with patch.object(builtins, "open", side_effect=OSError("full")):
                adapter._append_health_log({"state": "ok"})

            with self.assertRaises(RuntimeError):
                adapter._timed_local_publish(lambda: (_ for _ in ()).throw(RuntimeError("publish failed")))

            slow = adapter._backpressure_snapshot(
                slo={"violated": []},
                queue_health={"oldest_command_age_s": adapter.slo_queue_max_age_seconds * 3.0},
            )
            self.assertEqual(slow["state"], "slow")
            adapter.circuit.protective_until = time.time() + 10.0
            protective = adapter._backpressure_snapshot(slo={"violated": []}, queue_health={"oldest_command_age_s": 0.0})
            self.assertEqual(protective["state"], "protective")
            adapter.circuit.protective_until = 0.0
            congested = adapter._backpressure_snapshot(slo={"violated": ["gui_fresh"]}, queue_health={"oldest_command_age_s": 0.0})
            self.assertEqual(congested["state"], "congested")

            now = time.time()
            adapter.cache.update_value(f"path:{adapter.service_name}/Mode", 1, source="svc/Mode", now=now - 2.0)
            self.assertGreater(adapter._max_cached_path_age({"/Mode"}, now), 0.0)
            self.assertEqual(adapter._max_cached_path_age({"/Missing"}, now), 0.0)
            adapter.tick_health.record(duration_ms=1.0, expected_interval_s=0.1, now=time.monotonic() - 1.0)
            adapter.tick_health.record(duration_ms=1.0, expected_interval_s=0.1, now=time.monotonic())
            adapter._apply_slo_regulation()
            self.assertLessEqual(
                adapter.write_scheduler.dynamic_local_publish_burst_limit,
                adapter.write_scheduler.local_publish_burst_limit,
            )
            adapter.cache.values[f"path:{adapter.service_name}/Zero"] = {"updated_at": 0.0}
            self.assertEqual(adapter._max_cached_path_age({"/Zero"}, now), 0.0)

    def test_poll_and_discovery_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.read_scheduler.next_read_at = {key: time.time() + 1000 for key in adapter.read_scheduler.specs}
            self.assertFalse(adapter._poll_one_due_read_once())

            adapter.read_scheduler.next_read_at = {"grid_power_w": 0.0}
            adapter.read_executor.poll_read_spec = MagicMock(return_value="applied")  # type: ignore[method-assign]
            self.assertTrue(adapter._poll_one_due_read_once())
            adapter.read_scheduler.next_read_at = {"grid_power_w": 0.0}
            adapter.read_executor.poll_read_spec = MagicMock(return_value="dropped")  # type: ignore[method-assign]
            self.assertTrue(adapter._poll_one_due_read_once())
            adapter.read_scheduler.next_read_at = {"grid_power_w": 0.0}
            adapter.read_executor.poll_read_spec = MagicMock(return_value="deferred")  # type: ignore[method-assign]
            self.assertFalse(adapter._poll_one_due_read_once())

            adapter.discovery.next_scan_at = time.time() + 1000
            self.assertFalse(adapter._refresh_services_if_due_once())
            adapter.discovery.next_scan_at = 0.0
            refresh_path = adapter.commands.enqueue({"kind": "refresh_services", "priority": "normal"})
            self.assertTrue(Path(refresh_path).exists())
            adapter._list_services = MagicMock(return_value=["svc"])  # type: ignore[method-assign]
            self.assertTrue(adapter._refresh_services_if_due_once())
            self.assertIn("svc", adapter.cache.services)
            self.assertFalse(Path(refresh_path).exists())
            adapter.discovery.next_scan_at = 0.0
            adapter._list_services = MagicMock(side_effect=DbusOperationDeferred("read"))  # type: ignore[method-assign]
            self.assertFalse(adapter._refresh_services_if_due_once())
            adapter._list_services = MagicMock(side_effect=RuntimeError("dbus down"))  # type: ignore[method-assign]
            self.assertTrue(adapter._refresh_services_if_due_once())
            self.assertEqual(adapter.discovery.last_error, "dbus down")
            adapter._maybe_refresh_services()

    def test_cache_publish_interval_throttles_unchanged_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nDbusGatewayCachePublishIntervalSeconds=60\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.cache.write_snapshot_files = MagicMock()  # type: ignore[method-assign]

            adapter._publish_cache()
            adapter._publish_cache()
            adapter.cache.update_value("path:svc/P", 1, source="svc/P")
            adapter._publish_cache()

            self.assertEqual(adapter.cache.write_snapshot_files.call_count, 2)

    def test_signal_handlers_and_list_services_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            fake_loop = MagicMock()
            adapter._main_loop = fake_loop
            callbacks: dict[int, object] = {}

            def fake_signal(signum: int, callback: object) -> None:
                callbacks[signum] = callback

            with patch.object(adapter_module.signal, "signal", side_effect=fake_signal), patch.object(adapter_module.GLib, "idle_add") as idle_add:
                adapter._install_signal_handlers()
                callbacks[adapter_module.signal.SIGTERM](adapter_module.signal.SIGTERM, None)  # type: ignore[index,operator]
            self.assertTrue(adapter._stop)
            idle_add.assert_called_once_with(fake_loop.quit)

            adapter._main_loop = None
            adapter._stop = False
            with patch.object(adapter_module.signal, "signal", side_effect=fake_signal), patch.object(adapter_module.GLib, "idle_add") as idle_add:
                adapter._install_signal_handlers()
                callbacks[adapter_module.signal.SIGINT](adapter_module.signal.SIGINT, None)  # type: ignore[index,operator]
            self.assertTrue(adapter._stop)
            idle_add.assert_not_called()

            fake_iface = MagicMock()
            fake_iface.ListNames.return_value = ["svc.a", b"svc.b"]
            fake_bus = MagicMock()
            fake_bus.get_object.return_value = object()
            adapter.connection.bus = MagicMock(return_value=fake_bus)  # type: ignore[method-assign]
            with patch.object(adapter_module.dbus, "Interface", return_value=fake_iface):
                self.assertEqual(adapter._list_services(), ["svc.a", "b'svc.b'"])

    def test_socket_start_without_stale_path_and_default_cache_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            Path(adapter.paths.socket_path).parent.mkdir(parents=True, exist_ok=True)
            adapter._start_socket()
            adapter._close_socket()
            adapter.cache.write_snapshot_files = MagicMock()  # type: ignore[method-assign]
            adapter._publish_cache()
            adapter.cache.write_snapshot_files.assert_called_once()

    def test_atomic_json_writer_writes_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "payload.json"
            writer = adapter_module.AtomicJsonWriter()
            writer.write(str(path), {"ok": True})
            self.assertEqual(read_json_file(str(path), {}), {"ok": True})

    def test_non_write_introspection_timed_logging_main_and_json_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nLogging=DEBUG\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            self.assertEqual(adapter._process_non_write_command({"kind": "nope"}), "dropped")
            adapter.read_executor.refresh_requested_value = MagicMock(return_value="applied")  # type: ignore[method-assign]
            self.assertEqual(adapter._process_non_write_command({"kind": "refresh_value"}), "applied")
            adapter._list_services = MagicMock(return_value=["svc"])  # type: ignore[method-assign]
            self.assertEqual(adapter._process_non_write_command({"kind": "refresh_services"}), "applied")
            adapter.circuit.degraded_until = time.time() + 10.0
            self.assertEqual(adapter._process_non_write_command({"kind": "refresh_services"}), "deferred")
            self.assertEqual(adapter._process_non_write_command({"kind": "introspect"}), "deferred")
            adapter.circuit.degraded_until = 0.0
            self.assertEqual(adapter._introspect_command({}), "dropped")
            adapter._timed = MagicMock(return_value="<node/>")  # type: ignore[method-assign]
            self.assertEqual(adapter._process_non_write_command({"kind": "introspect", "service": "svc", "path": "/"}), "applied")
            self.assertEqual(adapter.cache.values["introspection:svc:/"]["value"], "<node/>")

            adapter._introspection_queue_depth = 1
            adapter._timed = MagicMock(side_effect=RuntimeError("no reply"))  # type: ignore[method-assign]
            self.assertEqual(
                adapter._process_non_write_command({"kind": "introspect", "service": "svc", "path": "/Slow"}),
                "dropped",
            )
            self.assertEqual(adapter._introspection_queue_depth, 0)
            self.assertEqual(adapter.cache.values["introspection:svc:/Slow"]["status"], "error")

            adapter._timed = MagicMock(side_effect=DbusOperationDeferred("rate limited"))  # type: ignore[method-assign]
            self.assertEqual(
                adapter._process_non_write_command({"kind": "introspect", "service": "svc", "path": "/Later"}),
                "deferred",
            )

            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-timed")))
            fake_iface = MagicMock()
            fake_iface.Introspect.return_value = "<real/>"
            fake_bus = MagicMock()
            fake_bus.get_object.return_value = object()
            adapter.connection.bus = MagicMock(return_value=fake_bus)  # type: ignore[method-assign]
            with patch.object(adapter_module.dbus, "Interface", return_value=fake_iface):
                self.assertEqual(adapter._introspect_command({"service": "svc", "path": "/Real", "timeout": 2.0}), "applied")
            fake_iface.Introspect.assert_called_once_with(timeout=2.0)

            self.assertEqual(adapter._timed("read", lambda: 42), 42)
            adapter.rate_limiter.next_at["read"] = 0.0
            with self.assertRaises(RuntimeError):
                adapter._timed("read", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
            self.assertEqual(adapter._json_ready({"ok": True}), {"ok": True})
            self.assertEqual(adapter._json_ready(object()).startswith("<object object"), True)

            self.assertEqual(adapter_module._logging_level_from_config(adapter.config), adapter_module.logging.DEBUG)
            with patch.object(adapter_module.DbusAdapter, "run") as run:
                self.assertEqual(adapter_module.main([str(config_path), "--run-dir", str(Path(temp_dir) / "run2")]), 0)
            run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
