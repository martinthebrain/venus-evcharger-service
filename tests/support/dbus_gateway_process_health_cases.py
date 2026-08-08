# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway adapter runtime health and discovery scenarios."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    DbusAdapter,
    DbusOperationDeferred,
    GatewayAdapterContractCase,
    MagicMock,
    Path,
    builtins,
    gateway_paths,
    dbus_wire_call,
    health_backpressure_module,
    health_slo_module,
    install_dbus_call_responder,
    install_mock,
    observe_evcs_fields,
    patch,
    process_io_module,
    run_non_write_command,
    tempfile,
    time,
    unittest,
)
from venus_evcharger.ipc.energy import EnergyRefreshRequest


class GatewayProcessHealthCases(GatewayAdapterContractCase):
    """Exercise runtime health and discovery scenarios."""

    def test_health_regulation_edges_reduce_burst_and_ignore_bad_cached_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-health-edges")))

            adapter.write_scheduler.health_tracker.local_publish_burst_limit = 20
            thresholds = adapter.health_role.slo_thresholds()
            self.assertEqual(
                health_slo_module.regulated_publish_burst(
                    queue_age=0.0,
                    eventloop_gap_ms=thresholds.mainloop_gap_max_ms + 1.0,
                    base_burst=adapter.write_scheduler.local_publish_burst_limit,
                    thresholds=thresholds,
                ),
                10,
            )

            now = time.time()
            observe_evcs_fields(adapter, {"ac_power_w": (object(), 0.0)}, now=now)
            self.assertEqual(adapter.health_role.fresh_evcs_field_float("ac_power_w", now), 0.0)

            fresh_adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-fresh")))
            observe_evcs_fields(fresh_adapter, {"connected": (1, 0.0)}, now=now)
            fresh_adapter.cache.update_services(["svc"])
            for key in ("grid_power_w", "pv_power_w", "battery_soc"):
                fresh_adapter.cache.update_value(key, 1.0, source="test", now=now)
            install_mock(fresh_adapter.introspection_role, "enqueue_background_introspection_if_due", MagicMock())
            install_mock(fresh_adapter.io_role, "poll_one_due_read_once", MagicMock(return_value=False))
            install_mock(
                fresh_adapter.write_scheduler,
                "process_local_publish_burst",
                MagicMock(return_value=1),
            )
            install_mock(fresh_adapter.write_scheduler, "process_one", MagicMock(return_value=False))
            install_mock(fresh_adapter.io_role, "refresh_services_if_due_once", MagicMock(return_value=False))
            self.assertTrue(fresh_adapter.loop_role.process_one_dbus_operation_once())
            fresh_adapter.write_scheduler.process_local_publish_burst.assert_called_once()

            install_mock(adapter.loop_role, "process_one_dbus_operation_once", MagicMock())
            install_mock(adapter.io_role, "publish_cache", MagicMock())
            adapter._next_work_tick_monotonic = 0.0
            self.assertTrue(adapter.tick())
            adapter.loop_role.process_one_dbus_operation_once.assert_called_once()
            adapter.io_role.publish_cache.assert_called_once()

    def test_health_log_backpressure_and_publish_failure_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.health_log_path = ""
            adapter.health_role.append_health_log({"state": "ok"})
            adapter.health_log_path = str(Path(temp_dir) / "health.log")
            adapter.health_log_interval_seconds = 0.01
            with patch.object(builtins, "open", side_effect=OSError("full")):
                adapter.health_role.append_health_log({"state": "ok"})

            with self.assertRaises(RuntimeError):
                adapter.io_role.timed_local_publish(
                    lambda: (_ for _ in ()).throw(RuntimeError("publish failed")),
                )

            slow = health_backpressure_module.backpressure_snapshot(
                circuit_state=adapter.circuit.state(),
                slo={"violated": []},
                queue_health={"oldest_slo_command_age_s": adapter.slo_queue_max_age_seconds * 3.0},
                queue_max_age_seconds=adapter.slo_queue_max_age_seconds,
            )
            self.assertEqual(slow["state"], "slow")
            adapter.circuit.protective_until = time.time() + 10.0
            protective = health_backpressure_module.backpressure_snapshot(
                circuit_state=adapter.circuit.state(),
                slo={"violated": []},
                queue_health={"oldest_slo_command_age_s": 0.0},
                queue_max_age_seconds=adapter.slo_queue_max_age_seconds,
            )
            self.assertEqual(protective["state"], "protective")
            adapter.circuit.protective_until = 0.0
            congested = health_backpressure_module.backpressure_snapshot(
                circuit_state=adapter.circuit.state(),
                slo={"violated": ["gui_fresh"]},
                queue_health={"oldest_slo_command_age_s": 0.0},
                queue_max_age_seconds=adapter.slo_queue_max_age_seconds,
            )
            self.assertEqual(congested["state"], "congested")
            congested = health_backpressure_module.backpressure_snapshot(
                circuit_state=adapter.circuit.state(),
                slo={"violated": ["core_reads_fresh"]},
                queue_health={"oldest_slo_command_age_s": 0.0},
                queue_max_age_seconds=adapter.slo_queue_max_age_seconds,
            )
            self.assertEqual(congested["state"], "congested")
            self.assertEqual(
                health_backpressure_module.backpressure_snapshot(
                    circuit_state="ok",
                    slo={"violated": ["core_reads_fresh", "queue_age_ok", "gui_fresh", "core_reads_fresh"]},
                    queue_health={"oldest_slo_command_age_s": adapter.slo_queue_max_age_seconds + 0.1},
                    queue_max_age_seconds=adapter.slo_queue_max_age_seconds,
                ),
                {
                    "state": "congested",
                    "core_should_throttle": True,
                    "suppress_optional_commands": False,
                    "prefer_coalescing": True,
                    "reason": "queue-age,core_reads_fresh,queue_age_ok,gui_fresh",
                },
            )
            self.assertEqual(
                health_backpressure_module.backpressure_snapshot(
                    circuit_state="degraded",
                    slo={"violated": ("queue_age_ok",)},
                    queue_health={"oldest_slo_command_age_s": 0.0},
                    queue_max_age_seconds=adapter.slo_queue_max_age_seconds,
                ),
                {
                    "state": "slow",
                    "core_should_throttle": True,
                    "suppress_optional_commands": True,
                    "prefer_coalescing": True,
                    "reason": "dbus-degraded,queue_age_ok",
                },
            )
            self.assertEqual(
                health_backpressure_module.backpressure_snapshot(
                    circuit_state="protective",
                    slo={"violated": set()},
                    queue_health={"oldest_slo_command_age_s": 0.0},
                    queue_max_age_seconds=adapter.slo_queue_max_age_seconds,
                ),
                {
                    "state": "protective",
                    "core_should_throttle": True,
                    "suppress_optional_commands": True,
                    "prefer_coalescing": True,
                    "reason": "dbus-protective",
                },
            )
            self.assertEqual(
                health_backpressure_module.backpressure_snapshot(
                    circuit_state="ok",
                    slo={"violated": "core_reads_fresh"},
                    queue_health={"oldest_slo_command_age_s": adapter.slo_queue_max_age_seconds},
                    queue_max_age_seconds=adapter.slo_queue_max_age_seconds,
                ),
                {
                    "state": "ok",
                    "core_should_throttle": False,
                    "suppress_optional_commands": False,
                    "prefer_coalescing": False,
                    "reason": "ok",
                },
            )
            self.assertEqual(
                health_backpressure_module.backpressure_reasons(
                    "ok",
                    adapter.slo_queue_max_age_seconds + 0.01,
                    {"violated": ["mainloop_gap_ok", "queue_age_ok", "core_reads_fresh"]},
                    queue_max_age_seconds=adapter.slo_queue_max_age_seconds,
                ),
                ["queue-age", "queue_age_ok", "core_reads_fresh"],
            )
            self.assertEqual(health_backpressure_module.slo_violations({"violated": ["a", "b"]}), ["a", "b"])
            self.assertEqual(health_backpressure_module.slo_violations({"violated": ("a", "b")}), ["a", "b"])
            self.assertEqual(health_backpressure_module.slo_violations({"violated": {"a"}}), ["a"])
            self.assertEqual(health_backpressure_module.slo_violations({"violated": set()}), [])
            self.assertEqual(health_backpressure_module.slo_violations({"violated": "a"}), [])
            self.assertEqual(health_backpressure_module.slo_violations({"violated": object()}), [])
            self.assertEqual(health_backpressure_module.slo_violations({}), [])
            self.assertEqual(health_backpressure_module.slo_violations({"violated": None}), [])
            self.assertEqual(
                health_backpressure_module.backpressure_state(
                    "ok",
                    adapter.slo_queue_max_age_seconds * 2.0,
                    ["queue-age"],
                    queue_max_age_seconds=adapter.slo_queue_max_age_seconds,
                ),
                "congested",
            )
            self.assertEqual(
                health_backpressure_module.backpressure_state(
                    "ok",
                    adapter.slo_queue_max_age_seconds * 2.0 + 0.01,
                    [],
                    queue_max_age_seconds=adapter.slo_queue_max_age_seconds,
                ),
                "slow",
            )

            now = time.time()
            observe_evcs_fields(adapter, {"mode": (1, 2.0)}, now=now)
            self.assertGreater(adapter.health_role.max_publication_field_age({"mode"}, now), 0.0)
            self.assertEqual(adapter.health_role.max_publication_field_age({"missing"}, now), 0.0)
            adapter.tick_health.record(duration_ms=1.0, expected_interval_s=0.1, now=time.monotonic() - 1.0)
            adapter.tick_health.record(duration_ms=1.0, expected_interval_s=0.1, now=time.monotonic())
            adapter.health_role.apply_slo_regulation()
            self.assertLessEqual(
                adapter.write_scheduler.health_tracker.dynamic_local_publish_burst_limit,
                adapter.write_scheduler.local_publish_burst_limit,
            )
            self.assertEqual(adapter.health_role.max_publication_field_age(set(), now), 0.0)

    def test_poll_and_discovery_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.read_scheduler.next_read_at = {key: time.time() + 1000 for key in adapter.read_scheduler.specs}
            self.assertFalse(adapter.io_role.poll_one_due_read_once())

            adapter.read_scheduler.next_read_at = {
                key: (0.0 if key == "grid_power_w" else time.time() + 1000) for key in adapter.read_scheduler.specs
            }
            install_mock(
                adapter.read_executor,
                "poll_read_spec",
                MagicMock(
                    side_effect=lambda _key, _spec, *, completion: (
                        completion("applied") or "applied"
                    )
                ),
            )
            self.assertTrue(adapter.io_role.poll_one_due_read_once())
            adapter.read_scheduler.next_read_at["grid_power_w"] = 0.0
            install_mock(
                adapter.read_executor,
                "poll_read_spec",
                MagicMock(
                    side_effect=lambda _key, _spec, *, completion: (
                        completion("dropped") or "dropped"
                    )
                ),
            )
            self.assertTrue(adapter.io_role.poll_one_due_read_once())
            adapter.read_scheduler.next_read_at["grid_power_w"] = 0.0
            install_mock(adapter.read_executor, "poll_read_spec", MagicMock(return_value="deferred"))
            self.assertFalse(adapter.io_role.poll_one_due_read_once())

            adapter.discovery.next_scan_monotonic = time.monotonic() + 1000
            self.assertFalse(adapter.io_role.refresh_services_if_due_once())
            topology_refresh = EnergyRefreshRequest("topology", "topology", 0.0)
            self.assertEqual(
                run_non_write_command(adapter, topology_refresh.to_command(source="test")),
                "applied",
            )
            service_responder = MagicMock(return_value=["svc"])
            send_async = install_dbus_call_responder(
                adapter.connection,
                service_responder,
            )
            expected_call = (
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "ListNames",
                "",
                (),
            )
            self.assertTrue(adapter.io_role.refresh_services_if_due_once())
            self.assertEqual(dbus_wire_call(send_async.call_args.args[0]), expected_call)
            self.assertEqual(send_async.call_args.args[0].timeout_seconds, 1.0)
            self.assertIn("svc", adapter.cache.services)
            self.assertEqual(adapter.energy_discovery.topology_snapshot(captured_at=time.time()).generation, 1)
            adapter.discovery.next_scan_monotonic = 0.0
            with patch.object(
                adapter.operation_broker,
                "submit",
                side_effect=DbusOperationDeferred("read"),
            ):
                self.assertFalse(adapter.io_role.refresh_services_if_due_once())
            self.assertEqual(run_non_write_command(adapter, {"kind": "refresh_energy_inputs"}), "dropped")
            adapter.rate_limiter.next_at["read"] = 0.0
            service_error = RuntimeError("dbus down")
            service_responder.side_effect = service_error
            adapter.discovery.next_scan_monotonic = 0.0
            self.assertTrue(adapter.io_role.refresh_services_if_due_once())
            self.assertEqual(adapter.discovery.last_error, "dbus down")
            self.assertEqual(
                [dbus_wire_call(mock_call.args[0]) for mock_call in send_async.call_args_list],
                [expected_call, expected_call],
            )
            self.assertEqual(
                [mock_call.args[0].timeout_seconds for mock_call in send_async.call_args_list],
                [1.0, 1.0],
            )
            adapter.io_role.maybe_refresh_services()

    def test_poll_and_discovery_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            install_mock(adapter.circuit, "state", MagicMock(return_value="degraded"))
            install_mock(adapter.circuit, "allows_priority", MagicMock(return_value=True))
            next_due = install_mock(
                adapter.read_scheduler,
                "next_due",
                MagicMock(return_value=("grid", {"service": "svc"}, 2.5)),
            )
            record_success = install_mock(adapter.read_scheduler, "record_success", MagicMock())
            record_error = install_mock(adapter.read_scheduler, "record_error", MagicMock())
            poll_read_spec = install_mock(
                adapter.read_executor,
                "poll_read_spec",
                MagicMock(
                    side_effect=lambda _key, _spec, *, completion: (
                        completion("applied") or "applied"
                    )
                ),
            )
            adapter.read_executor._interval_factors["grid"] = 3.0

            with patch.object(process_io_module.time, "monotonic", return_value=123.0):
                self.assertTrue(adapter.io_role.poll_one_due_read_once())

            next_due.assert_called_once_with(
                monotonic_at=123.0,
                circuit_state="degraded",
                priority_allowed=adapter.circuit.allows_priority,
            )
            poll_read_spec.assert_called_once_with(
                "grid",
                {"service": "svc"},
                completion=unittest.mock.ANY,
            )
            record_success.assert_called_once_with(
                "grid",
                monotonic_at=123.0,
                interval=2.5,
                interval_factor=3.0,
            )
            self.assertNotIn("grid", adapter.read_executor._interval_factors)
            record_error.assert_not_called()

            next_due.reset_mock()
            poll_read_spec.reset_mock(return_value=True)
            record_success.reset_mock()
            record_error.reset_mock()
            poll_read_spec.side_effect = lambda _key, _spec, *, completion: (
                completion("dropped") or "dropped"
            )
            with patch.object(process_io_module.time, "monotonic", return_value=124.0):
                self.assertTrue(adapter.io_role.poll_one_due_read_once())
            record_success.assert_not_called()
            record_error.assert_called_once_with(
                "grid",
                monotonic_at=124.0,
                interval=2.5,
            )

            poll_read_spec.side_effect = None
            poll_read_spec.return_value = "deferred"
            adapter.read_executor.last_operation_performed = True
            record_error.reset_mock()
            with patch.object(process_io_module.time, "monotonic", return_value=125.0):
                self.assertTrue(adapter.io_role.poll_one_due_read_once())
            record_error.assert_not_called()

            next_due.return_value = None
            poll_read_spec.reset_mock()
            with patch.object(process_io_module.time, "monotonic", return_value=126.0):
                self.assertFalse(adapter.io_role.poll_one_due_read_once())
            poll_read_spec.assert_not_called()

            discovery_due = install_mock(adapter.discovery, "due", MagicMock(return_value=True))
            discovery_success = install_mock(adapter.discovery, "record_success", MagicMock())
            discovery_error = install_mock(adapter.discovery, "record_error", MagicMock())
            update_services = install_mock(adapter.cache, "update_services", MagicMock())
            update_energy_services = install_mock(adapter.energy_discovery, "update_services", MagicMock())
            needs_early_rescan = install_mock(
                adapter.energy_discovery,
                "needs_early_pv_rescan",
                MagicMock(return_value=True),
            )
            service_responder = MagicMock(return_value=["svc.a"])
            send_async = install_dbus_call_responder(
                adapter.connection,
                service_responder,
            )
            expected_call = (
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "ListNames",
                "",
                (),
            )
            with (
                patch.object(process_io_module.time, "time", return_value=200.0),
                patch.object(
                    process_io_module.time,
                    "monotonic",
                    return_value=20.0,
                ),
            ):
                self.assertTrue(adapter.io_role.refresh_services_if_due_once())
            self.assertEqual(dbus_wire_call(send_async.call_args.args[0]), expected_call)
            self.assertEqual(send_async.call_args.args[0].timeout_seconds, 1.0)
            discovery_due.assert_called_once_with(
                monotonic_at=20.0,
                priority_allowed=adapter.circuit.allows_priority,
            )
            update_services.assert_called_once_with(["svc.a"], now=200.0)
            update_energy_services.assert_called_once_with(
                ["svc.a"],
                captured_at=200.0,
            )
            needs_early_rescan.assert_called_once_with()
            discovery_success.assert_called_once_with(
                monotonic_at=20.0,
                captured_at=200.0,
                needs_early_rescan=True,
            )
            discovery_error.assert_not_called()

            update_services.reset_mock()
            update_energy_services.reset_mock()
            discovery_success.reset_mock()
            error = RuntimeError("dbus down")
            adapter.rate_limiter.next_at["read"] = 0.0
            service_responder.side_effect = error
            with (
                patch.object(process_io_module.time, "time", return_value=201.0),
                patch.object(
                    process_io_module.time,
                    "monotonic",
                    return_value=21.0,
                ),
            ):
                self.assertTrue(adapter.io_role.refresh_services_if_due_once())
            update_services.assert_not_called()
            update_energy_services.assert_not_called()
            discovery_success.assert_not_called()
            discovery_error.assert_called_once_with(
                error,
                monotonic_at=21.0,
                captured_at=201.0,
            )
            self.assertEqual(
                [dbus_wire_call(mock_call.args[0]) for mock_call in send_async.call_args_list],
                [expected_call, expected_call],
            )
            self.assertEqual(
                [mock_call.args[0].timeout_seconds for mock_call in send_async.call_args_list],
                [1.0, 1.0],
            )

            discovery_error.reset_mock()
            with (
                patch.object(
                    adapter.operation_broker,
                    "submit",
                    side_effect=DbusOperationDeferred("read"),
                ),
                patch.object(process_io_module.time, "time", return_value=202.0),
                patch.object(
                    process_io_module.time,
                    "monotonic",
                    return_value=22.0,
                ),
            ):
                self.assertFalse(adapter.io_role.refresh_services_if_due_once())
            discovery_error.assert_not_called()
