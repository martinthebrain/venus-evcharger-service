# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_update_cycle_controller_support import *
from venus_evcharger.update.offline_publish import _UpdateCycleOffline


class TestUpdateCycleControllerNonary(UpdateCycleControllerTestBase):
    def test_resolve_auto_inputs_inactive_mode_resets_cached_flag_to_false_boolean(self):
        service = SimpleNamespace(_auto_cached_inputs_used=True)
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertEqual(controller.resolve_auto_inputs({"captured_at": 100.0}, 100.0, False), (None, None, None))
        self.assertIs(service._auto_cached_inputs_used, False)

    def test_extract_pm_measurements_normalizes_all_shelly_fields(self):
        service = SimpleNamespace(
            _safe_float=MagicMock(side_effect=lambda value, default: float(value) if value is not None else default)
        )

        measurements = UpdateCycleController.extract_pm_measurements(
            service,
            {
                "output": 1,
                "apower": "1234.5",
                "voltage": "229.7",
                "current": "5.38",
                "aenergy": {"total": "4321.0"},
            },
        )

        self.assertEqual(measurements, (True, 1234.5, 229.7, 5.38, 4.321))
        self.assertEqual(
            [args for args, _kwargs in service._safe_float.call_args_list],
            [("1234.5", 0.0), ("229.7", 0.0), ("5.38", 0.0), ("4321.0", 0.0)],
        )

    def test_extract_pm_measurements_uses_zero_defaults_for_missing_numeric_fields(self):
        service = SimpleNamespace(
            _safe_float=MagicMock(side_effect=lambda value, default: default if value is None else float(value))
        )

        measurements = UpdateCycleController.extract_pm_measurements(service, {})

        self.assertEqual(measurements, (False, 0.0, 0.0, 0.0, 0.0))
        self.assertEqual(
            [args for args, _kwargs in service._safe_float.call_args_list],
            [(0.0, 0.0), (0.0, 0.0), (0.0, 0.0), (0.0, 0.0)],
        )

    def test_resolve_cached_input_value_stores_fresh_values_on_service_owner(self):
        owner = SimpleNamespace()
        service = SimpleNamespace(_service=owner, auto_input_cache_seconds=60.0)

        value, cached = UpdateCycleController.resolve_cached_input_value(
            service,
            12.5,
            None,
            "_last_value",
            "_last_at",
            now=20.0,
        )

        self.assertEqual(value, 12.5)
        self.assertFalse(cached)
        self.assertEqual(owner._last_value, 12.5)
        self.assertEqual(owner._last_at, 20.0)
        self.assertFalse(hasattr(service, "_last_value"))

    def test_resolve_cached_input_value_accepts_fresh_source_at_future_tolerance_boundary(self):
        owner = SimpleNamespace()
        service = SimpleNamespace(_service=owner, auto_input_cache_seconds=60.0)

        value, cached = UpdateCycleController.resolve_cached_input_value(
            service,
            12.5,
            101.0,
            "_last_value",
            "_last_at",
            now=100.0,
        )

        self.assertEqual(value, 12.5)
        self.assertFalse(cached)
        self.assertEqual(owner._last_value, 12.5)
        self.assertEqual(owner._last_at, 101.0)

    def test_snapshot_input_from_future_honors_subclass_tolerance(self):
        class WideToleranceController(UpdateCycleController):
            FUTURE_INPUT_TIMESTAMP_TOLERANCE_SECONDS = 5.0

        self.assertFalse(WideToleranceController._snapshot_input_from_future(105.0, 100.0))
        self.assertTrue(WideToleranceController._snapshot_input_from_future(105.1, 100.0))

    def test_cached_input_accepts_exact_future_tolerance_boundary(self):
        owner = SimpleNamespace(_last_value=33.0, _last_at=101.0)

        self.assertEqual(
            UpdateCycleController._cached_input_from_service(
                owner,
                "_last_value",
                "_last_at",
                now=100.0,
                cache_max_age=60.0,
            ),
            (33.0, True),
        )

    def test_cached_input_accepts_exact_max_age_boundary(self):
        owner = SimpleNamespace(_last_value=33.0, _last_at=90.0)

        self.assertEqual(
            UpdateCycleController._cached_input_from_service(
                owner,
                "_last_value",
                "_last_at",
                now=100.0,
                cache_max_age=10.0,
            ),
            (33.0, True),
        )

    def test_auto_input_source_max_age_clamps_poll_budget_and_validation_budget(self):
        self.assertEqual(
            UpdateCycleController._auto_input_source_max_age_seconds(
                SimpleNamespace(auto_pv_poll_interval_seconds=0.0, auto_input_validation_poll_seconds=12.0),
                "auto_pv_poll_interval_seconds",
            ),
            12.0,
        )
        self.assertEqual(
            UpdateCycleController._auto_input_source_max_age_seconds(
                SimpleNamespace(auto_pv_poll_interval_seconds=-3.0, auto_input_validation_poll_seconds=12.0),
                "auto_pv_poll_interval_seconds",
            ),
            12.0,
        )
        self.assertEqual(
            UpdateCycleController._auto_input_source_max_age_seconds(
                SimpleNamespace(auto_pv_poll_interval_seconds=0.1, auto_input_validation_poll_seconds=0.5),
                "auto_pv_poll_interval_seconds",
            ),
            1.0,
        )
        self.assertEqual(
            UpdateCycleController._auto_input_source_max_age_seconds(
                SimpleNamespace(auto_input_validation_poll_seconds=0.5),
                "missing_poll_interval_seconds",
            ),
            1.0,
        )
        self.assertEqual(
            UpdateCycleController._auto_input_source_max_age_seconds(
                SimpleNamespace(auto_input_validation_poll_seconds=12.0),
                "missing_poll_interval_seconds",
            ),
            12.0,
        )
        self.assertEqual(
            UpdateCycleController._auto_input_source_max_age_seconds(
                SimpleNamespace(auto_pv_poll_interval_seconds=1.0, auto_input_validation_poll_seconds=0.0),
                "auto_pv_poll_interval_seconds",
            ),
            2.0,
        )
        self.assertEqual(
            UpdateCycleController._auto_input_source_max_age_seconds(
                SimpleNamespace(auto_pv_poll_interval_seconds=0.0),
                "auto_pv_poll_interval_seconds",
            ),
            30.0,
        )
        self.assertEqual(
            UpdateCycleController._auto_input_source_max_age_seconds(
                SimpleNamespace(auto_pv_poll_interval_seconds=0.0, auto_input_validation_poll_seconds=0.0),
                "auto_pv_poll_interval_seconds",
            ),
            30.0,
        )

    def test_resolve_auto_inputs_records_complete_energy_cluster_snapshot(self):
        owner = SimpleNamespace(_last_energy_learning_profiles={})
        service = SimpleNamespace(
            _service=owner,
            auto_input_cache_seconds=120.0,
            auto_pv_poll_interval_seconds=2.0,
            auto_grid_poll_interval_seconds=3.0,
            auto_battery_poll_interval_seconds=10.0,
            auto_input_validation_poll_seconds=30.0,
            _auto_cached_inputs_used=False,
            _error_state={"cache_hits": 0},
            _last_pv_value=None,
            _last_pv_at=None,
            _last_grid_value=None,
            _last_grid_at=None,
            _last_battery_soc_value=None,
            _last_battery_soc_at=None,
            _last_combined_battery_charge_power_w=None,
            _last_combined_battery_charge_power_at=None,
            _last_combined_battery_discharge_power_w=None,
            _last_combined_battery_discharge_power_at=None,
            _last_combined_battery_net_power_w=None,
            _last_combined_battery_net_power_at=None,
            _last_combined_battery_ac_power_w=None,
            _last_combined_battery_ac_power_at=None,
        )
        battery_sources = [{"source_id": "battery"}, {"source_id": "hybrid"}]
        learning_profiles = {"battery": {"power_w": 1400.0}}
        snapshot = {
            "captured_at": 100.0,
            "pv_power": 2500.0,
            "pv_captured_at": 99.0,
            "grid_power": -1500.0,
            "grid_captured_at": 98.0,
            "battery_soc": 63.5,
            "battery_captured_at": 97.0,
            "battery_combined_soc": 64.1,
            "battery_combined_usable_capacity_wh": 9600.0,
            "battery_combined_charge_power_w": 111.0,
            "battery_combined_discharge_power_w": 222.0,
            "battery_combined_net_power_w": -333.0,
            "battery_combined_ac_power_w": 444.0,
            "battery_combined_pv_input_power_w": 555.0,
            "battery_combined_grid_interaction_w": -666.0,
            "battery_average_confidence": 0.87,
            "battery_source_count": 7,
            "battery_online_source_count": 6,
            "battery_valid_soc_source_count": 5,
            "battery_battery_source_count": 4,
            "battery_hybrid_inverter_source_count": 3,
            "battery_inverter_source_count": 2,
            "battery_sources": battery_sources,
            "battery_learning_profiles": learning_profiles,
        }
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertEqual(controller.resolve_auto_inputs(snapshot, 100.0, True), (2500.0, 63.5, -1500.0))

        self.assertEqual(owner._last_pv_value, 2500.0)
        self.assertEqual(owner._last_pv_at, 99.0)
        self.assertEqual(owner._last_grid_value, -1500.0)
        self.assertEqual(owner._last_grid_at, 98.0)
        self.assertEqual(owner._last_battery_soc_value, 63.5)
        self.assertEqual(owner._last_battery_soc_at, 97.0)
        self.assertEqual(owner._last_combined_battery_charge_power_w, 111.0)
        self.assertEqual(owner._last_combined_battery_charge_power_at, 97.0)
        self.assertEqual(owner._last_combined_battery_discharge_power_w, 222.0)
        self.assertEqual(owner._last_combined_battery_discharge_power_at, 97.0)
        self.assertEqual(owner._last_combined_battery_net_power_w, -333.0)
        self.assertEqual(owner._last_combined_battery_net_power_at, 97.0)
        self.assertEqual(owner._last_combined_battery_ac_power_w, 444.0)
        self.assertEqual(owner._last_combined_battery_ac_power_at, 97.0)
        self.assertEqual(
            owner._last_energy_cluster,
            {
                "battery_combined_soc": 64.1,
                "battery_combined_usable_capacity_wh": 9600.0,
                "battery_combined_charge_power_w": 111.0,
                "battery_combined_discharge_power_w": 222.0,
                "battery_combined_net_power_w": -333.0,
                "battery_combined_ac_power_w": 444.0,
                "battery_combined_pv_input_power_w": 555.0,
                "battery_combined_grid_interaction_w": -666.0,
                "grid_power_w": -1500.0,
                "grid_captured_at": 98.0,
                "grid_gateway_power_w": None,
                "grid_gateway_captured_at": None,
                "grid_primary_power_w": None,
                "grid_primary_captured_at": None,
                "grid_selected_source_id": None,
                "grid_fusion_state": None,
                "grid_fusion_confidence": None,
                "grid_fusion_primary_valid": None,
                "grid_fusion_backup_valid": None,
                "grid_fusion_difference_watts": None,
                "grid_fusion_tolerance_watts": None,
                "battery_average_confidence": 0.87,
                "battery_source_count": 7,
                "battery_online_source_count": 6,
                "battery_valid_soc_source_count": 5,
                "battery_battery_source_count": 4,
                "battery_hybrid_inverter_source_count": 3,
                "battery_inverter_source_count": 2,
                "battery_sources": [{"source_id": "battery"}, {"source_id": "hybrid"}],
            },
        )
        self.assertEqual(owner._last_energy_learning_profiles, learning_profiles)
        self.assertIsNot(owner._last_energy_cluster["battery_sources"], battery_sources)
        self.assertIsNot(owner._last_energy_learning_profiles, learning_profiles)
        battery_sources.append({"source_id": "late"})
        learning_profiles["late"] = {"power_w": 1.0}
        self.assertEqual(owner._last_energy_cluster["battery_sources"], [{"source_id": "battery"}, {"source_id": "hybrid"}])
        self.assertEqual(owner._last_energy_learning_profiles, {"battery": {"power_w": 1400.0}})

    def test_resolve_auto_inputs_rejects_all_over_age_sources_by_their_own_poll_budget(self):
        service = SimpleNamespace(
            auto_input_cache_seconds=120.0,
            auto_pv_poll_interval_seconds=2.0,
            auto_grid_poll_interval_seconds=3.0,
            auto_battery_poll_interval_seconds=4.0,
            auto_input_validation_poll_seconds=30.0,
            _auto_cached_inputs_used=False,
            _error_state={"cache_hits": 0},
            _last_pv_value=None,
            _last_pv_at=None,
            _last_grid_value=None,
            _last_grid_at=None,
            _last_battery_soc_value=None,
            _last_battery_soc_at=None,
            _last_combined_battery_charge_power_w=None,
            _last_combined_battery_charge_power_at=None,
            _last_combined_battery_discharge_power_w=None,
            _last_combined_battery_discharge_power_at=None,
            _last_combined_battery_net_power_w=None,
            _last_combined_battery_net_power_at=None,
            _last_combined_battery_ac_power_w=None,
            _last_combined_battery_ac_power_at=None,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        result = controller.resolve_auto_inputs(
            {
                "captured_at": 100.0,
                "pv_power": 2500.0,
                "pv_captured_at": 95.0,
                "grid_power": -1500.0,
                "grid_captured_at": 93.0,
                "battery_soc": 63.5,
                "battery_captured_at": 91.0,
            },
            100.0,
            True,
        )

        self.assertEqual(result, (None, None, None))
        self.assertFalse(service._auto_cached_inputs_used)
        self.assertEqual(service._error_state["cache_hits"], 0)

    def test_resolve_auto_inputs_uses_generic_captured_at_when_source_timestamps_are_missing(self):
        service = SimpleNamespace(
            auto_input_cache_seconds=120.0,
            auto_pv_poll_interval_seconds=2.0,
            auto_grid_poll_interval_seconds=3.0,
            auto_battery_poll_interval_seconds=10.0,
            auto_input_validation_poll_seconds=30.0,
            _auto_cached_inputs_used=False,
            _error_state={"cache_hits": 0},
            _last_pv_value=None,
            _last_pv_at=None,
            _last_grid_value=None,
            _last_grid_at=None,
            _last_battery_soc_value=None,
            _last_battery_soc_at=None,
            _last_combined_battery_charge_power_w=None,
            _last_combined_battery_charge_power_at=None,
            _last_combined_battery_discharge_power_w=None,
            _last_combined_battery_discharge_power_at=None,
            _last_combined_battery_net_power_w=None,
            _last_combined_battery_net_power_at=None,
            _last_combined_battery_ac_power_w=None,
            _last_combined_battery_ac_power_at=None,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertEqual(
            controller.resolve_auto_inputs(
                {
                    "captured_at": 99.0,
                    "pv_power": 2500.0,
                    "grid_power": -1500.0,
                    "battery_soc": 63.5,
                },
                100.0,
                True,
            ),
            (2500.0, 63.5, -1500.0),
        )
        self.assertEqual(service._last_pv_at, 99.0)
        self.assertEqual(service._last_grid_at, 99.0)
        self.assertEqual(service._last_battery_soc_at, 99.0)

    def test_resolve_auto_inputs_counts_a_single_grid_cache_hit_from_nonzero_baseline(self):
        service = SimpleNamespace(
            auto_input_cache_seconds=120.0,
            auto_pv_poll_interval_seconds=2.0,
            auto_grid_poll_interval_seconds=3.0,
            auto_battery_poll_interval_seconds=10.0,
            auto_input_validation_poll_seconds=30.0,
            _auto_cached_inputs_used=False,
            _error_state={"cache_hits": 5},
            _last_pv_value=None,
            _last_pv_at=None,
            _last_grid_value=-1500.0,
            _last_grid_at=99.0,
            _last_battery_soc_value=None,
            _last_battery_soc_at=None,
            _last_combined_battery_charge_power_w=None,
            _last_combined_battery_charge_power_at=None,
            _last_combined_battery_discharge_power_w=None,
            _last_combined_battery_discharge_power_at=None,
            _last_combined_battery_net_power_w=None,
            _last_combined_battery_net_power_at=None,
            _last_combined_battery_ac_power_w=None,
            _last_combined_battery_ac_power_at=None,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertEqual(controller.resolve_auto_inputs({"captured_at": 100.0}, 100.0, True), (None, None, -1500.0))
        self.assertTrue(service._auto_cached_inputs_used)
        self.assertEqual(service._error_state["cache_hits"], 6)

    def test_resolve_auto_inputs_uses_cached_combined_battery_metrics_when_snapshot_values_are_missing(self):
        service = SimpleNamespace(
            auto_input_cache_seconds=120.0,
            auto_pv_poll_interval_seconds=2.0,
            auto_grid_poll_interval_seconds=3.0,
            auto_battery_poll_interval_seconds=10.0,
            auto_input_validation_poll_seconds=30.0,
            _auto_cached_inputs_used=False,
            _error_state={"cache_hits": 0},
            _last_pv_value=None,
            _last_pv_at=None,
            _last_grid_value=None,
            _last_grid_at=None,
            _last_battery_soc_value=None,
            _last_battery_soc_at=None,
            _last_combined_battery_charge_power_w=111.0,
            _last_combined_battery_charge_power_at=95.0,
            _last_combined_battery_discharge_power_w=222.0,
            _last_combined_battery_discharge_power_at=96.0,
            _last_combined_battery_net_power_w=-333.0,
            _last_combined_battery_net_power_at=97.0,
            _last_combined_battery_ac_power_w=444.0,
            _last_combined_battery_ac_power_at=98.0,
            _last_energy_learning_profiles={"keep": True},
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        controller.resolve_auto_inputs({"captured_at": 100.0}, 100.0, True)

        self.assertEqual(service._last_energy_cluster["battery_combined_charge_power_w"], 111.0)
        self.assertEqual(service._last_energy_cluster["battery_combined_discharge_power_w"], 222.0)
        self.assertEqual(service._last_energy_cluster["battery_combined_net_power_w"], -333.0)
        self.assertEqual(service._last_energy_cluster["battery_combined_ac_power_w"], 444.0)
        self.assertEqual(service._last_energy_learning_profiles, {})

    def test_resolve_auto_inputs_rejects_over_age_combined_battery_snapshot_metrics_by_battery_poll_budget(self):
        service = SimpleNamespace(
            auto_input_cache_seconds=120.0,
            auto_pv_poll_interval_seconds=2.0,
            auto_grid_poll_interval_seconds=3.0,
            auto_battery_poll_interval_seconds=4.0,
            auto_input_validation_poll_seconds=30.0,
            _auto_cached_inputs_used=False,
            _error_state={"cache_hits": 0},
            _last_pv_value=None,
            _last_pv_at=None,
            _last_grid_value=None,
            _last_grid_at=None,
            _last_battery_soc_value=None,
            _last_battery_soc_at=None,
            _last_combined_battery_charge_power_w=None,
            _last_combined_battery_charge_power_at=None,
            _last_combined_battery_discharge_power_w=None,
            _last_combined_battery_discharge_power_at=None,
            _last_combined_battery_net_power_w=None,
            _last_combined_battery_net_power_at=None,
            _last_combined_battery_ac_power_w=None,
            _last_combined_battery_ac_power_at=None,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        controller.resolve_auto_inputs(
            {
                "captured_at": 100.0,
                "battery_captured_at": 91.0,
                "battery_combined_charge_power_w": 111.0,
                "battery_combined_discharge_power_w": 222.0,
                "battery_combined_net_power_w": -333.0,
                "battery_combined_ac_power_w": 444.0,
            },
            100.0,
            True,
        )

        self.assertIsNone(service._last_energy_cluster["battery_combined_charge_power_w"])
        self.assertIsNone(service._last_energy_cluster["battery_combined_discharge_power_w"])
        self.assertIsNone(service._last_energy_cluster["battery_combined_net_power_w"])
        self.assertIsNone(service._last_energy_cluster["battery_combined_ac_power_w"])

    def test_energy_cluster_snapshot_uses_documented_defaults_for_missing_optional_fields(self):
        service = SimpleNamespace(
            auto_input_cache_seconds=120.0,
            auto_pv_poll_interval_seconds=2.0,
            auto_grid_poll_interval_seconds=3.0,
            auto_battery_poll_interval_seconds=10.0,
            auto_input_validation_poll_seconds=30.0,
            _auto_cached_inputs_used=False,
            _error_state={"cache_hits": 0},
            _last_pv_value=None,
            _last_pv_at=None,
            _last_grid_value=None,
            _last_grid_at=None,
            _last_battery_soc_value=None,
            _last_battery_soc_at=None,
            _last_combined_battery_charge_power_w=None,
            _last_combined_battery_charge_power_at=None,
            _last_combined_battery_discharge_power_w=None,
            _last_combined_battery_discharge_power_at=None,
            _last_combined_battery_net_power_w=None,
            _last_combined_battery_net_power_at=None,
            _last_combined_battery_ac_power_w=None,
            _last_combined_battery_ac_power_at=None,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        controller.resolve_auto_inputs({"captured_at": 100.0}, 100.0, True)

        self.assertEqual(
            service._last_energy_cluster,
            {
                "battery_combined_soc": None,
                "battery_combined_usable_capacity_wh": None,
                "battery_combined_charge_power_w": None,
                "battery_combined_discharge_power_w": None,
                "battery_combined_net_power_w": None,
                "battery_combined_ac_power_w": None,
                "battery_combined_pv_input_power_w": None,
                "battery_combined_grid_interaction_w": None,
                "grid_power_w": None,
                "grid_captured_at": None,
                "grid_gateway_power_w": None,
                "grid_gateway_captured_at": None,
                "grid_primary_power_w": None,
                "grid_primary_captured_at": None,
                "grid_selected_source_id": None,
                "grid_fusion_state": None,
                "grid_fusion_confidence": None,
                "grid_fusion_primary_valid": None,
                "grid_fusion_backup_valid": None,
                "grid_fusion_difference_watts": None,
                "grid_fusion_tolerance_watts": None,
                "battery_average_confidence": None,
                "battery_source_count": 0,
                "battery_online_source_count": 0,
                "battery_valid_soc_source_count": 0,
                "battery_battery_source_count": 0,
                "battery_hybrid_inverter_source_count": 0,
                "battery_inverter_source_count": 0,
                "battery_sources": [],
            },
        )

    @staticmethod
    def _offline_service(**overrides):
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
            topology_configured=True,
            host_configured=True,
            _set_health=MagicMock(),
            _publish_live_measurements=MagicMock(return_value=False),
            _publish_energy_time_measurements=MagicMock(return_value=False),
            _publish_config_paths=MagicMock(return_value=False),
            _publish_diagnostic_paths=MagicMock(return_value=False),
            _publish_dbus_field=MagicMock(return_value=False),
            _save_runtime_state=MagicMock(),
            _ensure_observability_state=MagicMock(),
            _publish_companion_dbus_bridge=MagicMock(),
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
        for name, value in overrides.items():
            setattr(service, name, value)
        return service

    @staticmethod
    def _offline_controller(service):
        return UpdateCycleController(service, _phase_values, lambda reason: {"init": 0, "shelly-offline": 11, "not-configured": 41}.get(reason, 99))

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

    def test_pm_snapshot_contracts_normalize_worker_payload_timestamp_and_confirmation(self):
        pm_snapshot = UpdateCycleController

        self.assertIs(pm_snapshot.CLAMP_WORKER_PM_FUTURE_TIMESTAMPS, False)
        self.assertEqual(pm_snapshot._worker_pm_snapshot_data({}, 100.0), (None, False, 100.0))
        self.assertEqual(pm_snapshot._worker_pm_snapshot_data({"pm_status": "bad"}, 100.0), (None, False, 100.0))
        self.assertEqual(
            pm_snapshot._worker_pm_snapshot_data(
                {
                    "captured_at": 96.0,
                    "pm_status": {"output": False, "apower": 12.0},
                    "pm_confirmed": 1,
                    "pm_captured_at": 95.5,
                },
                100.0,
            ),
            ({"output": False, "apower": 12.0}, True, 95.5),
        )
        self.assertEqual(
            pm_snapshot._worker_pm_snapshot_data(
                {"pm_status": {"output": True}, "pm_confirmed": True},
                100.0,
            ),
            ({"output": True}, True, 100.0),
        )
        self.assertEqual(
            pm_snapshot._worker_pm_snapshot_data(
                {"captured_at": 94.0, "pm_status": {"output": True}, "pm_confirmed": False},
                100.0,
            ),
            ({"output": True}, False, 94.0),
        )
        self.assertEqual(
            pm_snapshot._worker_pm_snapshot_data(
                {"pm_status": {"output": False}, "pm_confirmed": True, "pm_captured_at": 102.5},
                100.0,
            ),
            ({"output": False}, True, 102.5),
        )
        normalized = {
            "captured_at": 90.0,
            "pm_status": {"output": True},
            "pm_confirmed": True,
            "pm_captured_at": None,
        }
        payload = pm_snapshot._worker_pm_status_payload(normalized)
        self.assertEqual(payload, {"output": True})
        self.assertIsNot(payload, normalized["pm_status"])
        self.assertTrue(pm_snapshot._worker_pm_confirmed(normalized))
        self.assertEqual(pm_snapshot._worker_pm_snapshot_timestamp(normalized), 90.0)
        normalized["pm_captured_at"] = 91.0
        self.assertEqual(pm_snapshot._worker_pm_snapshot_timestamp(normalized), 91.0)

    def test_pm_snapshot_contracts_remember_only_confirmed_cache_as_confirmed(self):
        pm_snapshot = UpdateCycleController
        service = SimpleNamespace()
        source_status = {"output": True, "apower": 1800.0}

        pm_snapshot._remember_pm_snapshot(service, source_status, 95.0, False)

        self.assertEqual(service._last_pm_status, {"output": True, "apower": 1800.0, "_pm_confirmed": False})
        self.assertEqual(service._last_pm_status_at, 95.0)
        self.assertFalse(service._last_pm_status_confirmed)
        self.assertFalse(hasattr(service, "_last_confirmed_pm_status"))
        source_status["output"] = False
        self.assertTrue(service._last_pm_status["output"])

        pm_snapshot._remember_pm_snapshot(service, {"output": False}, 96.0, True)

        self.assertEqual(service._last_pm_status, {"output": False, "_pm_confirmed": True})
        self.assertEqual(service._last_pm_status_at, 96.0)
        self.assertTrue(service._last_pm_status_confirmed)
        self.assertEqual(service._last_confirmed_pm_status, {"output": False, "_pm_confirmed": True})
        self.assertEqual(service._last_confirmed_pm_status_at, 96.0)

    def test_pm_snapshot_contracts_soft_fail_cache_prioritizes_explicit_confirmed_sample(self):
        service = SimpleNamespace(
            _last_confirmed_pm_status={"output": False, "source": "confirmed"},
            _last_confirmed_pm_status_at=95.0,
            _last_pm_status={"output": True, "source": "legacy"},
            _last_pm_status_at=99.0,
            _last_pm_status_confirmed=True,
        )

        self.assertEqual(
            UpdateCycleController._cached_pm_status_for_soft_fail(service, 100.0, 10.0),
            {"output": False, "source": "confirmed", "_pm_confirmed": True},
        )

        service._last_confirmed_pm_status_at = 80.0
        self.assertEqual(
            UpdateCycleController._cached_pm_status_for_soft_fail(service, 100.0, 10.0),
            {"output": True, "source": "legacy", "_pm_confirmed": True},
        )

        service._last_pm_status_confirmed = False
        self.assertIsNone(UpdateCycleController._cached_pm_status_for_soft_fail(service, 100.0, 10.0))
        self.assertIsNone(
            UpdateCycleController._cached_pm_status_for_soft_fail(
                SimpleNamespace(_last_pm_status_confirmed=True),
                100.0,
                10.0,
            )
        )
        self.assertIsNone(
            UpdateCycleController._cached_pm_status_for_soft_fail(
                SimpleNamespace(_last_pm_status_confirmed=True, _last_pm_status={"output": True}),
                100.0,
                10.0,
            )
        )
        self.assertFalse(UpdateCycleController._last_pm_status_marked_confirmed(SimpleNamespace()))
        self.assertFalse(UpdateCycleController._last_pm_status_marked_confirmed(SimpleNamespace(_last_pm_status_confirmed=0)))
        self.assertTrue(UpdateCycleController._last_pm_status_marked_confirmed(SimpleNamespace(_last_pm_status_confirmed=1)))

    def test_pm_snapshot_contracts_fresh_confirmed_cache_requires_dict_timestamp_and_budget(self):
        pm_snapshot = UpdateCycleController

        self.assertIsNone(pm_snapshot._fresh_confirmed_pm_status(None, 95.0, 100.0, 10.0))
        self.assertIsNone(pm_snapshot._fresh_confirmed_pm_status([], 95.0, 100.0, 10.0))
        self.assertIsNone(pm_snapshot._fresh_confirmed_pm_status({"output": True}, None, 100.0, 10.0))
        self.assertEqual(
            pm_snapshot._fresh_confirmed_pm_status({"output": True}, 90.0, 100.0, 10.0),
            {"output": True, "_pm_confirmed": True},
        )
        self.assertIsNone(pm_snapshot._fresh_confirmed_pm_status({"output": True}, 89.99, 100.0, 10.0))
        self.assertEqual(
            pm_snapshot._fresh_confirmed_pm_status({"output": False}, 101.0, 100.0, 10.0),
            {"output": False, "_pm_confirmed": True},
        )
        self.assertIsNone(pm_snapshot._fresh_confirmed_pm_status({"output": False}, 101.01, 100.0, 10.0))

    def test_pm_snapshot_contracts_worker_poll_window_and_storage_decision_edges(self):
        pm_snapshot = UpdateCycleController

        self.assertEqual(pm_snapshot._direct_pm_snapshot_max_age_seconds(SimpleNamespace()), 1.0)
        self.assertEqual(pm_snapshot._direct_pm_snapshot_max_age_seconds(SimpleNamespace(_worker_poll_interval_seconds=0.0)), 1.0)
        self.assertEqual(pm_snapshot._direct_pm_snapshot_max_age_seconds(SimpleNamespace(_worker_poll_interval_seconds=-1.0)), 1.0)
        self.assertEqual(pm_snapshot._direct_pm_snapshot_max_age_seconds(SimpleNamespace(_worker_poll_interval_seconds="bad")), 1.0)
        self.assertEqual(pm_snapshot._direct_pm_snapshot_max_age_seconds(SimpleNamespace(_worker_poll_interval_seconds=0.25)), 1.0)
        self.assertEqual(pm_snapshot._direct_pm_snapshot_max_age_seconds(SimpleNamespace(_worker_poll_interval_seconds=2.0)), 4.0)

        service = SimpleNamespace(_worker_poll_interval_seconds=0.6, _last_pm_status_at=98.0)
        self.assertTrue(pm_snapshot._pm_snapshot_within_soft_fail_budget(service, 100.0, 99.0, 0.0))
        self.assertTrue(pm_snapshot._pm_snapshot_within_soft_fail_budget(service, 100.0, 98.81, 0.0))
        self.assertFalse(pm_snapshot._pm_snapshot_within_soft_fail_budget(service, 100.0, 98.79, 0.0))
        self.assertTrue(pm_snapshot._pm_snapshot_within_soft_fail_budget(service, 100.0, 95.0, 5.0))
        self.assertFalse(pm_snapshot._pm_snapshot_within_soft_fail_budget(service, 100.0, 94.99, 5.0))
        self.assertFalse(pm_snapshot._pm_snapshot_newer_than_last(service, 97.99))
        self.assertTrue(pm_snapshot._pm_snapshot_newer_than_last(service, 98.0))
        self.assertTrue(pm_snapshot._pm_snapshot_newer_than_last(service, 98.01))
        dual_cache_service = SimpleNamespace(_last_pm_status_at=90.0, _last_confirmed_pm_status_at=95.0)
        self.assertEqual(pm_snapshot._remembered_pm_snapshot_timestamp(dual_cache_service), 95.0)
        self.assertEqual(
            pm_snapshot._remembered_pm_snapshot_timestamp(SimpleNamespace(_last_confirmed_pm_status_at=95.0)),
            95.0,
        )
        self.assertFalse(pm_snapshot._pm_snapshot_newer_than_last(dual_cache_service, 94.99))
        self.assertTrue(pm_snapshot._pm_snapshot_newer_than_last(dual_cache_service, 95.0))

        self.assertEqual(pm_snapshot._pm_snapshot_storage_decision(service, 100.0, 98.79, 0.0), (True, False))
        self.assertEqual(pm_snapshot._pm_snapshot_storage_decision(service, 100.0, 97.99, 0.0), (False, False))
        self.assertEqual(pm_snapshot._pm_snapshot_storage_decision(service, 100.0, 98.81, 0.0), (True, True))
        self.assertFalse(pm_snapshot._pm_snapshot_from_future(101.0, 100.0))
        self.assertTrue(pm_snapshot._pm_snapshot_from_future(101.01, 100.0))
        wide_tolerance = type(
            "WideTolerancePmSnapshot",
            (UpdateCycleController,),
            {"FUTURE_INPUT_TIMESTAMP_TOLERANCE_SECONDS": 2.0},
        )
        self.assertFalse(wide_tolerance._pm_snapshot_from_future(102.0, 100.0))
        self.assertTrue(wide_tolerance._pm_snapshot_from_future(102.01, 100.0))
        self.assertEqual(
            wide_tolerance._fresh_confirmed_pm_status({"output": True}, 102.0, 100.0, 10.0),
            {"output": True, "_pm_confirmed": True},
        )

    def test_pm_snapshot_contracts_resolve_uses_cache_for_old_or_unconfirmed_worker_payloads(self):
        service = SimpleNamespace(
            _last_confirmed_pm_status={"output": True, "source": "confirmed"},
            _last_confirmed_pm_status_at=95.0,
            _last_pm_status=None,
            _last_pm_status_at=None,
            _last_pm_status_confirmed=False,
            auto_shelly_soft_fail_seconds=10.0,
            _worker_poll_interval_seconds=0.6,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertEqual(
            controller.resolve_pm_status_for_update(
                service,
                {"pm_status": {"output": False, "source": "worker"}, "pm_confirmed": False, "pm_captured_at": 99.0},
                100.0,
            ),
            {"output": True, "source": "confirmed", "_pm_confirmed": True},
        )
        self.assertFalse(hasattr(service, "_last_pm_status_confirmed") and service._last_pm_status_confirmed)

        self.assertEqual(
            controller.resolve_pm_status_for_update(
                service,
                {"pm_status": {"output": False, "source": "old-worker"}, "pm_confirmed": True, "pm_captured_at": 80.0},
                100.0,
            ),
            {"output": True, "source": "confirmed", "_pm_confirmed": True},
        )
        self.assertIsNone(service._last_pm_status)
        self.assertIsNone(service._last_pm_status_at)

        short_soft_fail_service = SimpleNamespace(
            _last_confirmed_pm_status={"output": True, "source": "too-old-confirmed"},
            _last_confirmed_pm_status_at=95.0,
            _last_pm_status=None,
            _last_pm_status_at=None,
            _last_pm_status_confirmed=False,
            auto_shelly_soft_fail_seconds=4.0,
            _worker_poll_interval_seconds=0.6,
        )
        short_soft_fail_controller = UpdateCycleController(
            short_soft_fail_service,
            _phase_values,
            lambda reason: {"init": 0}.get(reason, 99),
        )

        self.assertIsNone(
            short_soft_fail_controller.resolve_pm_status_for_update(
                short_soft_fail_service,
                {"pm_status": {"output": False}, "pm_confirmed": False, "pm_captured_at": 99.0},
                100.0,
            )
        )
        default_soft_fail_service = SimpleNamespace(
            _last_confirmed_pm_status={"output": True, "source": "default-too-old-confirmed"},
            _last_confirmed_pm_status_at=89.5,
            _last_pm_status=None,
            _last_pm_status_at=None,
            _last_pm_status_confirmed=False,
            _worker_poll_interval_seconds=0.6,
        )
        default_soft_fail_controller = UpdateCycleController(
            default_soft_fail_service,
            _phase_values,
            lambda reason: {"init": 0}.get(reason, 99),
        )

        self.assertIsNone(
            default_soft_fail_controller.resolve_pm_status_for_update(
                default_soft_fail_service,
                {"pm_status": {"output": False}, "pm_confirmed": False, "pm_captured_at": 99.0},
                100.0,
            )
        )

    def test_short_network_gap_uses_confirmed_pm_cache_without_offline_panic(self):
        service = SimpleNamespace(
            _time_now=MagicMock(return_value=100.0),
            _state_summary=lambda: "state",
            _watchdog_recover=MagicMock(),
            _ensure_auto_input_helper_process=MagicMock(),
            _refresh_auto_input_snapshot=MagicMock(),
            _get_worker_snapshot=MagicMock(return_value={"pm_status": None}),
            _last_pm_status=None,
            _last_pm_status_at=None,
            _last_pm_status_confirmed=False,
            _last_confirmed_pm_status={"output": True, "apower": 1800.0},
            _last_confirmed_pm_status_at=95.0,
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with (
            patch.object(controller, "_run_online_update_cycle") as online_update,
            patch.object(controller, "publish_offline_update") as offline_update,
            patch.object(controller, "_software_update_housekeeping") as housekeeping,
        ):
            result = controller.update()

        self.assertTrue(result)
        service._watchdog_recover.assert_called_once_with(100.0)
        service._get_worker_snapshot.assert_called_once_with()
        online_update.assert_called_once_with({"output": True, "apower": 1800.0, "_pm_confirmed": True}, {"pm_status": None}, 100.0)
        offline_update.assert_not_called()
        housekeeping.assert_called_once_with(service, 100.0)

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
            _publish_dbus_field=MagicMock(return_value=False),
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
            _last_successful_update_at=None,
            _last_recovery_attempt_at=150.0,
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
            _publish_companion_dbus_bridge=MagicMock(),
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
        self.assertEqual(service._last_successful_update_at, 200.0)
        self.assertIsNone(service._last_recovery_attempt_at)
        service._publish_companion_dbus_bridge.assert_called_once_with(200.0)

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
            _publish_dbus_field=MagicMock(return_value=False),
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
            _publish_dbus_field=MagicMock(return_value=False),
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
            _publish_dbus_field=MagicMock(return_value=False),
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
            _publish_dbus_field=MagicMock(return_value=False),
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

    def test_offline_health_reason_falls_back_to_legacy_host_config_when_topology_absent(self):
        service = self._offline_service(host_configured=False)
        delattr(service, "topology_configured")

        self.assertEqual(_UpdateCycleOffline._offline_health_reason(service), "not-configured")

        service.host_configured = True
        self.assertEqual(_UpdateCycleOffline._offline_health_reason(service), "shelly-offline")

    def test_offline_confirmed_relay_age_budget_uses_smallest_positive_source(self):
        service = SimpleNamespace()
        self.assertEqual(_UpdateCycleOffline._offline_confirmed_relay_max_age_seconds(service), 2.0)

        service._worker_poll_interval_seconds = 0.75
        self.assertEqual(_UpdateCycleOffline._offline_confirmed_relay_max_age_seconds(service), 1.5)

        service.relay_sync_timeout_seconds = 1.25
        self.assertEqual(_UpdateCycleOffline._offline_confirmed_relay_max_age_seconds(service), 1.25)

        service._worker_poll_interval_seconds = 5.0
        service.relay_sync_timeout_seconds = 0.75
        self.assertEqual(_UpdateCycleOffline._offline_confirmed_relay_max_age_seconds(service), 1.0)

        service._worker_poll_interval_seconds = 0.0
        service.relay_sync_timeout_seconds = 0.0
        self.assertEqual(_UpdateCycleOffline._offline_confirmed_relay_max_age_seconds(service), 2.0)

    def test_offline_confirmed_relay_sample_contracts_are_strict(self):
        self.assertTrue(_UpdateCycleOffline._offline_confirmed_relay_sample_present({"output": False}, 10.0))
        self.assertFalse(_UpdateCycleOffline._offline_confirmed_relay_sample_present({"power": 0.0}, 10.0))
        self.assertFalse(_UpdateCycleOffline._offline_confirmed_relay_sample_present({"output": False}, None))
        self.assertFalse(_UpdateCycleOffline._offline_confirmed_relay_sample_present(None, 10.0))

        service = self._offline_service(_worker_poll_interval_seconds=1.0, relay_sync_timeout_seconds=2.0)
        self.assertTrue(UpdateCycleController._offline_confirmed_relay_sample_fresh(service, 200.0, 201.0))
        self.assertFalse(UpdateCycleController._offline_confirmed_relay_sample_fresh(service, 200.0, 201.1))
        self.assertFalse(UpdateCycleController._offline_confirmed_relay_sample_fresh(service, 200.0, 197.9))

        tight_service = self._offline_service(_worker_poll_interval_seconds=0.75, relay_sync_timeout_seconds=5.0)
        self.assertFalse(UpdateCycleController._offline_confirmed_relay_sample_fresh(tight_service, 200.0, 198.4))
        self.assertTrue(UpdateCycleController._offline_confirmed_relay_sample_fresh(tight_service, 200.0, 200.5))

        class TightFutureTolerance(UpdateCycleController):
            FUTURE_INPUT_TIMESTAMP_TOLERANCE_SECONDS = 0.25

        self.assertTrue(TightFutureTolerance._offline_confirmed_relay_sample_fresh(service, 200.0, 200.25))
        self.assertFalse(TightFutureTolerance._offline_confirmed_relay_sample_fresh(service, 200.0, 200.3))

    def test_fresh_offline_pm_status_requires_numeric_fresh_output_sample(self):
        service = self._offline_service(
            _last_confirmed_pm_status={"output": False, "_phase_selection": "P1_P2"},
            _last_confirmed_pm_status_at=199.0,
        )
        self.assertEqual(UpdateCycleController._fresh_offline_pm_status(service, 200.0), service._last_confirmed_pm_status)

        service._last_confirmed_pm_status = {"power": 0.0}
        self.assertIsNone(UpdateCycleController._fresh_offline_pm_status(service, 200.0))

        service._last_confirmed_pm_status = {"output": True}
        service._last_confirmed_pm_status_at = True
        self.assertIsNone(UpdateCycleController._fresh_offline_pm_status(service, 200.0))

        service._last_confirmed_pm_status_at = 201.1
        self.assertIsNone(UpdateCycleController._fresh_offline_pm_status(service, 200.0))

    def test_offline_relay_state_and_fresh_status_tolerate_missing_cache_attrs(self):
        service = self._offline_service(_worker_poll_interval_seconds=0.75, relay_sync_timeout_seconds=5.0)
        delattr(service, "_last_confirmed_pm_status")
        delattr(service, "_last_confirmed_pm_status_at")

        self.assertFalse(UpdateCycleController._offline_confirmed_relay_state(service, 200.0))
        self.assertIsNone(UpdateCycleController._fresh_offline_pm_status(service, 200.0))

        service._last_confirmed_pm_status = {"output": True}
        service._last_confirmed_pm_status_at = 198.4
        self.assertFalse(UpdateCycleController._offline_confirmed_relay_state(service, 200.0))
        self.assertIsNone(UpdateCycleController._fresh_offline_pm_status(service, 200.0))

    def test_offline_voltage_and_status_state_contracts(self):
        service = self._offline_service(_last_voltage=0.0)
        self.assertEqual(_UpdateCycleOffline._offline_voltage(service), 230.0)

        delattr(service, "_last_voltage")
        self.assertEqual(_UpdateCycleOffline._offline_voltage(service), 230.0)

        service._last_voltage = 229.5
        self.assertEqual(_UpdateCycleOffline._offline_voltage(service), 229.5)

        service._last_charger_fault_active = 1
        _UpdateCycleOffline._mark_offline_status_state(service)
        self.assertEqual(service._last_status_source, "shelly-offline")
        self.assertEqual(service._last_charger_fault_active, 0)

    def test_publish_offline_update_uses_cached_voltage_and_phase_metadata(self):
        service = self._offline_service(
            _last_voltage=241.0,
            _last_confirmed_pm_status={
                "output": True,
                "_phase_selection": "P1_P2",
                "_phase_powers_w": (600.0, 400.0, 0.0),
                "_phase_currents_a": (2.5, 1.5, 0.0),
            },
            _last_confirmed_pm_status_at=199.0,
        )
        controller = self._offline_controller(service)

        self.assertTrue(controller.publish_offline_update(200.0))

        power, voltage, current, phase_data, now = service._publish_live_measurements.call_args.args
        self.assertEqual(power, 0.0)
        self.assertEqual(voltage, 241.0)
        self.assertEqual(current, 4.0)
        self.assertEqual(now, 200.0)
        self.assertEqual(
            phase_data,
            {
                "L1": {"power": 600.0, "voltage": 241.0, "current": 2.5},
                "L2": {"power": 400.0, "voltage": 241.0, "current": 1.5},
                "L3": {"power": 0.0, "voltage": 241.0, "current": 0.0},
            },
        )
        self.assertEqual(service.virtual_startstop, 1)
        service._publish_companion_dbus_bridge.assert_called_once_with(200.0)

    def test_publish_offline_update_uses_phase_calculator_without_metadata(self):
        service = self._offline_service(
            _last_confirmed_pm_status={"output": False},
            _last_confirmed_pm_status_at=199.0,
        )

        def phase_values(power, voltage, phase, voltage_mode):
            return {
                "L1": {"power": float(power), "voltage": float(voltage), "current": 99.0},
                "L2": {"power": 0.0, "voltage": 0.0, "current": float(phase)},
                "L3": {"power": 0.0, "voltage": 0.0, "current": float(voltage_mode)},
            }

        controller = UpdateCycleController(service, phase_values, lambda reason: {"init": 0, "shelly-offline": 11}.get(reason, 99))
        service.phase = 0.0
        service.voltage_mode = 0.0

        self.assertTrue(controller.publish_offline_update(200.0))

        self.assertEqual(
            service._publish_live_measurements.call_args.args[3],
            {
                "L1": {"power": 0.0, "voltage": 230.0, "current": 99.0},
                "L2": {"power": 0.0, "voltage": 0.0, "current": 0.0},
                "L3": {"power": 0.0, "voltage": 0.0, "current": 0.0},
            },
        )

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
