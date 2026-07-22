# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway adapter adaptive SLO regulation contracts."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    DbusAdapter,
    GatewayAdapterContractCase,
    MagicMock,
    Path,
    gateway_paths,
    health_slo_module,
    install_mock,
    evcs_publication,
    patch,
    process_health_module,
    tempfile,
    time,
    unittest,
)


class GatewayRegulationCases(GatewayAdapterContractCase):
    """Exercise adaptive SLO regulation contracts."""

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
            stale_publication = evcs_publication({"mode": 1}, priority="live")
            stale_publication["created_at"] = now - 10.0
            adapter.commands.enqueue(stale_publication)
            adapter.cache.update_value("grid_power_w", 1.0, source="grid", now=now - 10.0)
            adapter.read_scheduler.next_read_at = {key: now + 1000.0 for key in adapter.read_scheduler.specs}

            adapter.apply_slo_regulation()

            self.assertEqual(adapter.write_scheduler.dynamic_local_publish_burst_limit, 1)
            self.assertEqual(adapter.read_scheduler.next_read_at["grid_power_w"], 0.0)
            self.assertEqual(adapter.read_scheduler.next_read_at["pv_power_w"], 0.0)
            self.assertEqual(adapter.read_scheduler.next_read_at["battery_soc"], 0.0)

            selective = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-selective")))
            selective.cache.update_value("grid_power_w", 1.0, source="grid", now=now)
            selective.cache.update_value("pv_power_w", 0.0, source="pv", now=now - 10.0)
            selective.cache.update_value("battery_soc", 10.0, source="battery", now=now - 10.0)
            selective.read_scheduler.next_read_at = {key: now + 1000.0 for key in selective.read_scheduler.specs}

            selective.apply_slo_regulation()

            self.assertGreater(selective.read_scheduler.next_read_at["grid_power_w"], now)
            self.assertEqual(selective.read_scheduler.next_read_at["pv_power_w"], 0.0)
            self.assertEqual(selective.read_scheduler.next_read_at["battery_soc"], 0.0)

            adapter.circuit.degraded_until = time.time() + 60.0
            adapter.discovery.next_scan_at = 0.0
            adapter.apply_slo_regulation()

            self.assertGreater(adapter.discovery.next_scan_at, time.time())

    def test_slo_regulation_forwards_exact_backpressure_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                "DbusGatewayLocalPublishBurstLimit=20\n"
                "DbusGatewaySloCoreReadMaxAgeSeconds=5\n"
                "DbusGatewaySloQueueMaxAgeSeconds=10\n"
                "DbusGatewaySloMainloopGapMaxMs=500\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            pending = [
                (
                    "stale-local.json",
                    {
                        **evcs_publication({"mode": 1}, priority="live"),
                        "queue_class": "local-publish",
                        "created_at": 980.0,
                    },
                )
            ]
            freshness = {
                "grid_power_w_age_s": 4.0,
                "grid_power_w_status": "fresh",
                "pv_power_w_age_s": 6.0,
                "pv_power_w_status": "fresh",
                "battery_soc_age_s": 7.0,
                "battery_soc_status": "stale",
            }
            install_mock(adapter.commands, "load_pending", MagicMock(return_value=pending))
            install_mock(adapter, "cache_freshness_snapshot", MagicMock(return_value=freshness))
            install_mock(adapter.tick_health, "snapshot", MagicMock(return_value={"max_tick_gap_ms_60s": 3000.0}))
            install_mock(adapter.read_scheduler, "force_due", MagicMock())
            install_mock(adapter.write_scheduler, "set_dynamic_local_publish_burst", MagicMock())
            install_mock(adapter, "suspend_advisory_work", MagicMock())
            install_mock(adapter.circuit, "state", MagicMock(return_value="ok"))
            adapter._last_resource_snapshot = {"state": "constrained"}

            with (
                patch.object(process_health_module.time, "time", return_value=1000.0),
                patch.object(
                    process_health_module.time,
                    "monotonic",
                    return_value=2000.0,
                ),
                patch.object(
                    process_health_module,
                    "backpressure_snapshot",
                    MagicMock(return_value={"state": "slow"}),
                ) as backpressure_snapshot,
                patch.object(
                    process_health_module,
                    "runtime_pressure_state",
                    MagicMock(return_value="slow"),
                ) as runtime_pressure_state,
            ):
                adapter.apply_slo_regulation()

            adapter.write_scheduler.set_dynamic_local_publish_burst.assert_called_once_with(5, pressure_state="slow")
            adapter.read_scheduler.force_due.assert_called_once_with({"pv_power_w", "battery_soc"})
            adapter.suspend_advisory_work.assert_called_once_with(1000.0)
            adapter.cache_freshness_snapshot.assert_called_once_with(1000.0)
            backpressure_snapshot.assert_called_once_with(
                circuit_state="ok",
                queue_health={"oldest_command_age_s": 20.0, "oldest_slo_command_age_s": 20.0},
                slo=unittest.mock.ANY,
                queue_max_age_seconds=10.0,
            )
            runtime_pressure_state.assert_called_once_with("constrained", "slow")
            self.assertEqual(
                adapter.tick_health.snapshot.call_args_list,
                [unittest.mock.call(), unittest.mock.call(now=2000.0)],
            )

    def test_slo_regulation_defaults_missing_pressure_states_to_ok(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                "DbusGatewayLocalPublishBurstLimit=20\n"
                "DbusGatewaySloCoreReadMaxAgeSeconds=5\n"
                "DbusGatewaySloQueueMaxAgeSeconds=10\n"
                "DbusGatewaySloMainloopGapMaxMs=500\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            install_mock(adapter.commands, "load_pending", MagicMock(return_value=[]))
            install_mock(adapter, "cache_freshness_snapshot", MagicMock(return_value={}))
            install_mock(adapter.tick_health, "snapshot", MagicMock(return_value={"max_tick_gap_ms_60s": 0.0}))
            install_mock(adapter.read_scheduler, "force_due", MagicMock())
            install_mock(adapter.write_scheduler, "set_dynamic_local_publish_burst", MagicMock())
            install_mock(adapter, "suspend_advisory_work", MagicMock())
            install_mock(adapter.circuit, "state", MagicMock(return_value="ok"))
            adapter._last_resource_snapshot = {}

            with (
                patch.object(process_health_module.time, "time", return_value=1000.0),
                patch.object(
                    process_health_module.time,
                    "monotonic",
                    return_value=2000.0,
                ),
                patch.object(
                    process_health_module,
                    "backpressure_snapshot",
                    MagicMock(return_value={}),
                ),
                patch.object(
                    process_health_module,
                    "runtime_pressure_state",
                    MagicMock(return_value="ok"),
                ) as runtime_pressure_state,
            ):
                adapter.apply_slo_regulation()

            runtime_pressure_state.assert_called_once_with("ok", "ok")
            adapter.write_scheduler.set_dynamic_local_publish_burst.assert_called_once_with(20, pressure_state="ok")
            adapter.suspend_advisory_work.assert_not_called()

    def test_slo_regulation_quiets_discovery_under_protective_pressure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                "DbusGatewayLocalPublishBurstLimit=20\n"
                "DbusGatewaySloCoreReadMaxAgeSeconds=5\n"
                "DbusGatewaySloQueueMaxAgeSeconds=10\n"
                "DbusGatewaySloMainloopGapMaxMs=500\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            install_mock(adapter.commands, "load_pending", MagicMock(return_value=[]))
            install_mock(adapter, "cache_freshness_snapshot", MagicMock(return_value={}))
            install_mock(adapter.tick_health, "snapshot", MagicMock(return_value={"max_tick_gap_ms_60s": 0.0}))
            install_mock(adapter.read_scheduler, "force_due", MagicMock())
            install_mock(adapter.write_scheduler, "set_dynamic_local_publish_burst", MagicMock())
            install_mock(adapter, "suspend_advisory_work", MagicMock())
            install_mock(adapter.circuit, "state", MagicMock(return_value="ok"))
            adapter._last_resource_snapshot = {"state": "ok"}

            with (
                patch.object(process_health_module.time, "time", return_value=1000.0),
                patch.object(
                    process_health_module.time,
                    "monotonic",
                    return_value=2000.0,
                ),
                patch.object(
                    process_health_module,
                    "backpressure_snapshot",
                    MagicMock(return_value={"state": "ok"}),
                ),
                patch.object(
                    process_health_module,
                    "runtime_pressure_state",
                    MagicMock(return_value="protective"),
                ),
            ):
                adapter.apply_slo_regulation()

            adapter.write_scheduler.set_dynamic_local_publish_burst.assert_called_once_with(
                1,
                pressure_state="protective",
            )
            adapter.suspend_advisory_work.assert_called_once_with(1000.0)

    def test_slo_snapshot_and_regulation_boundaries_are_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\n"
                "DbusGatewayLocalPublishBurstLimit=20\n"
                "DbusGatewaySloCoreReadMaxAgeSeconds=5\n"
                "DbusGatewaySloQueueMaxAgeSeconds=10\n"
                "DbusGatewaySloMainloopGapMaxMs=100\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))

            install_mock(
                adapter,
                "slo_observed",
                MagicMock(
                    return_value={
                        "gui_max_age_s": 0.0,
                        "gui_measurement_max_age_s": 0.0,
                        "gui_control_max_age_s": 0.0,
                        "gui_session_max_age_s": 0.0,
                        "core_read_max_age_s": 0.0,
                        "queue_oldest_age_s": 0.0,
                        "mainloop_max_gap_ms_60s": 0.0,
                    }
                ),
            )
            adapter.slo_snapshot(queue_health={}, cache_freshness={}, now=111.0, current_monotonic=222.0)
            adapter.slo_observed.assert_called_once_with({}, {}, 111.0, 222.0)

            install_mock(
                adapter,
                "cache_freshness_snapshot",
                MagicMock(
                    return_value={
                        "grid_power_w_age_s": 5.0,
                        "grid_power_w_status": "fresh",
                        "pv_power_w_age_s": 5.0,
                        "pv_power_w_status": "fresh",
                        "battery_soc_age_s": 5.0,
                        "battery_soc_status": "fresh",
                    }
                ),
            )
            install_mock(adapter.read_scheduler, "force_due", MagicMock())
            install_mock(adapter.tick_health, "snapshot", MagicMock(return_value={"max_tick_gap_ms_60s": 0.0}))
            adapter.apply_slo_regulation()
            adapter.read_scheduler.force_due.assert_not_called()
            install_mock(adapter, "suspend_advisory_work", MagicMock())
            adapter.apply_slo_regulation()
            adapter.suspend_advisory_work.assert_not_called()

            adapter.write_scheduler.local_publish_burst_limit = 20
            install_mock(adapter, "cache_freshness_snapshot", MagicMock(return_value={}))
            install_mock(
                adapter.tick_health,
                "snapshot",
                MagicMock(
                    return_value={
                        "max_tick_gap_ms_60s": health_slo_module.effective_mainloop_gap_max_ms(adapter.slo_thresholds())
                        + 1.0
                    }
                ),
            )
            adapter.apply_slo_regulation()
            self.assertEqual(adapter.write_scheduler.dynamic_local_publish_burst_limit, 10)

            quiet_adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-quiet")))
            quiet_adapter.discovery.next_scan_at = 0.0
            quiet_adapter._last_introspection_full_scan_at = 50.0
            advisory = [
                ("introspection.json", {"queue_class": "introspection"}),
                ("discovery.json", {"queue_class": "discovery"}),
                ("diagnostic.json", {"queue_class": "diagnostic"}),
                ("remote.json", {"queue_class": "remote-write"}),
            ]
            install_mock(quiet_adapter.commands, "load_pending", MagicMock(return_value=advisory))
            install_mock(quiet_adapter.commands, "remove", MagicMock())
            install_mock(quiet_adapter.write_scheduler, "record_lifecycle", MagicMock())
            quiet_adapter.suspend_advisory_work(100.0)
            self.assertEqual(quiet_adapter.discovery.next_scan_at, 160.0)
            self.assertEqual(quiet_adapter._last_introspection_full_scan_at, 100.0)
            self.assertEqual(
                quiet_adapter.commands.remove.call_args_list,
                [unittest.mock.call("introspection.json"), unittest.mock.call("discovery.json"), unittest.mock.call("diagnostic.json")],
            )
            self.assertEqual(
                quiet_adapter.write_scheduler.record_lifecycle.call_args_list,
                [
                    unittest.mock.call({"queue_class": "introspection"}, "dropped"),
                    unittest.mock.call({"queue_class": "discovery"}, "dropped"),
                    unittest.mock.call({"queue_class": "diagnostic"}, "dropped"),
                ],
            )
