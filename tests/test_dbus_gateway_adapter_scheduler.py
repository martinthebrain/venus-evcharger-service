# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import tempfile
import time
import sys
import unittest
import json
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
    )
    adapter_module = sys.modules["venus_evcharger_dbus_adapter"]


class DbusGatewayAdapterSchedulerTests(unittest.TestCase):
    def test_coalesced_commands_use_stable_filename_and_latest_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            inbox = DbusCommandInbox(str(Path(temp_dir) / "commands"))
            first = inbox.enqueue({"kind": "set_value", "value": 1, "coalesce_key": "ev:/Mode"})
            second = inbox.enqueue({"kind": "set_value", "value": 2, "coalesce_key": "ev:/Mode"})

            self.assertEqual(first, second)
            pending = inbox.load_pending()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0][1]["value"], 2)
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
        self.assertEqual(breaker.state(now=2000.0), "ok")

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

    def test_adapter_static_config_helpers_cover_defaults_and_invalid_instance(self) -> None:
        parser = adapter_module.configparser.ConfigParser()
        parser["DEFAULT"] = {
            "ServiceName": "com.example.ev",
            "DeviceInstance": "bad",
            "AutoGridL2Path": "",
            "AutoPvService": "pv.fixed",
            "AutoBatterySocPath": "/Soc",
        }
        self.assertEqual(DbusAdapter._evcharger_service_name(parser["DEFAULT"]), "com.example.ev.http_60")
        specs = DbusAdapter._configured_read_specs(parser["DEFAULT"])
        self.assertEqual(specs["grid_power_w"]["paths"], ["/Ac/Grid/L1/Power", "/Ac/Grid/L3/Power"])
        self.assertEqual(specs["pv_power_w"]["service"], "pv.fixed")
        self.assertEqual(specs["battery_soc"]["path"], "/Soc")

    def test_publish_desired_processes_one_path_per_tick(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
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
            self.assertEqual(adapter.write_scheduler.publish_command({"kind": "publish_desired", "paths": {"/Missing": 1}}), "deferred")
            self.assertEqual(adapter.write_scheduler.publish_path("", 1), "applied")
            self.assertEqual(adapter.write_scheduler.publish_path("/Missing", 1), "deferred")
            self.assertEqual(adapter.write_scheduler.publish_command({"kind": "publish_value", "path": "/Missing", "value": 1}), "deferred")
            self.assertEqual(adapter.write_scheduler.process_command({"kind": "unknown"}), "dropped")
            self.assertEqual(adapter.write_scheduler.process_command({"kind": "set_value"}), "dropped")
            adapter.write_scheduler.drop_stale_coalesced_commands("/tmp/none", {})
            processed = Path(adapter.paths.command_dir) / "processed.json"
            stale = Path(adapter.paths.command_dir) / "stale.json"
            stale.parent.mkdir(parents=True, exist_ok=True)
            processed.write_text(json.dumps({"kind": "publish_value", "coalesce_key": "same"}), encoding="utf-8")
            stale.write_text(json.dumps({"kind": "publish_value", "coalesce_key": "same"}), encoding="utf-8")
            adapter.write_scheduler.drop_stale_coalesced_commands(str(processed), {"coalesce_key": "same"})
            self.assertTrue(processed.exists())
            self.assertFalse(stale.exists())

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

            adapter.read_executor.read_busitem = fake_read  # type: ignore[method-assign]

            value, sources = adapter.read_executor.read_prefixed_service_sum(
                {"prefix": "com.victronenergy.pvinverter", "path": "/Ac/Power"}
            )

            self.assertEqual(value, 123.0)
            self.assertEqual(calls, [("com.victronenergy.pvinverter.http_1", "/Ac/Power")])
            self.assertEqual(sources, ["com.victronenergy.pvinverter.http_1/Ac/Power"])

    def test_read_executor_covers_refresh_sum_error_and_direct_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            values = {
                ("svc", "/L1"): 1.5,
                ("svc", "/L2"): None,
                ("svc", "/Path"): 7,
            }
            adapter.read_executor.read_busitem = lambda service, path: values.get((service, path), 0.0)  # type: ignore[method-assign]

            self.assertEqual(adapter.read_executor.poll_read_spec("sum", {"aggregate": "sum", "service": "svc", "paths": ["/L1", "/L2"]}), "applied")
            self.assertEqual(adapter.cache.values["sum"]["value"], 1.5)
            adapter.cache.update_services(["pv.1"])
            self.assertEqual(
                adapter.read_executor.poll_read_spec("pv", {"aggregate": "services-sum", "prefix": "pv.", "path": "/P"}),
                "applied",
            )
            self.assertEqual(adapter.read_executor.poll_read_spec("direct", {"service": "svc", "path": "/Path"}), "applied")
            self.assertEqual(adapter.read_executor.refresh_requested_value({"service": "svc", "path": "/Path"}), "applied")
            self.assertEqual(adapter.read_executor.refresh_requested_value({"key": "grid_power_w"}), "applied")
            self.assertEqual(adapter.read_executor.refresh_requested_value({}), "dropped")

            adapter.read_executor.read_busitem = MagicMock(side_effect=RuntimeError("read failed"))  # type: ignore[method-assign]
            self.assertEqual(adapter.read_executor.poll_read_spec("bad", {"service": "svc", "path": "/Bad"}), "applied")
            self.assertEqual(adapter.cache.values["bad"]["status"], "error")
            adapter.read_executor.read_busitem = MagicMock(side_effect=DbusOperationDeferred("read"))  # type: ignore[method-assign]
            self.assertEqual(adapter.read_executor.poll_read_spec("later", {"service": "svc", "path": "/Later"}), "deferred")

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
            with self.assertRaises(RuntimeError):
                adapter.read_executor.read_prefixed_service_sum({"prefix": "missing.", "path": "/P"})
            adapter.read_executor.read_busitem = MagicMock(side_effect=[None, 5.0])  # type: ignore[method-assign]
            total, sources = adapter.read_executor.read_prefixed_service_sum({"service": "explicit", "path": "/P"})
            self.assertEqual(total, 0.0)
            self.assertEqual(sources, [])

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
            adapter.core_commands.enqueue({"kind": "user_command", "path": "/Mode", "value": 1})
            adapter.circuit.record_success(12.5)
            adapter._last_tick_at = 123.0
            adapter._last_tick_monotonic = time.monotonic() - 0.25
            adapter._last_tick_duration_ms = 7.5

            health = adapter._health_snapshot()

            self.assertEqual(health["state"], "ok")
            self.assertEqual(health["pending_command_count"], 1)
            self.assertEqual(health["core_command_count"], 1)
            self.assertEqual(health["registered_path_count"], 2)
            self.assertEqual(health["last_tick_at"], 123.0)
            self.assertEqual(health["tick_duration_ms"], 7.5)
            self.assertIn("discovery_next_scan_at", health)
            self.assertGreaterEqual(health["mainloop_heartbeat_age_s"], 0.0)
            self.assertGreater(health["last_success_at"], 0.0)
            self.assertEqual(health["last_error"], "")

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

            adapter.write_scheduler.process_one = MagicMock(return_value=True)  # type: ignore[method-assign]
            self.assertTrue(adapter._process_one_dbus_operation_once())
            adapter.write_scheduler.process_one = MagicMock(return_value=False)  # type: ignore[method-assign]
            adapter._poll_one_due_read_once = MagicMock(return_value=True)  # type: ignore[method-assign]
            self.assertTrue(adapter._process_one_dbus_operation_once())
            adapter._poll_one_due_read_once = MagicMock(return_value=False)  # type: ignore[method-assign]
            adapter._refresh_services_if_due_once = MagicMock(return_value=True)  # type: ignore[method-assign]
            self.assertTrue(adapter._process_one_dbus_operation_once())

            adapter._process_socket_once = MagicMock()  # type: ignore[method-assign]
            adapter._process_one_dbus_operation_once = MagicMock()  # type: ignore[method-assign]
            adapter._publish_cache = MagicMock()  # type: ignore[method-assign]
            self.assertTrue(adapter._tick())

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
            self.assertTrue(adapter._poll_one_due_read_once())

            adapter.discovery.next_scan_at = time.time() + 1000
            self.assertFalse(adapter._refresh_services_if_due_once())
            adapter.discovery.next_scan_at = 0.0
            adapter._list_services = MagicMock(return_value=["svc"])  # type: ignore[method-assign]
            self.assertTrue(adapter._refresh_services_if_due_once())
            self.assertIn("svc", adapter.cache.services)
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
            self.assertEqual(adapter._introspect_command({}), "dropped")
            adapter._timed = MagicMock(return_value="<node/>")  # type: ignore[method-assign]
            self.assertEqual(adapter._process_non_write_command({"kind": "introspect", "service": "svc", "path": "/"}), "applied")
            self.assertEqual(adapter.cache.values["introspection:svc:/"]["value"], "<node/>")

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
