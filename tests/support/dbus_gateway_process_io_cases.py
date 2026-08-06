# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway adapter cache publication, signals, and timed I/O scenarios."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    AtomicJsonWriter,
    Callable,
    DbusAdapter,
    DbusOperationDeferred,
    GatewayAdapterContractCase,
    IntrospectionInterfaceStub,
    MagicMock,
    Path,
    ServiceListInterfaceStub,
    adapter_main,
    adapter_module,
    gateway_core_module,
    gateway_paths,
    install_mock,
    introspection_module,
    logging,
    patch,
    process_io_module,
    process_socket_module,
    read_json_file,
    runtime_module,
    tempfile,
    time,
)
from venus_evcharger.ipc.energy import EnergyRefreshRequest
from venus_evcharger.dbus_adapter.process.health import GatewayControlSnapshot
from venus_evcharger.dbus_adapter.process.config import logging_level_from_config


def _control_snapshot(*, captured_at: float = 100.0) -> GatewayControlSnapshot:
    return GatewayControlSnapshot(
        captured_at=captured_at,
        monotonic_at=captured_at,
        health={"state": "ok"},
        queue_age_seconds=0.0,
        core_read_age_seconds=0.0,
        eventloop_gap_ms=0.0,
        eventloop_max_duration_ms=0.0,
        resource_state="ok",
        pressure_state="ok",
        stale_core_reads=(),
    )


class GatewayProcessIoCases(GatewayAdapterContractCase):
    """Exercise cache publication, signals, and timed I/O scenarios."""

    def test_cache_publish_interval_is_a_hard_serialization_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                "DbusGatewayFullCachePublishIntervalSeconds=60\n"
                "DbusGatewayFullCacheDirtyIntervalSeconds=2\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            install_mock(adapter.cache, "write_cache_snapshot", MagicMock())
            install_mock(adapter.cache, "write_health_snapshot", MagicMock())
            install_mock(adapter.cache, "write_energy_inputs_snapshot", MagicMock())
            install_mock(adapter.cache, "write_energy_topology_snapshot", MagicMock())
            install_mock(adapter.diagnostics_role, "write_gateway_diagnostics", MagicMock())
            install_mock(adapter.health_role, "append_health_log", MagicMock())
            install_mock(adapter.introspection_snapshot_role, "write_introspection_snapshot", MagicMock())
            control = _control_snapshot()

            with patch.object(process_io_module.time, "monotonic", side_effect=[10.0, 10.5, 11.0]):
                adapter.io_role.publish_cache(control)
                adapter.io_role.publish_cache(control)
                adapter.cache.update_value("path:svc/P", 1, source="svc/P")
                adapter.io_role.publish_cache(control)

            self.assertEqual(adapter.cache.write_cache_snapshot.call_count, 1)

    def test_cache_publish_interval_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                "DbusGatewayEnergyPublishIntervalSeconds=1\n"
                "DbusGatewayHealthPublishIntervalSeconds=1\n"
                "DbusGatewayFullCachePublishIntervalSeconds=10\n"
                "DbusGatewayFullCacheDirtyIntervalSeconds=2\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            install_mock(adapter.cache, "write_cache_snapshot", MagicMock())
            install_mock(adapter.cache, "write_health_snapshot", MagicMock())
            install_mock(adapter.cache, "write_energy_inputs_snapshot", MagicMock())
            install_mock(adapter.cache, "write_energy_topology_snapshot", MagicMock())
            install_mock(adapter.diagnostics_role, "write_gateway_diagnostics", MagicMock())
            install_mock(adapter.health_role, "append_health_log", MagicMock())
            install_mock(adapter.introspection_snapshot_role, "write_introspection_snapshot", MagicMock())
            control = _control_snapshot()

            with patch.object(process_io_module.time, "monotonic", side_effect=[10.0, 10.5, 11.0, 12.0]):
                adapter.io_role.publish_cache(control)
                adapter.io_role.publish_cache(control)
                adapter.io_role.publish_cache(control)
                adapter.cache.update_value("path:svc/P", 1, source="svc/P")
                adapter.io_role.publish_cache(control)

            self.assertEqual(adapter.cache.health["state"], "ok")
            self.assertEqual(adapter.cache.write_energy_inputs_snapshot.call_count, 3)
            self.assertEqual(adapter.cache.write_health_snapshot.call_count, 3)
            self.assertEqual(adapter.cache.write_energy_topology_snapshot.call_count, 1)
            self.assertEqual(adapter.cache.write_cache_snapshot.call_count, 2)
            self.assertEqual(adapter.diagnostics_role.write_gateway_diagnostics.call_count, 3)
            self.assertEqual(adapter.health_role.append_health_log.call_count, 3)
            adapter.health_role.append_health_log.assert_called_with({"state": "ok"})
            self.assertEqual(adapter.introspection_snapshot_role.write_introspection_snapshot.call_count, 2)
            self.assertEqual(adapter._last_cache_publish_monotonic, 12.0)

    def test_signal_handlers_andlist_services_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            fake_loop = MagicMock()
            adapter._main_loop = fake_loop
            callbacks: dict[int, Callable[[int, object | None], None]] = {}

            def fake_signal(signum: int, callback: Callable[[int, object | None], None]) -> None:
                callbacks[signum] = callback

            with (
                patch.object(runtime_module.signal, "signal", side_effect=fake_signal),
                patch.object(runtime_module.GLib, "idle_add") as idle_add,
            ):
                adapter.runtime_role.install_signal_handlers()
                callbacks[runtime_module.signal.SIGTERM](runtime_module.signal.SIGTERM, None)
            self.assertTrue(adapter._stop)
            idle_add.assert_called_once_with(fake_loop.quit)

            adapter._main_loop = None
            adapter._stop = False
            with (
                patch.object(runtime_module.signal, "signal", side_effect=fake_signal),
                patch.object(runtime_module.GLib, "idle_add") as idle_add,
            ):
                adapter.runtime_role.install_signal_handlers()
                callbacks[runtime_module.signal.SIGINT](runtime_module.signal.SIGINT, None)
            self.assertTrue(adapter._stop)
            idle_add.assert_not_called()

            fake_iface = ServiceListInterfaceStub(["svc.a", b"svc.b"])
            fake_obj = object()
            get_object = install_mock(adapter.connection, "get_object", MagicMock(return_value=fake_obj))
            with patch.object(process_io_module.dbus, "Interface", return_value=fake_iface) as dbus_interface:
                self.assertEqual(adapter.io_role.list_services(), ["svc.a", "b'svc.b'"])
                get_object.assert_called_once_with(
                    "org.freedesktop.DBus",
                    "/org/freedesktop/DBus",
                    introspect=False,
                )
                dbus_interface.assert_called_once_with(fake_obj, "org.freedesktop.DBus")
                adapter.rate_limiter.next_at["read"] = 0.0
                fake_iface.names = "svc.a"
                with self.assertRaisesRegex(TypeError, "^DBus ListNames returned a non-iterable service list$"):
                    adapter.io_role.list_services()
                adapter.rate_limiter.next_at["read"] = 0.0
                fake_iface.names = object()
                with self.assertRaisesRegex(TypeError, "^DBus ListNames returned a non-iterable service list$"):
                    adapter.io_role.list_services()
            self.assertEqual(fake_iface.call_count, 3)
            self.assertEqual(process_io_module._service_names(("a", b"b")), ["a", "b'b'"])

    def test_socket_start_without_stale_path_and_default_cache_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            Path(adapter.paths.socket_path).parent.mkdir(parents=True, exist_ok=True)
            server = MagicMock()
            with (
                patch.object(process_socket_module.socket, "socket", return_value=server),
                patch.object(process_socket_module.os, "chmod") as chmod,
            ):
                adapter.socket_role.start_socket()
            chmod.assert_called_once_with(adapter.paths.socket_path, 0o600)
            adapter.socket_role.close_socket()
            server.close.assert_called_once_with()
            install_mock(adapter.cache, "write_cache_snapshot", MagicMock())
            install_mock(adapter.diagnostics_role, "write_gateway_diagnostics", MagicMock())
            adapter.io_role.publish_cache(_control_snapshot())
            adapter.cache.write_cache_snapshot.assert_called_once()

    def test_atomic_json_writer_writes_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "payload.json"
            writer = AtomicJsonWriter()
            writer.write(str(path), {"ok": True})
            self.assertEqual(read_json_file(str(path), {}), {"ok": True})

    def test_non_write_introspection_timed_logging_main_and_json_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nLogging=DEBUG\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            self.assertEqual(adapter.process_non_write_command({}), "dropped")
            self.assertEqual(adapter.process_non_write_command({"kind": "nope"}), "dropped")
            force_due = install_mock(adapter.read_scheduler, "force_due", MagicMock())
            refresh = EnergyRefreshRequest(
                request_id="io-pv",
                scope="pv",
                max_age_seconds=0.0,
                urgency="priority",
                reason="io-contract",
            )
            self.assertEqual(adapter.process_non_write_command(refresh.to_command(source="test")), "applied")
            force_due.assert_called_once_with(("pv_power_w",))
            self.assertEqual(adapter.process_non_write_command({"kind": "refresh_value"}), "dropped")
            self.assertEqual(adapter.process_non_write_command({"kind": "refresh_services"}), "dropped")
            adapter.circuit.degraded_until = time.time() + 10.0
            self.assertEqual(adapter.process_non_write_command({"kind": "introspect"}), "deferred")
            adapter.circuit.degraded_until = 0.0
            self.assertEqual(adapter.introspection_role.introspect_command({}), "dropped")
            timed_result = install_mock(
                adapter.introspection_role,
                "timed_introspection_result",
                MagicMock(return_value=("deferred", None)),
            )
            self.assertEqual(adapter.introspection_role.introspect_command({"service": "svc"}), "deferred")
            timed_result.assert_called_once_with("svc", "/", 1.0)
            delattr(adapter.introspection_role, "timed_introspection_result")
            install_mock(adapter.io_role, "timed_dbus_operation", MagicMock(return_value="<node/>"))
            self.assertEqual(
                adapter.process_non_write_command({"kind": "introspect", "service": "svc", "path": "/"}), "applied"
            )
            self.assertEqual(adapter.cache.values["introspection:svc:/"]["value"], "<node/>")

            adapter._introspection_queue_depth = 1
            install_mock(adapter.io_role, "timed_dbus_operation", MagicMock(side_effect=RuntimeError("no reply")))
            self.assertEqual(
                adapter.process_non_write_command({"kind": "introspect", "service": "svc", "path": "/Slow"}),
                "dropped",
            )
            self.assertEqual(adapter._introspection_queue_depth, 0)
            self.assertEqual(adapter.cache.values["introspection:svc:/Slow"]["status"], "error")

            install_mock(
                adapter.io_role,
                "timed_dbus_operation",
                MagicMock(side_effect=DbusOperationDeferred("rate limited")),
            )
            self.assertEqual(
                adapter.process_non_write_command({"kind": "introspect", "service": "svc", "path": "/Later"}),
                "deferred",
            )

            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-timed")))
            timed_error = RuntimeError("introspect failed")
            install_mock(adapter.io_role, "timed_dbus_operation", MagicMock(side_effect=timed_error))
            drop_failed = install_mock(adapter.introspection_role, "drop_failed_introspection", MagicMock(return_value="dropped"))
            self.assertEqual(adapter.introspection_role.timed_introspection_result("svc", "/Err", 3.0), ("dropped", None))
            drop_failed.assert_called_once_with("svc", "/Err", timed_error)
            delattr(adapter.io_role, "timed_dbus_operation")
            delattr(adapter.introspection_role, "drop_failed_introspection")

            fake_iface = IntrospectionInterfaceStub("<real/>")
            fake_obj = object()
            get_object = install_mock(adapter.connection, "get_object", MagicMock(return_value=fake_obj))
            with patch.object(introspection_module.dbus, "Interface", return_value=fake_iface) as interface:
                self.assertEqual(
                    adapter.introspection_role.introspect_command({"service": "svc", "path": "/Real", "timeout": 2.0}), "applied"
                )
            get_object.assert_called_once_with("svc", "/Real", introspect=False)
            interface.assert_called_once_with(fake_obj, "org.freedesktop.DBus.Introspectable")
            self.assertEqual(fake_iface.calls, [2.0])

            adapter._introspection_queue_depth = 2
            adapter.introspection_role.record_introspection_xml("svc", "/Recorded", "<xml/>")
            self.assertEqual(adapter._introspection_queue_depth, 1)
            recorded = adapter.cache.values["introspection:svc:/Recorded"]
            self.assertEqual(recorded["value"], "<xml/>")
            self.assertEqual(recorded["source"], "svc/Recorded")
            self.assertEqual(recorded["confidence"], 0.5)

            adapter._introspection_queue_depth = 2
            failed_error = RuntimeError("bad")
            with patch.object(introspection_module.logging, "debug") as debug:
                self.assertEqual(adapter.introspection_role.drop_failed_introspection("svc", "/Failed", failed_error), "dropped")
            debug.assert_called_once()
            self.assertEqual(
                debug.call_args.args[0], "Dropping failed DBus introspection command service=%s path=%s: %s"
            )
            self.assertEqual(debug.call_args.args[1:], ("svc", "/Failed", failed_error))
            self.assertEqual(adapter._introspection_queue_depth, 1)
            failed = adapter.cache.values["introspection:svc:/Failed"]
            self.assertEqual(failed["status"], "error")
            self.assertEqual(failed["source"], "svc/Failed")
            self.assertEqual(failed["last_error"], "bad")

            adapter._introspection_queue_depth = 1
            adapter.introspection_role.record_introspection_xml("svc", "/Zero", "<xml/>")
            self.assertEqual(adapter._introspection_queue_depth, 0)

            self.assertEqual(adapter.timed_dbus_operation("read", lambda: 42), 42)
            adapter.rate_limiter.next_at["read"] = 0.0
            with self.assertRaises(RuntimeError):
                adapter.timed_dbus_operation("read", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
            self.assertEqual(gateway_core_module._json_ready({"ok": True}), {"ok": True})
            self.assertEqual(gateway_core_module._json_ready(object()).startswith("<object object"), True)

            self.assertEqual(logging_level_from_config(adapter.config), logging.DEBUG)
            with patch.object(adapter_module.DbusAdapter, "run") as run:
                self.assertEqual(adapter_main([str(config_path), "--run-dir", str(Path(temp_dir) / "run2")]), 0)
            run.assert_called_once()

    def test_timed_operation_contracts_record_latency_and_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            require_due = install_mock(adapter.rate_limiter, "require_due", MagicMock())
            record_success = install_mock(adapter.circuit, "record_success", MagicMock())
            record_error = install_mock(adapter.circuit, "record_error", MagicMock())

            with patch.object(process_io_module.time, "monotonic", side_effect=[10.0, 10.125]):
                self.assertEqual(adapter.timed_dbus_operation("write", lambda: "ok"), "ok")
            require_due.assert_called_once_with("write")
            record_success.assert_called_once_with(125.0, kind="write")
            record_error.assert_not_called()

            require_due.reset_mock()
            record_success.reset_mock()
            error = RuntimeError("boom")
            with patch.object(process_io_module.time, "monotonic", return_value=20.0):
                with self.assertRaises(RuntimeError):
                    adapter.timed_dbus_operation("read", lambda: (_ for _ in ()).throw(error))
            require_due.assert_called_once_with("read")
            record_success.assert_not_called()
            record_error.assert_called_once_with(error, kind="read")

            require_due.reset_mock()
            record_error.reset_mock()
            record_success.reset_mock()
            with patch.object(process_io_module.time, "monotonic", side_effect=[30.0, 30.25]):
                self.assertEqual(adapter.timed_local_publish(lambda: "published"), "published")
            require_due.assert_not_called()
            record_success.assert_called_once_with(250.0, kind="local_publish")
            record_error.assert_not_called()

            record_success.reset_mock()
            error = RuntimeError("publish failed")
            with patch.object(process_io_module.time, "monotonic", return_value=40.0):
                with self.assertRaises(RuntimeError):
                    adapter.timed_local_publish(lambda: (_ for _ in ()).throw(error))
            record_success.assert_not_called()
            record_error.assert_called_once_with(error, kind="local_publish")
