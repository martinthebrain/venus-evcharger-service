# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway adapter tick and main-loop lifecycle scenarios."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    DbusAdapter,
    GatewayAdapterContractCase,
    MagicMock,
    Path,
    evcs_registration,
    gateway_paths,
    install_mock,
    patch,
    process_loop_module,
    tempfile,
    time,
)


class GatewayProcessLoopCases(GatewayAdapterContractCase):
    """Exercise tick and main-loop lifecycle scenarios."""

    def test_tick_and_dbus_operation_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            adapter._stop = True
            install_mock(adapter, "close_socket", MagicMock())
            self.assertFalse(adapter.tick())
            adapter.close_socket.assert_called_once()

            adapter._stop = False
            install_mock(adapter, "process_socket_once", MagicMock(side_effect=RuntimeError("tick failed")))
            self.assertTrue(adapter.tick())
            self.assertEqual(adapter.circuit.last_error, "tick failed")

            install_mock(adapter, "poll_one_due_read_once", MagicMock(return_value=True))
            install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=True))
            install_mock(adapter, "refresh_services_if_due_once", MagicMock(return_value=True))
            self.assertTrue(adapter.process_one_dbus_operation_once())
            adapter.write_scheduler.process_one.assert_called_once_with(
                include_local_publish=False,
                required_kind="register_evcs",
            )
            adapter.refresh_services_if_due_once.assert_not_called()
            adapter.poll_one_due_read_once.assert_not_called()

            install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=False))
            self.assertFalse(adapter.process_one_dbus_operation_once())
            adapter.refresh_services_if_due_once.assert_not_called()
            adapter.poll_one_due_read_once.assert_not_called()

            self.assertEqual(adapter.write_scheduler.process_publication(evcs_registration()), "applied")
            install_mock(adapter, "refresh_services_if_due_once", MagicMock(return_value=True))
            self.assertTrue(adapter.process_one_dbus_operation_once())
            adapter.refresh_services_if_due_once.assert_called_once()

            adapter.cache.update_services(["svc"])
            install_mock(adapter, "refresh_services_if_due_once", MagicMock(return_value=True))
            install_mock(adapter, "poll_one_due_read_once", MagicMock(return_value=True))
            install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=True))
            self.assertTrue(adapter.process_one_dbus_operation_once())
            adapter.poll_one_due_read_once.assert_called_once()
            adapter.write_scheduler.process_one.assert_not_called()
            install_mock(adapter, "poll_one_due_read_once", MagicMock(return_value=False))
            install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=True))
            self.assertTrue(adapter.process_one_dbus_operation_once())
            adapter._prefer_read_next = True
            install_mock(adapter, "poll_one_due_read_once", MagicMock(return_value=True))
            install_mock(adapter, "reads_need_priority", MagicMock(return_value=False))
            self.assertTrue(adapter.try_read_then_write())
            self.assertFalse(adapter._prefer_read_next)
            adapter._prefer_read_next = True
            install_mock(adapter, "poll_one_due_read_once", MagicMock(return_value=False))
            install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=False))
            install_mock(adapter, "refresh_services_if_due_once", MagicMock(return_value=False))
            self.assertFalse(adapter.process_one_dbus_operation_once())
            adapter._prefer_read_next = False
            install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=True))
            adapter.write_scheduler.last_scheduled_outcome = "applied"
            install_mock(adapter, "poll_one_due_read_once", MagicMock(return_value=True))
            self.assertTrue(adapter.try_write_then_read())
            adapter.poll_one_due_read_once.assert_not_called()
            adapter._prefer_read_next = True
            install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=False))
            install_mock(adapter, "poll_one_due_read_once", MagicMock(return_value=True))
            self.assertTrue(adapter.try_write_then_read())
            self.assertFalse(adapter._prefer_read_next)
            install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=False))
            install_mock(adapter, "poll_one_due_read_once", MagicMock(return_value=True))
            self.assertTrue(adapter.process_one_dbus_operation_once())
            self.assertFalse(adapter._prefer_read_next)
            install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=False))
            install_mock(adapter, "poll_one_due_read_once", MagicMock(return_value=False))
            install_mock(adapter, "refresh_services_if_due_once", MagicMock(return_value=True))
            self.assertTrue(adapter.process_one_dbus_operation_once())

            priority_adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-priority")))
            self.assertEqual(
                priority_adapter.write_scheduler.process_publication(evcs_registration()),
                "applied",
            )
            priority_adapter.cache.update_services(["svc"])
            install_mock(priority_adapter, "enqueue_background_introspection_if_due", MagicMock())
            install_mock(priority_adapter, "poll_one_due_read_once", MagicMock(return_value=True))
            install_mock(
                priority_adapter.write_scheduler,
                "process_local_publish_burst",
                MagicMock(return_value=5),
            )
            self.assertTrue(priority_adapter.process_one_dbus_operation_once())
            priority_adapter.poll_one_due_read_once.assert_called_once()
            priority_adapter.write_scheduler.process_local_publish_burst.assert_not_called()

            aggregate_adapter = DbusAdapter(
                str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-aggregate"))
            )
            self.assertEqual(
                aggregate_adapter.write_scheduler.process_publication(evcs_registration()),
                "applied",
            )
            aggregate_adapter.cache.update_services(["svc"])
            now = time.time()
            for key in ("grid_power_w", "pv_power_w", "battery_soc"):
                aggregate_adapter.cache.update_value(key, 1.0, source="test", now=now)
            install_mock(aggregate_adapter.read_executor, "read_busitem", MagicMock(return_value=1.0))
            self.assertEqual(
                aggregate_adapter.read_executor.poll_read_spec(
                    "pv_power_w",
                    {"aggregate": "sum", "service": "svc", "paths": ["/L1", "/L2"]},
                ),
                "deferred",
            )
            install_mock(aggregate_adapter, "enqueue_background_introspection_if_due", MagicMock())
            install_mock(aggregate_adapter, "poll_one_due_read_once", MagicMock(return_value=True))
            install_mock(
                aggregate_adapter.write_scheduler,
                "process_local_publish_burst",
                MagicMock(return_value=5),
            )
            self.assertTrue(aggregate_adapter.process_one_dbus_operation_once())
            aggregate_adapter.poll_one_due_read_once.assert_called_once()
            aggregate_adapter.write_scheduler.process_local_publish_burst.assert_not_called()

    def test_tick_records_lifecycle_and_honors_stop_after_work(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter._next_work_tick_monotonic = 100.0
            install_mock(adapter, "process_socket_once", MagicMock())
            install_mock(adapter, "process_one_dbus_operation_once", MagicMock())
            install_mock(adapter, "publish_cache", MagicMock())
            install_mock(adapter.tick_health, "record", MagicMock())
            install_mock(adapter, "update_adaptive_tick", MagicMock())

            with (
                patch.object(process_loop_module.time, "time", return_value=123.0),
                patch.object(
                    process_loop_module.time,
                    "monotonic",
                    side_effect=[100.0, 100.03, 100.04],
                ),
            ):
                self.assertTrue(adapter.tick())

            self.assertEqual(adapter._last_tick_at, 123.0)
            self.assertEqual(adapter._last_tick_monotonic, 100.0)
            self.assertAlmostEqual(adapter._last_tick_duration_ms, 30.0)
            adapter.tick_health.record.assert_called_once_with(
                duration_ms=adapter._last_tick_duration_ms,
                expected_interval_s=adapter.tick_seconds,
                now=100.0,
            )
            adapter.update_adaptive_tick.assert_called_once()
            self.assertAlmostEqual(adapter._next_work_tick_monotonic, 100.04 + adapter.tick_seconds)
            adapter.process_socket_once.assert_called_once()
            adapter.process_one_dbus_operation_once.assert_called_once()
            adapter.publish_cache.assert_called_once()

            deferred_adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-deferred")))
            deferred_adapter._next_work_tick_monotonic = 100.01
            install_mock(deferred_adapter, "process_socket_once", MagicMock())
            with patch.object(process_loop_module.time, "monotonic", return_value=100.0):
                self.assertTrue(deferred_adapter.tick())
            deferred_adapter.process_socket_once.assert_not_called()

            stop_adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-stop")))
            stop_adapter._next_work_tick_monotonic = 0.0

            def stop_during_work() -> None:
                stop_adapter._stop = True

            install_mock(stop_adapter, "process_socket_once", MagicMock(side_effect=stop_during_work))
            install_mock(stop_adapter, "process_one_dbus_operation_once", MagicMock())
            install_mock(stop_adapter, "publish_cache", MagicMock())
            self.assertFalse(stop_adapter.tick())
            stop_adapter.process_one_dbus_operation_once.assert_called_once()
            stop_adapter.publish_cache.assert_called_once()

    def test_run_initializes_gateway_loop_and_closes_transport(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nDbusGatewayMinTickSeconds=0.9995\n", encoding="utf-8")
            paths = gateway_paths(str(Path(temp_dir) / "run"))
            Path(paths.run_dir).mkdir(parents=True)
            Path(paths.command_dir).mkdir(parents=True)
            Path(paths.core_command_dir).mkdir(parents=True)
            adapter = DbusAdapter(str(config_path), paths=paths)
            fake_loop = MagicMock()
            install_mock(adapter, "install_signal_handlers", MagicMock())
            install_mock(adapter, "start_socket", MagicMock())
            install_mock(adapter, "ensure_dbus_service", MagicMock())
            install_mock(adapter, "close_socket", MagicMock())

            with (
                patch.object(process_loop_module, "DBusGMainLoop") as dbus_mainloop,
                patch.object(
                    process_loop_module.GLib,
                    "MainLoop",
                    return_value=fake_loop,
                ) as main_loop_factory,
                patch.object(process_loop_module.GLib, "timeout_add", return_value=123) as timeout_add,
            ):
                adapter.run()

            dbus_mainloop.assert_called_once_with(set_as_default=True)
            adapter.install_signal_handlers.assert_called_once()
            self.assertTrue(Path(paths.run_dir).is_dir())
            self.assertTrue(Path(paths.command_dir).is_dir())
            self.assertTrue(Path(paths.core_command_dir).is_dir())
            adapter.start_socket.assert_called_once()
            adapter.ensure_dbus_service.assert_called_once()
            main_loop_factory.assert_called_once_with()
            timeout_add.assert_called_once_with(max(50, int(adapter.min_tick_seconds * 1000)), adapter.tick)
            fake_loop.run.assert_called_once_with()
            self.assertIs(adapter._main_loop, fake_loop)
            self.assertTrue(adapter._stop)
            adapter.close_socket.assert_called_once()

    def test_run_uses_minimum_timer_interval_for_fast_ticks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\nDbusGatewayMinTickSeconds=0.05\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-fast")))
            install_mock(adapter, "install_signal_handlers", MagicMock())
            install_mock(adapter, "start_socket", MagicMock())
            install_mock(adapter, "ensure_dbus_service", MagicMock())
            install_mock(adapter, "close_socket", MagicMock())

            with (
                patch.object(process_loop_module, "DBusGMainLoop"),
                patch.object(
                    process_loop_module.GLib,
                    "MainLoop",
                    return_value=MagicMock(),
                ),
                patch.object(process_loop_module.GLib, "timeout_add", return_value=123) as timeout_add,
            ):
                adapter.run()

            timeout_add.assert_called_once_with(50, adapter.tick)

    def test_tick_recovery_records_and_logs_gateway_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter._next_work_tick_monotonic = 0.0
            error = RuntimeError("tick boom")
            install_mock(adapter, "process_socket_once", MagicMock(side_effect=error))
            install_mock(adapter, "process_one_dbus_operation_once", MagicMock())
            install_mock(adapter, "publish_cache", MagicMock())
            install_mock(adapter.circuit, "record_error", MagicMock())

            with patch.object(process_loop_module.logging, "exception") as log_exception:
                self.assertTrue(adapter.tick())

            adapter.circuit.record_error.assert_called_once_with(error)
            log_exception.assert_called_once_with("DBus adapter tick failed: %s", error)
            adapter.process_one_dbus_operation_once.assert_not_called()
            adapter.publish_cache.assert_not_called()

    def test_loop_core_read_freshness_and_priority_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            stale_sentinel = adapter.slo_core_read_max_age_seconds + 1.0

            self.assertEqual(adapter.core_read_age("grid_power_w", 100.0), stale_sentinel)
            adapter.cache.values["grid_power_w"] = {"updated_at": 0.0}
            self.assertEqual(adapter.core_read_age("grid_power_w", 100.0), stale_sentinel)
            adapter.cache.values["grid_power_w"] = {"updated_at": "bad"}
            self.assertEqual(adapter.core_read_age("grid_power_w", 100.0), stale_sentinel)
            adapter.cache.values["grid_power_w"] = {"updated_at": 0.5}
            self.assertEqual(adapter.core_read_age("grid_power_w", 100.0), 99.5)
            adapter.cache.values["grid_power_w"] = {"updated_at": 95.0}
            self.assertEqual(adapter.core_read_age("grid_power_w", 100.0), 5.0)
            adapter.cache.values["grid_power_w"] = {"updated_at": 105.0}
            self.assertEqual(adapter.core_read_age("grid_power_w", 100.0), -5.0)

            now = 200.0
            for key in ("grid_power_w", "pv_power_w", "battery_soc"):
                adapter.cache.values[key] = {"updated_at": now - adapter.slo_core_read_max_age_seconds}
            with patch.object(process_loop_module.time, "time", return_value=now):
                self.assertFalse(adapter.core_reads_stale())
                self.assertFalse(adapter.reads_need_priority())

            adapter.cache.values["pv_power_w"] = {"updated_at": now - adapter.slo_core_read_max_age_seconds - 0.01}
            with patch.object(process_loop_module.time, "time", return_value=now):
                self.assertTrue(adapter.core_reads_stale())
                self.assertTrue(adapter.reads_need_priority())

            adapter.cache.values["pv_power_w"] = {"updated_at": now}
            install_mock(adapter.read_executor, "has_pending_aggregate", MagicMock(return_value=True))
            with patch.object(process_loop_module.time, "time", return_value=now):
                self.assertTrue(adapter.reads_need_priority())
            adapter.read_executor.has_pending_aggregate.assert_called_once()

    def test_loop_read_write_preference_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            install_mock(adapter, "poll_one_due_read_once", MagicMock(return_value=True))
            install_mock(adapter, "reads_need_priority", MagicMock(return_value=True))
            install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=True))
            adapter._prefer_read_next = False
            self.assertTrue(adapter.try_read_then_write())
            self.assertTrue(adapter._prefer_read_next)
            adapter.write_scheduler.process_one.assert_not_called()

            install_mock(adapter, "poll_one_due_read_once", MagicMock(return_value=False))
            install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=True))
            adapter._prefer_read_next = False
            self.assertTrue(adapter.try_read_then_write())
            adapter.write_scheduler.process_one.assert_called_once_with(include_local_publish=False)
            self.assertTrue(adapter._prefer_read_next)

            install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=False))
            adapter._prefer_read_next = False
            self.assertFalse(adapter.try_scheduled_write(prefer_read_next=True))
            self.assertFalse(adapter._prefer_read_next)

            install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=True))
            adapter.write_scheduler.last_scheduled_outcome = "applied"
            adapter._prefer_read_next = True
            self.assertTrue(adapter.try_scheduled_write(prefer_read_next=False))
            self.assertIs(adapter._prefer_read_next, False)
            adapter.write_scheduler.process_one.assert_called_once_with(include_local_publish=False)

            install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=True))
            adapter.write_scheduler.last_scheduled_outcome = "deferred"
            adapter._prefer_read_next = True
            self.assertTrue(adapter.try_scheduled_write(prefer_read_next=True))
            self.assertIs(adapter._prefer_read_next, False)

            install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=False))
            install_mock(adapter, "poll_one_due_read_once", MagicMock(return_value=False))
            self.assertFalse(adapter.try_write_then_read())
            adapter.poll_one_due_read_once.assert_called_once()

            install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=True))
            install_mock(adapter, "poll_one_due_read_once", MagicMock(return_value=True))
            adapter.write_scheduler.last_scheduled_outcome = "applied"
            adapter._prefer_read_next = False
            self.assertTrue(adapter.try_write_then_read())
            self.assertTrue(adapter._prefer_read_next)
            adapter.write_scheduler.process_one.assert_called_once_with(include_local_publish=False)
            adapter.poll_one_due_read_once.assert_not_called()

            install_mock(adapter.write_scheduler, "process_one", MagicMock(return_value=False))
            install_mock(adapter, "poll_one_due_read_once", MagicMock(return_value=True))
            adapter._prefer_read_next = True
            self.assertTrue(adapter.try_write_then_read())
            self.assertIs(adapter._prefer_read_next, False)

    def test_standard_operation_and_adaptive_tick_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            install_mock(adapter.write_scheduler, "process_local_publish_burst", MagicMock(return_value=3))
            install_mock(adapter, "process_preferred_read_or_write", MagicMock(return_value=True))
            install_mock(adapter, "refresh_services_if_due_once", MagicMock(return_value=True))
            self.assertTrue(adapter.process_standard_operation_once())
            adapter.write_scheduler.process_local_publish_burst.assert_called_once()
            adapter.process_preferred_read_or_write.assert_called_once()
            adapter.refresh_services_if_due_once.assert_not_called()

            install_mock(adapter.write_scheduler, "process_local_publish_burst", MagicMock(return_value=2))
            install_mock(adapter, "process_preferred_read_or_write", MagicMock(return_value=False))
            install_mock(adapter, "refresh_services_if_due_once", MagicMock(return_value=False))
            self.assertTrue(adapter.process_standard_operation_once())

            install_mock(adapter.write_scheduler, "process_local_publish_burst", MagicMock(return_value=0))
            install_mock(adapter, "process_preferred_read_or_write", MagicMock(return_value=False))
            install_mock(adapter, "refresh_services_if_due_once", MagicMock(return_value=False))
            self.assertFalse(adapter.process_standard_operation_once())

            adapter.tick_health.record(
                duration_ms=adapter.slo_mainloop_gap_max_ms + 1.0,
                expected_interval_s=adapter.tick_seconds,
                now=time.monotonic(),
            )
            install_mock(adapter.resource_monitor, "snapshot", MagicMock(return_value={"state": "ok"}))
            install_mock(adapter.circuit, "state", MagicMock(return_value="ok"))
            adapter.update_adaptive_tick()
            self.assertEqual(adapter._last_resource_snapshot, {"state": "ok"})
            self.assertAlmostEqual(adapter.tick_seconds, 0.3)

            install_mock(adapter.resource_monitor, "snapshot", MagicMock(return_value={"state": "constrained"}))
            install_mock(adapter.circuit, "state", MagicMock(return_value="degraded"))
            adapter.update_adaptive_tick()
            self.assertEqual(adapter.tick_seconds, adapter.max_tick_seconds)

            boundary_adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-boundary")))
            install_mock(boundary_adapter.resource_monitor, "snapshot", MagicMock(return_value={"state": "ok"}))
            install_mock(
                boundary_adapter.tick_health,
                "snapshot",
                MagicMock(return_value={"max_tick_duration_ms_60s": boundary_adapter.slo_mainloop_gap_max_ms}),
            )
            install_mock(boundary_adapter.circuit, "state", MagicMock(return_value="ok"))
            boundary_adapter.update_adaptive_tick()
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
            missing_state_adapter.update_adaptive_tick()
            self.assertAlmostEqual(missing_state_adapter.tick_seconds, 0.3)

            degraded_adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-degraded")))
            install_mock(degraded_adapter.resource_monitor, "snapshot", MagicMock(return_value={"state": "ok"}))
            install_mock(
                degraded_adapter.tick_health,
                "snapshot",
                MagicMock(return_value={"max_tick_duration_ms_60s": 0.0}),
            )
            install_mock(degraded_adapter.circuit, "state", MagicMock(return_value="degraded"))
            degraded_adapter.update_adaptive_tick()
            self.assertAlmostEqual(degraded_adapter.tick_seconds, 0.5)

            tuning_adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-tuning")))
            tuning_adapter.min_tick_seconds = 0.4
            tuning_adapter.max_tick_seconds = 2.0
            self.assertAlmostEqual(
                tuning_adapter.adaptive_tick_seconds(circuit_state="degraded", resource_state="ok"),
                1.0,
            )
            self.assertAlmostEqual(
                tuning_adapter.adaptive_tick_seconds(circuit_state="ok", resource_state="busy"),
                0.6,
            )
