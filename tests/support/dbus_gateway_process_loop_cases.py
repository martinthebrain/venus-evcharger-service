# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway adapter tick and main-loop lifecycle scenarios."""

from __future__ import annotations

from unittest.mock import call

from tests.support.dbus_gateway_adapter_harness import (
    DbusAdapter,
    GatewayAdapterContractCase,
    MagicMock,
    Path,
    evcs_registration,
    gateway_paths,
    install_read_responder,
    install_mock,
    patch,
    process_loop_module,
    tempfile,
    time,
)
from venus_evcharger.dbus_adapter.async_broker import (
    DbusAsyncOperation,
    DbusErrorHandler,
    DbusReplyHandler,
)
from venus_evcharger.dbus_adapter.async_request import DbusWireRequest


class GatewayProcessLoopCases(GatewayAdapterContractCase):
    """Exercise tick and main-loop lifecycle scenarios."""

    def test_urgent_write_precedes_initial_external_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(
                str(config_path),
                paths=gateway_paths(str(Path(temp_dir) / "run")),
            )
            self.assertEqual(
                adapter.write_scheduler.publication_executor.process(
                    evcs_registration()
                ),
                "applied",
            )
            self.assertEqual(adapter.cache.services, {})
            urgent = install_mock(
                adapter.write_scheduler,
                "process_urgent_once",
                MagicMock(return_value=True),
            )
            discovery = install_mock(
                adapter.io_role,
                "refresh_services_if_due_once",
                MagicMock(return_value=True),
            )

            self.assertTrue(adapter.loop_role.process_one_dbus_operation_once())

            urgent.assert_called_once_with()
            discovery.assert_not_called()

    def test_initial_discovery_survives_pressure_and_unlocks_energy_sources(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                "AutoBatteryService=\n"
                "AutoBatteryServicePrefix=com.victronenergy.battery\n"
                "AutoBatterySocPath=/Soc\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(
                str(config_path),
                paths=gateway_paths(str(Path(temp_dir) / "run")),
            )
            self.assertEqual(
                adapter.write_scheduler.publication_executor.process(evcs_registration()),
                "applied",
            )
            adapter.health_role.suspend_advisory_work(
                monotonic_at=100.0,
                captured_at=1000.0,
            )
            self.assertEqual(adapter.discovery.next_scan_monotonic, 0.0)

            services = [
                "com.victronenergy.system",
                "com.victronenergy.pvinverter.http_48",
                "com.victronenergy.battery.socketcan_can0",
            ]
            def complete_list_names(
                request: DbusWireRequest,
                reply_handler: object,
                _error_handler: object,
            ) -> object:
                self.assertEqual((request.method_name, request.signature), ("ListNames", ""))
                assert callable(reply_handler)
                reply_handler(services)
                return object()

            install_mock(
                adapter.connection,
                "send_async",
                MagicMock(side_effect=complete_list_names),
            )
            self.assertTrue(adapter.loop_role.process_one_dbus_operation_once())

            self.assertEqual(set(adapter.cache.services), set(services))
            self.assertGreater(adapter.discovery.last_success_at, 0.0)
            self.assertEqual(len(adapter.energy_discovery.source_ids("battery")), 1)

            install_read_responder(
                adapter,
                MagicMock(side_effect=(1250.0, 0.0)),
            )
            pv_spec = adapter.read_scheduler.specs["pv_power_w"]
            self.assertEqual(
                adapter.read_executor.poll_read_spec("pv_power_w", pv_spec),
                "deferred",
            )
            self.assertEqual(
                adapter.read_executor.poll_read_spec("pv_power_w", pv_spec),
                "applied",
            )
            self.assertEqual(adapter.cache.values["pv_power_w"]["value"], 1250.0)

            install_read_responder(adapter, MagicMock(return_value=62.0))
            battery_spec = adapter.read_scheduler.specs["battery_soc"]
            self.assertEqual(
                adapter.read_executor.poll_read_spec(
                    "battery_soc",
                    battery_spec,
                ),
                "applied",
            )
            self.assertEqual(adapter.cache.values["battery_soc"]["value"], 62.0)

    def test_initial_discovery_reports_idle_when_no_operation_can_start(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(
                str(config_path),
                paths=gateway_paths(str(Path(temp_dir) / "run")),
            )
            urgent = install_mock(
                adapter.write_scheduler,
                "process_urgent_once",
                MagicMock(return_value=False),
            )
            discovery = install_mock(
                adapter.loop_role,
                "refresh_initial_services_once",
                MagicMock(return_value=False),
            )

            self.assertIsNone(adapter.loop_role._initial_discovery_outcome())

            urgent.assert_called_once_with()
            discovery.assert_called_once_with()

    def test_busy_broker_still_drains_local_publications_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(
                str(config_path),
                paths=gateway_paths(str(Path(temp_dir) / "run")),
            )
            self.assertEqual(
                adapter.write_scheduler.publication_executor.process(evcs_registration()),
                "applied",
            )
            adapter.cache.update_services(["service"])

            def hold_operation(
                _reply: DbusReplyHandler,
                _error: DbusErrorHandler,
            ) -> object | None:
                return None

            adapter.operation_broker.submit(
                DbusAsyncOperation(
                    rate_kind="read",
                    metric_kind="read",
                    source="held/source",
                    priority="read",
                    timeout_seconds=1.0,
                    starter=hold_operation,
                    on_success=MagicMock(),
                    on_error=MagicMock(),
                )
            )
            install_mock(
                adapter.introspection_role,
                "enqueue_background_introspection_if_due",
                MagicMock(),
            )
            local_burst = install_mock(
                adapter.write_scheduler,
                "process_local_publish_burst",
                MagicMock(return_value=3),
            )
            urgent = install_mock(
                adapter.write_scheduler,
                "process_urgent_once",
                MagicMock(return_value=True),
            )
            read = install_mock(
                adapter.io_role,
                "poll_one_due_read_once",
                MagicMock(return_value=True),
            )
            discovery = install_mock(
                adapter.io_role,
                "refresh_services_if_due_once",
                MagicMock(return_value=True),
            )

            self.assertTrue(adapter.loop_role.process_one_dbus_operation_once())
            local_burst.assert_called_once_with()
            urgent.assert_not_called()
            read.assert_not_called()
            discovery.assert_not_called()
            self.assertTrue(adapter.operation_broker.busy)
            self.assertTrue(adapter.operation_broker.cancel_current("test complete"))

    def test_tick_and_dbus_operation_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            adapter._stop = True
            install_mock(adapter.socket_role, "close_socket", MagicMock())
            self.assertFalse(adapter.tick())
            adapter.socket_role.close_socket.assert_called_once()

            adapter._stop = False
            with patch.object(
                adapter.loop_role,
                "process_one_dbus_operation_once",
                side_effect=RuntimeError("tick failed"),
            ):
                self.assertTrue(adapter.tick())
            self.assertEqual(adapter.circuit.last_error, "")

            install_mock(adapter.io_role, "poll_one_due_read_once", MagicMock(return_value=True))
            install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=True))
            install_mock(adapter.io_role, "refresh_services_if_due_once", MagicMock(return_value=True))
            self.assertTrue(adapter.loop_role.process_one_dbus_operation_once())
            adapter.write_scheduler.process_one.assert_called_once_with(
                include_local_publish=False,
                required_kind="register_evcs",
            )
            adapter.io_role.refresh_services_if_due_once.assert_not_called()
            adapter.io_role.poll_one_due_read_once.assert_not_called()

            install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=False))
            self.assertFalse(adapter.loop_role.process_one_dbus_operation_once())
            adapter.io_role.refresh_services_if_due_once.assert_not_called()
            adapter.io_role.poll_one_due_read_once.assert_not_called()

            self.assertEqual(adapter.write_scheduler.publication_executor.process(evcs_registration()), "applied")
            install_mock(adapter.io_role, "refresh_services_if_due_once", MagicMock(return_value=True))
            self.assertTrue(adapter.loop_role.process_one_dbus_operation_once())
            adapter.io_role.refresh_services_if_due_once.assert_called_once()

            adapter.cache.update_services(["svc"])
            install_mock(adapter.io_role, "refresh_services_if_due_once", MagicMock(return_value=True))
            install_mock(adapter.io_role, "poll_one_due_read_once", MagicMock(return_value=True))
            install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=True))
            self.assertTrue(adapter.loop_role.process_one_dbus_operation_once())
            adapter.io_role.poll_one_due_read_once.assert_called_once()
            adapter.write_scheduler.process_one.assert_not_called()
            install_mock(adapter.io_role, "poll_one_due_read_once", MagicMock(return_value=False))
            install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=True))
            self.assertTrue(adapter.loop_role.process_one_dbus_operation_once())
            adapter._prefer_read_next = True
            install_mock(adapter.io_role, "poll_one_due_read_once", MagicMock(return_value=True))
            install_mock(adapter.loop_role, "reads_need_priority", MagicMock(return_value=False))
            self.assertTrue(adapter.loop_role.try_read_then_write())
            self.assertFalse(adapter._prefer_read_next)
            adapter._prefer_read_next = True
            install_mock(adapter.io_role, "poll_one_due_read_once", MagicMock(return_value=False))
            install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=False))
            install_mock(adapter.io_role, "refresh_services_if_due_once", MagicMock(return_value=False))
            self.assertFalse(adapter.loop_role.process_one_dbus_operation_once())
            adapter._prefer_read_next = False
            install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=True))
            adapter.write_scheduler.command_queue.last_scheduled_outcome = "applied"
            install_mock(adapter.io_role, "poll_one_due_read_once", MagicMock(return_value=True))
            self.assertTrue(adapter.loop_role.try_write_then_read())
            adapter.io_role.poll_one_due_read_once.assert_not_called()
            adapter._prefer_read_next = True
            install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=False))
            install_mock(adapter.io_role, "poll_one_due_read_once", MagicMock(return_value=True))
            self.assertTrue(adapter.loop_role.try_write_then_read())
            self.assertFalse(adapter._prefer_read_next)
            install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=False))
            install_mock(adapter.io_role, "poll_one_due_read_once", MagicMock(return_value=True))
            self.assertTrue(adapter.loop_role.process_one_dbus_operation_once())
            self.assertFalse(adapter._prefer_read_next)
            install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=False))
            install_mock(adapter.io_role, "poll_one_due_read_once", MagicMock(return_value=False))
            install_mock(adapter.io_role, "refresh_services_if_due_once", MagicMock(return_value=True))
            self.assertTrue(adapter.loop_role.process_one_dbus_operation_once())

            priority_adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-priority")))
            self.assertEqual(
                priority_adapter.write_scheduler.publication_executor.process(evcs_registration()),
                "applied",
            )
            priority_adapter.cache.update_services(["svc"])
            install_mock(priority_adapter.introspection_role, "enqueue_background_introspection_if_due", MagicMock())
            install_mock(priority_adapter.io_role, "poll_one_due_read_once", MagicMock(return_value=True))
            install_mock(
                priority_adapter.write_scheduler,
                "process_local_publish_burst",
                MagicMock(return_value=5),
            )
            self.assertTrue(priority_adapter.loop_role.process_one_dbus_operation_once())
            priority_adapter.io_role.poll_one_due_read_once.assert_called_once()
            priority_adapter.write_scheduler.process_local_publish_burst.assert_called_once()

            install_mock(
                priority_adapter.write_scheduler,
                "process_urgent_once",
                MagicMock(return_value=True),
            )
            priority_adapter.io_role.poll_one_due_read_once.reset_mock()
            self.assertTrue(priority_adapter.loop_role.process_one_dbus_operation_once())
            priority_adapter.write_scheduler.process_urgent_once.assert_called_once()
            priority_adapter.io_role.poll_one_due_read_once.assert_not_called()

            aggregate_adapter = DbusAdapter(
                str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-aggregate"))
            )
            self.assertEqual(
                aggregate_adapter.write_scheduler.publication_executor.process(evcs_registration()),
                "applied",
            )
            aggregate_adapter.cache.update_services(["svc"])
            now = time.time()
            for key in ("grid_power_w", "pv_power_w", "battery_soc"):
                aggregate_adapter.cache.update_value(key, 1.0, source="test", now=now)
            install_read_responder(aggregate_adapter, MagicMock(return_value=1.0))
            self.assertEqual(
                aggregate_adapter.read_executor.poll_read_spec(
                    "pv_power_w",
                    {"aggregate": "sum", "service": "svc", "paths": ["/L1", "/L2"]},
                ),
                "deferred",
            )
            install_mock(aggregate_adapter.introspection_role, "enqueue_background_introspection_if_due", MagicMock())
            install_mock(aggregate_adapter.io_role, "poll_one_due_read_once", MagicMock(return_value=True))
            install_mock(
                aggregate_adapter.write_scheduler,
                "process_local_publish_burst",
                MagicMock(return_value=5),
            )
            self.assertTrue(aggregate_adapter.loop_role.process_one_dbus_operation_once())
            aggregate_adapter.io_role.poll_one_due_read_once.assert_called_once()
            aggregate_adapter.write_scheduler.process_local_publish_burst.assert_called_once()

    def test_tick_records_lifecycle_and_honors_stop_after_work(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter._next_work_tick_monotonic = 100.0
            install_mock(adapter.loop_role, "process_one_dbus_operation_once", MagicMock())
            install_mock(adapter.io_role, "publish_cache", MagicMock())
            install_mock(adapter.tick_health, "record", MagicMock())
            initial_tick_interval = adapter.tick_seconds

            def adapt_tick(_control: object) -> None:
                adapter.tick_seconds = adapter.max_tick_seconds

            install_mock(
                adapter.loop_role,
                "update_adaptive_tick",
                MagicMock(side_effect=adapt_tick),
            )
            control = object()
            install_mock(adapter.health_role, "control_snapshot", MagicMock(return_value=control))

            with (
                patch.object(process_loop_module.time, "time", return_value=123.0),
                patch.object(
                    process_loop_module.time,
                    "monotonic",
                    side_effect=[100.0, 100.01, 100.03],
                ),
            ):
                self.assertTrue(adapter.tick())

            self.assertEqual(adapter._last_tick_at, 123.0)
            self.assertEqual(adapter._last_tick_monotonic, 100.0)
            self.assertAlmostEqual(adapter._last_tick_duration_ms, 10.0)
            adapter.tick_health.record.assert_called_once_with(
                duration_ms=adapter._last_tick_duration_ms,
                expected_interval_s=initial_tick_interval,
                scheduled_at=100.0,
                now=100.0,
            )
            adapter.loop_role.update_adaptive_tick.assert_called_once_with(control)
            self.assertEqual(adapter.tick_seconds, adapter.max_tick_seconds)
            self.assertAlmostEqual(
                adapter._next_work_tick_monotonic,
                100.0 + adapter.tick_seconds,
            )
            adapter.loop_role.process_one_dbus_operation_once.assert_called_once()
            adapter.io_role.publish_cache.assert_called_once_with(control)

            deferred_adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-deferred")))
            deferred_adapter._next_work_tick_monotonic = 100.01
            install_mock(
                deferred_adapter.operation_broker,
                "expire_due",
                MagicMock(return_value=False),
            )
            with patch.object(process_loop_module.time, "monotonic", return_value=100.0):
                self.assertTrue(deferred_adapter.tick())
            deferred_adapter.operation_broker.expire_due.assert_called_once_with(now=100.0)

            stop_adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-stop")))
            stop_adapter._next_work_tick_monotonic = 0.0

            def stop_during_work() -> None:
                stop_adapter._stop = True

            install_mock(
                stop_adapter.loop_role,
                "process_one_dbus_operation_once",
                MagicMock(side_effect=stop_during_work),
            )
            install_mock(stop_adapter.io_role, "publish_cache", MagicMock())
            stop_control = object()
            install_mock(stop_adapter.health_role, "control_snapshot", MagicMock(return_value=stop_control))
            install_mock(stop_adapter.loop_role, "update_adaptive_tick", MagicMock())
            self.assertFalse(stop_adapter.tick())
            stop_adapter.loop_role.process_one_dbus_operation_once.assert_called_once()
            stop_adapter.io_role.publish_cache.assert_called_once_with(stop_control)

    def test_run_uses_minimum_timer_interval_for_fast_ticks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nDbusGatewayMinTickSeconds=0.05\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-fast")))
            lifecycle: list[str] = []
            install_mock(
                adapter.connection,
                "connect",
                MagicMock(side_effect=lambda: lifecycle.append("connect")),
            )
            install_mock(
                adapter.runtime_role,
                "install_signal_handlers",
                MagicMock(side_effect=lambda: lifecycle.append("signals")),
            )
            install_mock(adapter.socket_role, "start_socket", MagicMock())
            install_mock(adapter.socket_role, "install_glib_watch", MagicMock())
            install_mock(adapter.socket_role, "close_socket", MagicMock())
            main_loop = MagicMock()
            main_loop.run.side_effect = lambda: lifecycle.append("run")

            with (
                patch.object(
                    process_loop_module,
                    "DBusGMainLoop",
                    side_effect=lambda **_kwargs: lifecycle.append("mainloop-binding"),
                ),
                patch.object(
                    process_loop_module.GLib,
                    "MainLoop",
                    return_value=main_loop,
                ),
                patch.object(process_loop_module.GLib, "timeout_add", return_value=123) as timeout_add,
            ):
                adapter.run()

            self.assertEqual(
                timeout_add.call_args_list,
                [call(50, adapter.loop_role.tick)],
            )
            self.assertEqual(
                lifecycle,
                ["mainloop-binding", "connect", "signals", "run"],
            )

    def test_tick_recovery_records_and_logs_gateway_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter._next_work_tick_monotonic = 0.0
            error = RuntimeError("tick boom")
            install_mock(
                adapter.loop_role,
                "process_one_dbus_operation_once",
                MagicMock(side_effect=error),
            )
            install_mock(adapter.io_role, "publish_cache", MagicMock())
            install_mock(adapter.circuit, "record_error", MagicMock())

            with patch.object(process_loop_module.logging, "exception") as log_exception:
                self.assertTrue(adapter.tick())

            adapter.circuit.record_error.assert_not_called()
            log_exception.assert_called_once_with(
                "Gateway work tick failed outside the DBus circuit: %s",
                error,
            )
            adapter.loop_role.process_one_dbus_operation_once.assert_called_once()
            adapter.io_role.publish_cache.assert_not_called()

    def test_tick_does_not_end_a_scheduler_tick_that_never_began(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(
                str(config_path),
                paths=gateway_paths(str(Path(temp_dir) / "run")),
            )
            adapter._next_work_tick_monotonic = 0.0
            install_mock(
                adapter.write_scheduler,
                "begin_tick",
                MagicMock(side_effect=RuntimeError("begin failed")),
            )
            install_mock(adapter.write_scheduler, "end_tick", MagicMock())

            self.assertTrue(adapter.tick())

            adapter.write_scheduler.end_tick.assert_not_called()

    def test_loop_core_read_freshness_and_priority_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            stale_sentinel = adapter.slo_core_read_max_age_seconds + 1.0

            self.assertEqual(adapter.loop_role.core_read_age("grid_power_w", 100.0), stale_sentinel)
            adapter.cache.values["grid_power_w"] = {"updated_at": 0.0}
            self.assertEqual(adapter.loop_role.core_read_age("grid_power_w", 100.0), stale_sentinel)
            adapter.cache.values["grid_power_w"] = {"updated_at": "bad"}
            self.assertEqual(adapter.loop_role.core_read_age("grid_power_w", 100.0), stale_sentinel)
            adapter.cache.values["grid_power_w"] = {"updated_at": 0.5}
            self.assertEqual(adapter.loop_role.core_read_age("grid_power_w", 100.0), 99.5)
            adapter.cache.values["grid_power_w"] = {"updated_at": 95.0}
            self.assertEqual(adapter.loop_role.core_read_age("grid_power_w", 100.0), 5.0)
            adapter.cache.values["grid_power_w"] = {"updated_at": 105.0}
            self.assertEqual(adapter.loop_role.core_read_age("grid_power_w", 100.0), -5.0)

            now = 200.0
            for key in ("grid_power_w", "pv_power_w", "battery_soc"):
                adapter.cache.values[key] = {"updated_at": now - adapter.slo_core_read_max_age_seconds}
            with patch.object(process_loop_module.time, "time", return_value=now):
                self.assertFalse(adapter.loop_role.core_reads_stale())
                self.assertFalse(adapter.loop_role.reads_need_priority())

            adapter.cache.values["pv_power_w"] = {"updated_at": now - adapter.slo_core_read_max_age_seconds - 0.01}
            with patch.object(process_loop_module.time, "time", return_value=now):
                self.assertTrue(adapter.loop_role.core_reads_stale())
                self.assertTrue(adapter.loop_role.reads_need_priority())

            adapter.cache.values["pv_power_w"] = {"updated_at": now}
            install_mock(adapter.read_executor, "has_pending_aggregate", MagicMock(return_value=True))
            with patch.object(process_loop_module.time, "time", return_value=now):
                self.assertTrue(adapter.loop_role.reads_need_priority())
            adapter.read_executor.has_pending_aggregate.assert_called_once()

    def test_loop_read_write_preference_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            install_mock(adapter.io_role, "poll_one_due_read_once", MagicMock(return_value=True))
            install_mock(adapter.loop_role, "reads_need_priority", MagicMock(return_value=True))
            install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=True))
            adapter._prefer_read_next = False
            self.assertTrue(adapter.loop_role.try_read_then_write())
            self.assertTrue(adapter._prefer_read_next)
            adapter.write_scheduler.process_one.assert_not_called()

            install_mock(adapter.io_role, "poll_one_due_read_once", MagicMock(return_value=False))
            install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=True))
            adapter._prefer_read_next = False
            self.assertTrue(adapter.loop_role.try_read_then_write())
            adapter.write_scheduler.process_one.assert_called_once_with(include_local_publish=False)
            self.assertTrue(adapter._prefer_read_next)

            install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=False))
            adapter._prefer_read_next = False
            self.assertFalse(adapter.loop_role.try_scheduled_write(prefer_read_next=True))
            self.assertFalse(adapter._prefer_read_next)

            install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=True))
            adapter.write_scheduler.command_queue.last_scheduled_outcome = "applied"
            adapter._prefer_read_next = True
            self.assertTrue(adapter.loop_role.try_scheduled_write(prefer_read_next=False))
            self.assertIs(adapter._prefer_read_next, False)
            adapter.write_scheduler.process_one.assert_called_once_with(include_local_publish=False)

            install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=True))
            adapter.write_scheduler.command_queue.last_scheduled_outcome = "deferred"
            adapter._prefer_read_next = True
            self.assertTrue(adapter.loop_role.try_scheduled_write(prefer_read_next=True))
            self.assertIs(adapter._prefer_read_next, False)

            install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=False))
            install_mock(adapter.io_role, "poll_one_due_read_once", MagicMock(return_value=False))
            self.assertFalse(adapter.loop_role.try_write_then_read())
            adapter.io_role.poll_one_due_read_once.assert_called_once()

            install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=True))
            install_mock(adapter.io_role, "poll_one_due_read_once", MagicMock(return_value=True))
            adapter.write_scheduler.command_queue.last_scheduled_outcome = "applied"
            adapter._prefer_read_next = False
            self.assertTrue(adapter.loop_role.try_write_then_read())
            self.assertTrue(adapter._prefer_read_next)
            adapter.write_scheduler.process_one.assert_called_once_with(include_local_publish=False)
            adapter.io_role.poll_one_due_read_once.assert_not_called()

            install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=False))
            install_mock(adapter.io_role, "poll_one_due_read_once", MagicMock(return_value=True))
            adapter._prefer_read_next = True
            self.assertTrue(adapter.loop_role.try_write_then_read())
            self.assertIs(adapter._prefer_read_next, False)

    def test_standard_operation_and_adaptive_tick_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            install_mock(adapter.write_scheduler, "process_local_publish_burst", MagicMock(return_value=3))
            install_mock(adapter.loop_role, "process_preferred_read_or_write", MagicMock(return_value=True))
            install_mock(adapter.io_role, "refresh_services_if_due_once", MagicMock(return_value=True))
            self.assertTrue(adapter.loop_role.process_standard_operation_once(local_publish_count=3))
            adapter.write_scheduler.process_local_publish_burst.assert_not_called()
            adapter.loop_role.process_preferred_read_or_write.assert_called_once()
            adapter.io_role.refresh_services_if_due_once.assert_not_called()

            install_mock(adapter.write_scheduler, "process_local_publish_burst", MagicMock(return_value=2))
            install_mock(adapter.loop_role, "process_preferred_read_or_write", MagicMock(return_value=False))
            install_mock(adapter.io_role, "refresh_services_if_due_once", MagicMock(return_value=False))
            self.assertTrue(adapter.loop_role.process_standard_operation_once(local_publish_count=2))

            install_mock(adapter.write_scheduler, "process_local_publish_burst", MagicMock(return_value=0))
            install_mock(adapter.loop_role, "process_preferred_read_or_write", MagicMock(return_value=False))
            install_mock(adapter.io_role, "refresh_services_if_due_once", MagicMock(return_value=False))
            self.assertFalse(adapter.loop_role.process_standard_operation_once())

            adapter.tick_health.record(
                duration_ms=adapter.slo_mainloop_gap_max_ms + 1.0,
                expected_interval_s=adapter.tick_seconds,
                now=time.monotonic(),
            )
            install_mock(adapter.resource_monitor, "snapshot", MagicMock(return_value={"state": "ok"}))
            install_mock(adapter.circuit, "state", MagicMock(return_value="ok"))
            adapter.loop_role.update_adaptive_tick()
            self.assertEqual(adapter._last_resource_snapshot, {"state": "ok"})
            self.assertAlmostEqual(adapter.tick_seconds, 0.3)

            install_mock(adapter.resource_monitor, "snapshot", MagicMock(return_value={"state": "constrained"}))
            install_mock(adapter.circuit, "state", MagicMock(return_value="degraded"))
            adapter.loop_role.update_adaptive_tick()
            self.assertEqual(adapter.tick_seconds, 1.0)

            boundary_adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-boundary")))
            install_mock(boundary_adapter.resource_monitor, "snapshot", MagicMock(return_value={"state": "ok"}))
            install_mock(
                boundary_adapter.tick_health,
                "snapshot",
                MagicMock(return_value={"max_tick_duration_ms_60s": boundary_adapter.slo_mainloop_gap_max_ms}),
            )
            install_mock(boundary_adapter.circuit, "state", MagicMock(return_value="ok"))
            boundary_adapter.loop_role.update_adaptive_tick()
            self.assertEqual(boundary_adapter.tick_seconds, boundary_adapter.min_tick_seconds)

            missing_state_adapter = DbusAdapter(
                str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-missing-state"))
            )
            install_mock(missing_state_adapter.resource_monitor, "snapshot", MagicMock(return_value={}))
            install_mock(
                missing_state_adapter.tick_health,
                "snapshot",
                MagicMock(
                    return_value={"max_tick_duration_ms_60s": missing_state_adapter.slo_mainloop_gap_max_ms + 1.0}
                ),
            )
            install_mock(missing_state_adapter.circuit, "state", MagicMock(return_value="ok"))
            missing_state_adapter.loop_role.update_adaptive_tick()
            self.assertAlmostEqual(missing_state_adapter.tick_seconds, 0.3)

            degraded_adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-degraded")))
            install_mock(degraded_adapter.resource_monitor, "snapshot", MagicMock(return_value={"state": "ok"}))
            install_mock(
                degraded_adapter.tick_health,
                "snapshot",
                MagicMock(return_value={"max_tick_duration_ms_60s": 0.0}),
            )
            install_mock(degraded_adapter.circuit, "state", MagicMock(return_value="degraded"))
            degraded_adapter.loop_role.update_adaptive_tick()
            self.assertAlmostEqual(degraded_adapter.tick_seconds, 0.5)

            tuning_adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-tuning")))
            tuning_adapter.min_tick_seconds = 0.4
            tuning_adapter.max_tick_seconds = 2.0
            self.assertAlmostEqual(
                tuning_adapter.loop_role.adaptive_tick_seconds(circuit_state="degraded", resource_state="ok"),
                1.0,
            )
            self.assertAlmostEqual(
                tuning_adapter.loop_role.adaptive_tick_seconds(circuit_state="ok", resource_state="busy"),
                0.6,
            )
