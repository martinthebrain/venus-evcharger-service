# SPDX-License-Identifier: GPL-3.0-or-later
"""Gateway adapter health, freshness, and SLO behavior."""

from __future__ import annotations

from tests.support.dbus_gateway_adapter_harness import (
    DbusAdapter,
    GatewayAdapterContractCase,
    MagicMock,
    Path,
    gateway_paths,
    health_slo_module,
    install_mock,
    observe_evcs_fields,
    patch,
    process_health_module,
    tempfile,
    time,
)
from venus_evcharger.ipc.core_commands import core_control_command_payload
from venus_evcharger.ipc.energy import EnergyRefreshRequest


class GatewayHealthSloCases(GatewayAdapterContractCase):
    """Exercise health, freshness, and SLO behavior."""

    def test_health_snapshot_time_and_backpressure_contracts_are_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            install_mock(
                adapter.write_scheduler,
                "health",
                MagicMock(return_value={"processed_commands_60s": 7, "last_processed_at": 321.0}),
            )
            install_mock(adapter.tick_health, "snapshot", MagicMock(return_value={"max_tick_gap_ms_60s": 12.0}))
            install_mock(adapter.health_role, "slo_snapshot", MagicMock(return_value={"violated": [], "checks": {}}))
            adapter._last_tick_monotonic = 999.75

            with (
                patch.object(process_health_module.time, "time", return_value=500.0),
                patch.object(
                    process_health_module.time,
                    "monotonic",
                    return_value=1000.0,
                ),
            ):
                health = adapter.health_snapshot()

            adapter.write_scheduler.health.assert_called_once_with(now=500.0)
            adapter.health_role.slo_snapshot.assert_called_once()
            self.assertEqual(adapter.health_role.slo_snapshot.call_args.kwargs["current_monotonic"], 1000.0)
            adapter.tick_health.snapshot.assert_called_once_with(now=1000.0)
            self.assertEqual(health["queues"]["processed_commands_60s"], 7)
            self.assertEqual(health["queues"]["last_processed_at"], 321.0)
            self.assertEqual(health["write_scheduler"]["processed_commands_60s"], 7)
            self.assertEqual(health["mainloop_heartbeat_age_s"], 0.25)
            self.assertEqual(health["eventloop"]["mainloop_heartbeat_age_s"], 0.25)
            self.assertEqual(health["eventloop"]["max_tick_gap_ms_60s"], 12.0)
            self.assertEqual(health["backpressure"]["state"], "ok")

            degraded = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run-degraded")))
            install_mock(degraded.health_role, "slo_snapshot", MagicMock(return_value={"violated": [], "checks": {}}))
            degraded.circuit.degraded_until = time.time() + 60.0
            self.assertEqual(degraded.health_snapshot()["backpressure"]["state"], "slow")

            for last_tick, expected in ((0.0, 0.0), (0.5, 999.5)):
                heartbeat_adapter = DbusAdapter(
                    str(config_path),
                    paths=gateway_paths(str(Path(temp_dir) / f"run-heartbeat-{last_tick}")),
                )
                install_mock(heartbeat_adapter.health_role, "slo_snapshot", MagicMock(return_value={"violated": [], "checks": {}}))
                heartbeat_adapter._last_tick_monotonic = last_tick
                with patch.object(process_health_module.time, "monotonic", return_value=1000.0):
                    self.assertEqual(heartbeat_adapter.health_snapshot()["mainloop_heartbeat_age_s"], expected)

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
            observe_evcs_fields(adapter, {"ac_power_w": (10.0, 5.0)}, now=now)
            adapter.cache.update_value("grid_power_w", 10.0, source="grid", now=now - 5.0)
            monotonic_now = time.monotonic()
            adapter.tick_health.record(duration_ms=1.0, expected_interval_s=0.1, now=monotonic_now - 3.0)
            adapter.tick_health.record(duration_ms=1.0, expected_interval_s=0.1, now=monotonic_now)
            refresh = EnergyRefreshRequest("health-stale", "grid", 0.0, urgency="priority").to_command(
                source="health-test"
            )
            refresh["created_at"] = now - 5.0
            adapter.commands.enqueue(refresh)

            health = adapter.health_snapshot()

            self.assertEqual(health["slo"]["state"], "violated")
            self.assertIn("gui_fresh", health["slo"]["violated"])
            self.assertIn("core_reads_fresh", health["slo"]["violated"])
            self.assertIn("queue_age_ok", health["slo"]["violated"])
            self.assertIn("mainloop_gap_ok", health["slo"]["violated"])
            self.assertNotEqual(health["backpressure"]["state"], "ok")
            self.assertTrue(health["backpressure"]["core_should_throttle"])

    def test_missing_values_drive_slo_backpressure_and_priority_refresh(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            install_mock(
                adapter.energy_discovery,
                "dormant_evidence",
                MagicMock(return_value=()),
            )
            control = adapter.health_role.control_snapshot()
            slo = control.health["slo"]
            observed = slo["observed"]

            self.assertGreater(observed["gui_missing_field_count"], 0.0)
            self.assertEqual(observed["core_read_missing_count"], 3.0)
            self.assertEqual(observed["core_read_nonfresh_count"], 0.0)
            self.assertFalse(slo["checks"]["gui_fresh"])
            self.assertFalse(slo["checks"]["core_reads_fresh"])
            self.assertEqual(control.pressure_state, "congested")
            self.assertIn("gui_fresh", control.health["backpressure"]["reason"])
            self.assertIn("core_reads_fresh", control.health["backpressure"]["reason"])
            self.assertEqual(
                set(control.stale_core_reads),
                {"grid_power_w", "pv_power_w", "battery_soc"},
            )

            install_mock(adapter.read_scheduler, "expedite_healthy", MagicMock())
            install_mock(adapter.health_role, "suspend_advisory_work", MagicMock())
            adapter.health_role.apply_slo_regulation(control)
            adapter.read_scheduler.expedite_healthy.assert_called_once_with(
                control.stale_core_reads
            )
            adapter.health_role.suspend_advisory_work.assert_called_once_with(
                monotonic_at=control.monotonic_at,
                captured_at=control.captured_at,
            )

    def test_nonfresh_core_status_violates_slo_even_with_zero_age(self) -> None:
        with self.adapter_scenario() as scenario:
            adapter = scenario.adapter
            now = time.time()
            observe_evcs_fields(adapter, {}, now=now)
            for key in ("grid_power_w", "pv_power_w", "battery_soc"):
                adapter.cache.update_value(key, 1.0, source="test", now=now)
            adapter.cache.mark_error(
                "pv_power_w",
                source="test",
                error="temporary",
                now=now,
            )
            install_mock(
                adapter.energy_discovery,
                "dormant_evidence",
                MagicMock(return_value=()),
            )

            with patch.object(process_health_module.time, "time", return_value=now):
                control = adapter.health_role.control_snapshot()
            observed = control.health["slo"]["observed"]
            self.assertEqual(observed["core_read_max_age_s"], 0.0)
            self.assertEqual(observed["core_read_missing_count"], 0.0)
            self.assertEqual(observed["core_read_nonfresh_count"], 1.0)
            self.assertFalse(control.health["slo"]["checks"]["core_reads_fresh"])
            self.assertEqual(control.stale_core_reads, ("pv_power_w",))
            self.assertEqual(control.pressure_state, "congested")

    def test_gui_freshness_ignores_idle_session_counters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\nDbusGatewaySloGuiMaxAgeSeconds=1\nDbusGatewaySloCoreReadMaxAgeSeconds=1\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            now = time.time()
            observe_evcs_fields(
                adapter,
                {
                    "ac_power_w": (1.7, 0.5),
                    "ac_current_a": (0.01, 0.5),
                    "session_energy_kwh": (0.0, 600.0),
                    "session_time_s": (0, 600.0),
                    "energy_forward_kwh": (0.0, 600.0),
                },
                now=now,
            )

            self.assertFalse(adapter.health_role.charging_session_active_for_gui(now))
            freshness_fields = adapter.health_role.gui_freshness_fields(now)
            self.assertIn("ac_power_w", freshness_fields)
            self.assertIn("ac_current_a", freshness_fields)
            self.assertIn("connected", freshness_fields)
            self.assertIn("mode", freshness_fields)
            self.assertNotIn("session_energy_kwh", freshness_fields)
            observed = adapter.health_role.slo_observed({}, {}, now, time.monotonic())
            self.assertIn("gui_measurement_max_age_s", observed)
            self.assertIn("gui_measurement_missing_field_count", observed)
            self.assertEqual(observed["gui_session_max_age_s"], 0.0)
            self.assertEqual(observed["gui_session_missing_field_count"], 0.0)
            self.assertEqual(observed["gui_control_missing_field_count"], 0.0)
            checks = health_slo_module.slo_checks_from_observed(observed, adapter.health_role.slo_thresholds())
            self.assertTrue(checks["gui_fresh"])
            self.assertTrue(checks["gui_controls_fresh"])

    def test_gui_freshness_tracks_control_fields_against_effective_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\nDbusGatewaySloGuiMaxAgeSeconds=2\nDbusGatewaySloCoreReadMaxAgeSeconds=5\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            now = time.time()
            observe_evcs_fields(
                adapter,
                {
                    "ac_power_w": (1.0, 0.5),
                    "ac_current_a": (0.01, 0.5),
                    "mode": (1, 9.0),
                },
                now=now,
            )

            observed = adapter.health_role.slo_observed({}, {}, now, time.monotonic())
            checks = health_slo_module.slo_checks_from_observed(observed, adapter.health_role.slo_thresholds())
            targets = health_slo_module.slo_targets(adapter.health_role.slo_thresholds())

            self.assertEqual(targets["configured_gui_max_age_s"], 2.0)
            self.assertEqual(targets["gui_control_max_age_s"], 10.0)
            self.assertEqual(observed["gui_control_max_age_s"], 9.0)
            self.assertEqual(observed["gui_control_missing_field_count"], 0.0)
            self.assertEqual(observed["gui_missing_field_count"], 0.0)
            self.assertTrue(checks["gui_controls_fresh"])
            self.assertTrue(checks["gui_fresh"])

            observe_evcs_fields(adapter, {"mode": (1, 10.1)}, now=now)
            stale_observed = adapter.health_role.slo_observed({}, {}, now, time.monotonic())
            stale_checks = health_slo_module.slo_checks_from_observed(stale_observed, adapter.health_role.slo_thresholds())

            self.assertFalse(stale_checks["gui_controls_fresh"])
            self.assertFalse(stale_checks["gui_fresh"])

    def test_slo_observed_uses_tick_snapshot_time_and_named_measurement_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            install_mock(adapter.tick_health, "snapshot", MagicMock(return_value={"max_tick_gap_ms_60s": 42.0}))

            observed = adapter.health_role.slo_observed(
                {"oldest_command_age_s": 99.0, "oldest_slo_command_age_s": 3.0},
                {"grid_power_w_age_s": 4.0},
                now=100.0,
                current_monotonic=777.0,
            )

            adapter.tick_health.snapshot.assert_called_once_with(now=777.0)
            self.assertEqual(observed["gui_measurement_max_age_s"], 0.0)
            self.assertGreater(observed["gui_measurement_missing_field_count"], 0.0)
            self.assertEqual(observed["queue_oldest_age_s"], 3.0)
            self.assertEqual(observed["core_read_max_age_s"], 4.0)
            self.assertEqual(observed["core_read_missing_count"], 3.0)
            self.assertEqual(observed["core_read_nonfresh_count"], 0.0)
            self.assertEqual(observed["mainloop_max_gap_ms_60s"], 42.0)

    def test_gui_freshness_includes_session_counters_while_charging(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\nDbusGatewaySloGuiMaxAgeSeconds=1\nDbusGatewaySloCoreReadMaxAgeSeconds=1\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            now = time.time()
            observe_evcs_fields(
                adapter,
                {
                    "ac_power_w": (900.0, 0.5),
                    "ac_current_a": (4.0, 0.5),
                    "session_energy_kwh": (0.1, 600.0),
                },
                now=now,
            )

            self.assertTrue(adapter.health_role.charging_session_active_for_gui(now))
            self.assertIn("session_energy_kwh", adapter.health_role.gui_freshness_fields(now))
            observed = adapter.health_role.slo_observed({}, {}, now, time.monotonic())
            self.assertFalse(
                health_slo_module.slo_checks_from_observed(observed, adapter.health_role.slo_thresholds())["gui_fresh"]
            )

    def test_gui_activity_detection_uses_fresh_power_or_current_thresholds(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\nDbusGatewaySloGuiMaxAgeSeconds=1\nDbusGatewaySloCoreReadMaxAgeSeconds=1\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            now = time.time()

            observe_evcs_fields(adapter, {"ac_power_w": (50.0, 0.0), "ac_current_a": (0.0, 0.0)}, now=now)
            self.assertEqual(adapter.health_role.fresh_evcs_field_float("ac_power_w", now), 50.0)
            self.assertTrue(adapter.health_role.charging_session_active_for_gui(now))

            observe_evcs_fields(adapter, {"ac_power_w": (0.0, 0.0), "ac_current_a": (0.2, 0.0)}, now=now)
            self.assertEqual(adapter.health_role.fresh_evcs_field_float("ac_current_a", now), 0.2)
            self.assertTrue(adapter.health_role.charging_session_active_for_gui(now))

            observe_evcs_fields(adapter, {"ac_power_w": (900.0, 2.0), "ac_current_a": (0.0, 0.0)}, now=now)
            self.assertEqual(adapter.health_role.fresh_evcs_field_float("ac_power_w", now), 900.0)
            observe_evcs_fields(adapter, {"ac_power_w": (900.0, 2.1)}, now=now)
            self.assertEqual(adapter.health_role.fresh_evcs_field_float("ac_power_w", now), 0.0)
            self.assertFalse(adapter.health_role.charging_session_active_for_gui(now))

    def test_core_read_stale_requires_fresh_status_and_age(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text(
                "[DEFAULT]\nDbusGatewaySloCoreReadMaxAgeSeconds=5\n",
                encoding="utf-8",
            )
            adapter = DbusAdapter(str(config_path), paths=gateway_paths(str(Path(temp_dir) / "run")))
            adapter.slo_core_read_max_age_seconds = 0.5

            self.assertTrue(
                health_slo_module.core_read_stale(
                    "grid_power_w",
                    {},
                    max_age_seconds=adapter.slo_core_read_max_age_seconds,
                )
            )
            self.assertTrue(
                health_slo_module.core_read_stale(
                    "grid_power_w",
                    {"grid_power_w_status": "fresh"},
                    max_age_seconds=adapter.slo_core_read_max_age_seconds,
                )
            )
            self.assertTrue(
                health_slo_module.core_read_stale(
                    "grid_power_w",
                    {"grid_power_w_status": "error", "grid_power_w_age_s": 0.0},
                    max_age_seconds=adapter.slo_core_read_max_age_seconds,
                )
            )
            self.assertFalse(
                health_slo_module.core_read_stale(
                    "grid_power_w",
                    {"grid_power_w_status": "fresh", "grid_power_w_age_s": 0.0},
                    max_age_seconds=adapter.slo_core_read_max_age_seconds,
                )
            )
            self.assertFalse(
                health_slo_module.core_read_stale(
                    "grid_power_w",
                    {"grid_power_w_status": "fresh", "grid_power_w_age_s": 0.5},
                    max_age_seconds=adapter.slo_core_read_max_age_seconds,
                )
            )
            self.assertTrue(
                health_slo_module.core_read_stale(
                    "grid_power_w",
                    {"grid_power_w_status": "fresh", "grid_power_w_age_s": 0.6},
                    max_age_seconds=adapter.slo_core_read_max_age_seconds,
                )
            )

    def test_health_snapshot_includes_gateway_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            paths = gateway_paths(str(Path(temp_dir) / "run"))
            adapter = DbusAdapter(str(config_path), paths=paths)

            observe_evcs_fields(adapter, {"mode": (1, 0.0), "start_stop": (1, 0.0)}, now=time.time())
            refresh = EnergyRefreshRequest("health-topology", "topology", 0.0).to_command(source="health-test")
            refresh_path = adapter.commands.enqueue(refresh)
            duplicate_refresh = Path(paths.command_dir) / "duplicate-topology-refresh.json"
            duplicate_refresh.write_text(Path(refresh_path).read_text(encoding="utf-8"), encoding="utf-8")
            adapter.core_command_mailbox.enqueue(
                core_control_command_payload(
                    "set_mode",
                    "mode",
                    1,
                    source="control-api",
                    origin="health-test",
                )
            )
            adapter.circuit.record_success(12.5)
            adapter.cache.update_value("grid_power_w", 10.0, source="grid", now=time.time() - 1.0)
            adapter.cache.mark_error("pv_power_w", source="pv", error="offline")
            adapter._last_tick_at = 123.0
            adapter._last_tick_monotonic = time.monotonic() - 0.25
            adapter._last_tick_duration_ms = 7.5
            install_mock(adapter.resource_monitor, "snapshot", MagicMock(return_value={"state": "ok"}))
            adapter.tick_health.record(duration_ms=7.5, expected_interval_s=adapter.tick_seconds)

            health = adapter.health_snapshot()

            self.assertEqual(health["state"], "ok")
            self.assertEqual(health["pending_command_count"], 1)
            self.assertEqual(health["physical_command_count"], 2)
            self.assertEqual(health["core_command_count"], 1)
            self.assertEqual(health["registered_path_count"], adapter.publication_registry.registered_path_count)
            self.assertGreater(health["registered_path_count"], 0)
            self.assertEqual(health["last_tick_at"], 123.0)
            self.assertEqual(health["tick_duration_ms"], 7.5)
            self.assertIn("discovery_last_success_at", health)
            self.assertIn("discovery_last_error", health)
            self.assertIn("discovery_next_scan_at", health)
            self.assertGreaterEqual(health["mainloop_heartbeat_age_s"], 0.0)
            self.assertGreater(health["last_success_at"], 0.0)
            self.assertEqual(health["last_error"], "")
            self.assertEqual(health["queues"]["pending_command_count"], 1)
            self.assertEqual(health["queues"]["physical_command_count"], 2)
            self.assertGreaterEqual(health["queues"]["oldest_command_age_s"], 0.0)
            self.assertEqual(
                health["queues"]["last_processed_at"],
                health["write_scheduler"]["last_processed_at"],
            )
            self.assertEqual(health["queue_classes"]["discovery"]["pending"], 1)
            self.assertEqual(health["cache_freshness"]["grid_power_w_status"], "fresh")
            self.assertEqual(health["cache_freshness"]["pv_power_w_status"], "error")
            self.assertIn("write_scheduler", health)
            self.assertIn("queue_class_budgets", health["write_scheduler"])
            self.assertIn("queue_class_usage_1s", health["write_scheduler"])
            self.assertIn("core_reads_fresh", health["slo"]["checks"])
            self.assertIn(health["backpressure"]["state"], {"ok", "congested", "slow", "protective"})
            self.assertIn("core_should_throttle", health["backpressure"])
            self.assertEqual(health["resources"]["state"], "ok")
            self.assertEqual(health["adaptive_tick_seconds"], adapter.tick_seconds)
            self.assertEqual(health["min_tick_seconds"], adapter.min_tick_seconds)
            self.assertEqual(health["max_tick_seconds"], adapter.max_tick_seconds)
            self.assertIn("last_tick_at", health["eventloop"])
            self.assertIn("mainloop_heartbeat_age_s", health["eventloop"])
            self.assertEqual(health["eventloop"]["last_tick_at"], 123.0)
            self.assertEqual(health["eventloop"]["tick_duration_ms"], 7.5)
            self.assertEqual(
                health["eventloop"]["mainloop_heartbeat_age_s"],
                health["mainloop_heartbeat_age_s"],
            )
