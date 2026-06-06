# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_update_cycle_controller_support import *


class TestUpdateCycleControllerNonary(UpdateCycleControllerTestBase):
    def test_resolve_auto_inputs_does_not_reuse_equally_stale_cache(self):
        service = SimpleNamespace(
            auto_input_cache_seconds=20.0,
            auto_pv_poll_interval_seconds=2.0,
            auto_grid_poll_interval_seconds=2.0,
            auto_battery_poll_interval_seconds=10.0,
            auto_input_validation_poll_seconds=30.0,
            _auto_cached_inputs_used=False,
            _error_state={"cache_hits": 0},
            _last_pv_value=2100.0,
            _last_pv_at=90.0,
            _last_grid_value=None,
            _last_grid_at=None,
            _last_battery_soc_value=None,
            _last_battery_soc_at=None,
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        pv_power, battery_soc, grid_power = controller.resolve_auto_inputs(
            {
                "captured_at": 100.0,
                "pv_power": 2300.0,
                "pv_captured_at": 50.0,
                "grid_power": None,
                "battery_soc": None,
            },
            100.0,
            True,
        )

        self.assertIsNone(pv_power)
        self.assertIsNone(battery_soc)
        self.assertIsNone(grid_power)
        self.assertFalse(service._auto_cached_inputs_used)
        self.assertEqual(service._error_state["cache_hits"], 0)

    def test_resolve_auto_inputs_rejects_future_source_timestamps_before_cache_fallback(self):
        service = SimpleNamespace(
            auto_input_cache_seconds=20.0,
            auto_pv_poll_interval_seconds=2.0,
            auto_grid_poll_interval_seconds=2.0,
            auto_battery_poll_interval_seconds=10.0,
            auto_input_validation_poll_seconds=30.0,
            _auto_cached_inputs_used=False,
            _error_state={"cache_hits": 0},
            _last_pv_value=2100.0,
            _last_pv_at=98.0,
            _last_grid_value=-1400.0,
            _last_grid_at=97.0,
            _last_battery_soc_value=61.0,
            _last_battery_soc_at=96.0,
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        pv_power, battery_soc, grid_power = controller.resolve_auto_inputs(
            {
                "captured_at": 100.0,
                "pv_power": 2300.0,
                "pv_captured_at": 102.0,
                "grid_power": -1500.0,
                "grid_captured_at": 103.0,
                "battery_soc": 55.0,
                "battery_captured_at": 104.0,
            },
            100.0,
            True,
        )

        self.assertEqual(pv_power, 2100.0)
        self.assertEqual(battery_soc, 61.0)
        self.assertEqual(grid_power, -1400.0)
        self.assertTrue(service._auto_cached_inputs_used)
        self.assertEqual(service._error_state["cache_hits"], 1)

    def test_auto_input_source_max_age_prefers_source_poll_budget_over_validation_budget(self):
        service = SimpleNamespace(
            auto_pv_poll_interval_seconds=2.0,
            auto_battery_poll_interval_seconds=10.0,
            auto_input_validation_poll_seconds=30.0,
        )

        self.assertEqual(UpdateCycleController._auto_input_source_max_age_seconds(service, "auto_pv_poll_interval_seconds"), 4.0)
        self.assertEqual(
            UpdateCycleController._auto_input_source_max_age_seconds(service, "auto_battery_poll_interval_seconds"),
            20.0,
        )

    def test_resolve_pm_status_for_update_rejects_worker_snapshot_older_than_soft_fail_budget(self):
        service = SimpleNamespace(
            _last_pm_status={"output": True},
            _last_pm_status_at=95.0,
            _last_pm_status_confirmed=True,
            auto_shelly_soft_fail_seconds=10.0,
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertEqual(
            controller.resolve_pm_status_for_update(
                service,
                {"pm_status": {"output": False}, "pm_confirmed": True, "pm_captured_at": 80.0},
                100.0,
            ),
            {"output": True, "_pm_confirmed": True},
        )
        self.assertEqual(service._last_pm_status, {"output": True})

    def test_resolve_pm_status_for_update_rejects_unconfirmed_local_placeholder(self):
        service = SimpleNamespace(
            _last_pm_status={"output": True},
            _last_pm_status_at=99.0,
            _last_pm_status_confirmed=False,
            _last_confirmed_pm_status=None,
            _last_confirmed_pm_status_at=None,
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertIsNone(
            controller.resolve_pm_status_for_update(
                service,
                {"pm_status": {"output": True}, "pm_confirmed": False, "pm_captured_at": 99.0},
                100.0,
            )
        )

    def test_resolve_pm_status_for_update_falls_back_to_confirmed_snapshot_for_unconfirmed_worker_data(self):
        service = SimpleNamespace(
            _last_pm_status={"output": True},
            _last_pm_status_at=99.0,
            _last_pm_status_confirmed=False,
            _last_confirmed_pm_status={"output": False},
            _last_confirmed_pm_status_at=96.0,
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertEqual(
            controller.resolve_pm_status_for_update(
                service,
                {"pm_status": {"output": True}, "pm_confirmed": False, "pm_captured_at": 99.0},
                100.0,
            ),
            {"output": False, "_pm_confirmed": True},
        )

    def test_resolve_pm_status_for_update_rejects_future_worker_snapshot(self):
        service = SimpleNamespace(
            _last_pm_status={"output": True},
            _last_pm_status_at=95.0,
            _last_pm_status_confirmed=True,
            auto_shelly_soft_fail_seconds=10.0,
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertEqual(
            controller.resolve_pm_status_for_update(
                service,
                {"pm_status": {"output": False}, "pm_confirmed": True, "pm_captured_at": 102.5},
                100.0,
            ),
            {"output": True, "_pm_confirmed": True},
        )
        self.assertEqual(service._last_pm_status, {"output": True})

    def test_resolve_pm_status_for_update_rejects_future_cached_soft_fail_snapshot(self):
        service = SimpleNamespace(
            _last_pm_status={"output": True},
            _last_pm_status_at=102.5,
            _last_pm_status_confirmed=True,
            auto_shelly_soft_fail_seconds=10.0,
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertIsNone(controller.resolve_pm_status_for_update(service, {}, 100.0))

    def test_resolve_pm_status_for_update_accepts_fresh_direct_snapshot_when_soft_fail_budget_is_zero(self):
        service = SimpleNamespace(
            _last_pm_status=None,
            _last_pm_status_at=None,
            _last_pm_status_confirmed=False,
            auto_shelly_soft_fail_seconds=0.0,
            _worker_poll_interval_seconds=1.0,
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertEqual(
            controller.resolve_pm_status_for_update(
                service,
                {"pm_status": {"output": False}, "pm_confirmed": True, "pm_captured_at": 99.6},
                100.0,
            ),
            {"output": False, "_pm_confirmed": True},
        )
        self.assertEqual(service._last_pm_status, {"output": False, "_pm_confirmed": True})
        self.assertEqual(service._last_pm_status_at, 99.6)
        self.assertTrue(service._last_pm_status_confirmed)

    def test_resolve_pm_status_for_update_rejects_inconsistent_confirmed_worker_snapshot(self):
        service = SimpleNamespace(
            _last_pm_status={"output": True},
            _last_pm_status_at=95.0,
            _last_pm_status_confirmed=True,
            auto_shelly_soft_fail_seconds=10.0,
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertEqual(
            controller.resolve_pm_status_for_update(
                service,
                {"captured_at": 100.0, "pm_status": {"apower": 1800.0}, "pm_confirmed": True, "pm_captured_at": 99.5},
                100.0,
            ),
            {"output": True, "_pm_confirmed": True},
        )
        self.assertEqual(service._last_pm_status, {"output": True})

    def test_update_offline_path_publishes_disconnected_state(self):
        service = SimpleNamespace(
            _time_now=MagicMock(return_value=200.0),
            _state_summary=lambda: "state",
            _watchdog_recover=MagicMock(),
            _ensure_auto_input_helper_process=MagicMock(),
            _refresh_auto_input_snapshot=MagicMock(),
            _get_worker_snapshot=MagicMock(return_value={"pm_status": None}),
            _last_pm_status=None,
            _last_pm_status_at=None,
            _last_confirmed_pm_status=None,
            _last_confirmed_pm_status_at=None,
            auto_shelly_soft_fail_seconds=10.0,
            _last_voltage=230.0,
            virtual_startstop=1,
            phase="L1",
            voltage_mode="phase",
            _set_health=MagicMock(),
            _publish_live_measurements=MagicMock(return_value=True),
            _publish_energy_time_measurements=MagicMock(return_value=False),
            _publish_config_paths=MagicMock(return_value=False),
            _publish_diagnostic_paths=MagicMock(return_value=False),
            _publish_dbus_path=MagicMock(return_value=False),
            _bump_update_index=MagicMock(),
            _save_runtime_state=MagicMock(),
            _ensure_observability_state=MagicMock(),
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _last_health_reason="init",
            _last_health_code=0,
            charging_started_at=None,
            energy_at_start=0.0,
            last_status=0,
            virtual_mode=0,
            virtual_enable=1,
            _dbusservice={"/Ac/Power": 0.0},
            service_name="com.victronenergy.evcharger.http_60",
            last_update=0.0,
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        result = controller.update()

        self.assertTrue(result)
        service._watchdog_recover.assert_called_once_with(200.0)
        service._publish_live_measurements.assert_called_once()
        service._set_health.assert_called_once_with("shelly-offline", cached=False)
        service._bump_update_index.assert_called_once_with(200.0)
        self.assertEqual(service.virtual_startstop, 0)
        self.assertEqual(service.last_update, 200.0)

    def test_publish_offline_update_uses_recent_confirmed_relay_state_only(self):
        service = SimpleNamespace(
            _time_now=MagicMock(return_value=200.0),
            _last_voltage=230.0,
            _last_confirmed_pm_status={"output": True},
            _last_confirmed_pm_status_at=199.0,
            _worker_poll_interval_seconds=1.0,
            relay_sync_timeout_seconds=2.0,
            virtual_startstop=0,
            phase="L1",
            voltage_mode="phase",
            _set_health=MagicMock(),
            _publish_live_measurements=MagicMock(return_value=False),
            _publish_energy_time_measurements=MagicMock(return_value=False),
            _publish_config_paths=MagicMock(return_value=False),
            _publish_diagnostic_paths=MagicMock(return_value=False),
            _publish_dbus_path=MagicMock(return_value=False),
            _save_runtime_state=MagicMock(),
            _ensure_observability_state=MagicMock(),
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _last_health_reason="init",
            _last_health_code=0,
            charging_started_at=None,
            energy_at_start=0.0,
            last_status=0,
            virtual_mode=0,
            virtual_enable=1,
            _dbusservice={"/Ac/Power": 0.0},
            service_name="svc",
            last_update=0.0,
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertTrue(controller.publish_offline_update(200.0))
        self.assertEqual(service.virtual_startstop, 1)

        service._last_confirmed_pm_status_at = 195.0
        self.assertTrue(controller.publish_offline_update(200.0))
        self.assertEqual(service.virtual_startstop, 0)

    def test_publish_offline_update_rejects_future_confirmed_relay_timestamp(self):
        service = SimpleNamespace(
            _time_now=MagicMock(return_value=200.0),
            _last_voltage=230.0,
            _last_confirmed_pm_status={"output": True},
            _last_confirmed_pm_status_at=202.5,
            _worker_poll_interval_seconds=1.0,
            relay_sync_timeout_seconds=2.0,
            virtual_startstop=0,
            phase="L1",
            voltage_mode="phase",
            _set_health=MagicMock(),
            _publish_live_measurements=MagicMock(return_value=False),
            _publish_energy_time_measurements=MagicMock(return_value=False),
            _publish_config_paths=MagicMock(return_value=False),
            _publish_diagnostic_paths=MagicMock(return_value=False),
            _publish_dbus_path=MagicMock(return_value=False),
            _save_runtime_state=MagicMock(),
            _ensure_observability_state=MagicMock(),
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _last_health_reason="init",
            _last_health_code=0,
            charging_started_at=None,
            energy_at_start=0.0,
            last_status=0,
            virtual_mode=0,
            virtual_enable=1,
            _dbusservice={"/Ac/Power": 0.0},
            service_name="svc",
            last_update=0.0,
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertTrue(controller.publish_offline_update(200.0))
        self.assertEqual(service.virtual_startstop, 0)

    def test_publish_offline_update_marks_unconfigured_service_without_shelly_offline(self):
        service = SimpleNamespace(
            _time_now=MagicMock(return_value=200.0),
            _last_voltage=230.0,
            _last_confirmed_pm_status=None,
            _last_confirmed_pm_status_at=None,
            _worker_poll_interval_seconds=1.0,
            relay_sync_timeout_seconds=2.0,
            virtual_startstop=0,
            phase="L1",
            voltage_mode="phase",
            topology_configured=False,
            host_configured=False,
            _set_health=MagicMock(),
            _publish_live_measurements=MagicMock(return_value=False),
            _publish_energy_time_measurements=MagicMock(return_value=False),
            _publish_config_paths=MagicMock(return_value=False),
            _publish_diagnostic_paths=MagicMock(return_value=False),
            _publish_dbus_path=MagicMock(return_value=False),
            _save_runtime_state=MagicMock(),
            _ensure_observability_state=MagicMock(),
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _last_health_reason="init",
            _last_health_code=0,
            _last_status_source="unknown",
            charging_started_at=None,
            energy_at_start=0.0,
            last_status=0,
            virtual_mode=0,
            virtual_enable=1,
            _dbusservice={"/Ac/Power": 0.0},
            service_name="svc",
            last_update=0.0,
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0, "not-configured": 41}.get(reason, 99))

        self.assertTrue(controller.publish_offline_update(200.0))
        service._set_health.assert_called_once_with("not-configured", cached=False)
        self.assertEqual(service._last_status_source, "not-configured")

    def test_publish_offline_update_marks_configured_split_service_offline_even_without_legacy_host(self):
        service = SimpleNamespace(
            _time_now=MagicMock(return_value=200.0),
            _last_voltage=230.0,
            _last_confirmed_pm_status=None,
            _last_confirmed_pm_status_at=None,
            _worker_poll_interval_seconds=1.0,
            relay_sync_timeout_seconds=2.0,
            virtual_startstop=0,
            phase="L1",
            voltage_mode="phase",
            topology_configured=True,
            host_configured=False,
            _set_health=MagicMock(),
            _publish_live_measurements=MagicMock(return_value=False),
            _publish_energy_time_measurements=MagicMock(return_value=False),
            _publish_config_paths=MagicMock(return_value=False),
            _publish_diagnostic_paths=MagicMock(return_value=False),
            _publish_dbus_path=MagicMock(return_value=False),
            _save_runtime_state=MagicMock(),
            _ensure_observability_state=MagicMock(),
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _last_health_reason="init",
            _last_health_code=0,
            _last_status_source="unknown",
            charging_started_at=None,
            energy_at_start=0.0,
            last_status=0,
            virtual_mode=0,
            virtual_enable=1,
            _dbusservice={"/Ac/Power": 0.0},
            service_name="svc",
            last_update=0.0,
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0, "shelly-offline": 11}.get(reason, 99))

        self.assertTrue(controller.publish_offline_update(200.0))
        service._set_health.assert_called_once_with("shelly-offline", cached=False)
        self.assertEqual(service._last_status_source, "shelly-offline")

    def test_publish_online_update_prefers_backend_phase_distribution_metadata(self):
        service = SimpleNamespace(
            phase="L1",
            voltage_mode="phase",
            _publish_live_measurements=MagicMock(return_value=False),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        controller.update_virtual_state = MagicMock(return_value=False)

        changed = controller.publish_online_update(
            {
                "output": True,
                "_phase_selection": "P1_P2",
                "_phase_powers_w": (1200.0, 1200.0, 0.0),
                "_phase_currents_a": (5.2, 5.2, 0.0),
            },
            2,
            12.5,
            True,
            2400.0,
            230.0,
            200.0,
        )

        self.assertFalse(changed)
        self.assertEqual(
            service._publish_live_measurements.call_args.args[3],
            {
                "L1": {"power": 1200.0, "voltage": 230.0, "current": 5.2},
                "L2": {"power": 1200.0, "voltage": 230.0, "current": 5.2},
                "L3": {"power": 0.0, "voltage": 230.0, "current": 0.0},
            },
        )
