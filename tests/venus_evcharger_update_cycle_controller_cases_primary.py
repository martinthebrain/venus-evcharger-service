# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_update_cycle_controller_support import *
from venus_evcharger.auto.tracking import clear_auto_decision_tracking


class TestUpdateCycleControllerPrimary(UpdateCycleControllerTestBase):
    def test_clear_auto_decision_tracking_reports_whether_state_changed(self):
        service = SimpleNamespace(
            auto_start_condition_since=10.0,
            auto_stop_condition_since=None,
            auto_stop_condition_reason="auto-stop-surplus",
        )

        self.assertTrue(clear_auto_decision_tracking(service))
        self.assertIsNone(service.auto_start_condition_since)
        self.assertIsNone(service.auto_stop_condition_since)
        self.assertIsNone(service.auto_stop_condition_reason)
        self.assertFalse(clear_auto_decision_tracking(service))

    def test_runtime_state_save_best_effort_covers_missing_and_warning_paths(self):
        controller = UpdateCycleController(SimpleNamespace(), _phase_values, lambda reason: 0)
        controller.components.state.save_runtime_state_best_effort("default-role")

        service = SimpleNamespace(
            _save_runtime_state=MagicMock(side_effect=RuntimeError("boom")),
            _warning_throttled=MagicMock(),
            auto_shelly_soft_fail_seconds=7.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: 0)
        controller.components.state.save_runtime_state_best_effort("test")

        service._warning_throttled.assert_called_once()
        self.assertEqual(service._warning_throttled.call_args.args[0], "runtime-state-save-failed-test")

    def test_update_virtual_state_keeps_dbus_publish_alive_when_runtime_save_fails(self):
        service = SimpleNamespace(
            charging_started_at=90.0,
            energy_at_start=1.0,
            phase="L1",
            virtual_startstop=0,
            virtual_enable=1,
            virtual_mode=1,
            _charger_backend=None,
            time_now=MagicMock(return_value=100.0),
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _ensure_observability_state=MagicMock(),
            _publish_energy_time_measurements=MagicMock(return_value=True),
            _publish_config_paths=MagicMock(return_value=False),
            _publish_diagnostic_paths=MagicMock(return_value=False),
            _save_runtime_state=MagicMock(side_effect=RuntimeError("save failed")),
            _warning_throttled=MagicMock(),
            auto_shelly_soft_fail_seconds=5.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertTrue(controller.components.state.update_virtual_state(2, 1.5, True))

        service._publish_energy_time_measurements.assert_called_once()
        service._warning_throttled.assert_called_once()
        self.assertEqual(service.last_status, 2)

    def test_update_state_helpers_cover_freshness_and_startstop_edges(self):
        service = SimpleNamespace(
            _charger_backend=object(),
            _worker_poll_interval_seconds=0.4,
            auto_shelly_soft_fail_seconds=10.0,
            _last_charger_state_enabled=True,
            _last_charger_state_at=None,
            virtual_startstop=0,
            virtual_enable=0,
            virtual_mode=0,
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertEqual(controller.components.readbacks.max_age_seconds(), 1.0)
        self.assertIsNone(controller.components.readbacks.resolve(100.0).charger)

        service._readback_store.replace_charger(
            TimedChargerState(
                ChargerState(enabled=True, current_amps=None, phase_selection=None),
                100.0,
            )
        )
        self.assertEqual(controller.components.state.startstop_display_for_state(service, False, 100.0), 1)

    def test_charger_state_freshness_prefers_strictest_positive_runtime_budget(self):
        service = SimpleNamespace(
            _worker_poll_interval_seconds=3.0,
            auto_shelly_soft_fail_seconds=5.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: 0)
        service._readback_store.replace_charger(
            TimedChargerState(ChargerState(None, None, None), 100.0)
        )

        self.assertEqual(controller.components.readbacks.max_age_seconds(), 2.0)
        self.assertIsNotNone(controller.components.readbacks.resolve(102.0).charger)
        self.assertIsNone(controller.components.readbacks.resolve(102.001).charger)
        self.assertIsNotNone(controller.components.readbacks.resolve(98.0).charger)
        self.assertIsNone(controller.components.readbacks.resolve(97.999).charger)

        service._worker_poll_interval_seconds = 0.25
        service.auto_shelly_soft_fail_seconds = 0.75
        self.assertEqual(controller.components.readbacks.max_age_seconds(), 1.0)

        service._worker_poll_interval_seconds = 4.0
        service.auto_shelly_soft_fail_seconds = 1.5
        self.assertEqual(controller.components.readbacks.max_age_seconds(), 1.5)

        service._worker_poll_interval_seconds = float("nan")
        service.auto_shelly_soft_fail_seconds = 0.5
        self.assertEqual(controller.components.readbacks.max_age_seconds(), 1.0)

        service._worker_poll_interval_seconds = 0.0
        service.auto_shelly_soft_fail_seconds = 0.0
        self.assertEqual(controller.components.readbacks.max_age_seconds(), 2.0)

    def test_charger_enabled_readback_requires_backend_value_and_fresh_timestamp(self):
        service = SimpleNamespace(
            _charger_backend=None,
            _worker_poll_interval_seconds=1.0,
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: 0)

        self.assertIsNone(controller.components.readbacks.resolve(100.0).charger)
        service._readback_store.replace_charger(
            TimedChargerState(ChargerState(None, None, None), 100.0)
        )
        readback = controller.components.readbacks.resolve(100.0).charger
        self.assertIsNotNone(readback)
        self.assertIsNone(readback.state.enabled)
        service._readback_store.replace_charger(
            TimedChargerState(ChargerState(False, None, None), 100.0)
        )
        self.assertFalse(controller.components.readbacks.resolve(100.0).charger.state.enabled)
        service._readback_store.replace_charger(
            TimedChargerState(ChargerState(True, None, None), 120.1)
        )
        self.assertIsNone(controller.components.readbacks.resolve(100.0).charger)

    def test_startstop_display_contract_for_native_auto_and_manual_modes(self):
        service = SimpleNamespace(
            _charger_backend=object(),
            _last_charger_state_enabled=False,
            _last_charger_state_at=100.0,
            auto_shelly_soft_fail_seconds=10.0,
            virtual_startstop=1,
            virtual_enable=1,
            virtual_mode=1,
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: 0)

        self.assertEqual(controller.components.state.startstop_display_for_state(service, True, 100.0), 0)
        self.assertEqual(service.virtual_startstop, 1)

        service._charger_backend = None
        service._readback_store.replace_charger(None)
        self.assertEqual(controller.components.state.startstop_display_for_state(service, False, 100.0), 1)
        self.assertEqual(service.virtual_startstop, 0)

        service.virtual_mode = 0
        service.virtual_enable = 1
        service.virtual_startstop = 1
        self.assertEqual(controller.components.state.startstop_display_for_state(service, False, 100.0), 0)

    def test_startup_manual_target_contract_covers_missing_none_and_bool_coercion(self):
        controller = UpdateCycleController(SimpleNamespace(), _phase_values, lambda reason: 0)
        service = SimpleNamespace()

        self.assertIsNone(controller.components.state._startup_manual_target(service))
        self.assertTrue(hasattr(service, "_startup_manual_target"))

        service._startup_manual_target = None
        self.assertIsNone(controller.components.state._startup_manual_target(service))

        service._startup_manual_target = 0
        self.assertFalse(controller.components.state._startup_manual_target(service))

        service._startup_manual_target = "on"
        self.assertTrue(controller.components.state._startup_manual_target(service))

    def test_startup_manual_target_skips_auto_modes_and_clears_when_already_matched(self):
        service = SimpleNamespace(
            _startup_manual_target=True,
            virtual_mode=1,
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: 0)
        pm_status = {"output": False, "apower": 12.0}

        self.assertIs(controller.components.state.apply_startup_manual_target(pm_status, 100.0), pm_status)
        self.assertTrue(service._startup_manual_target)

        service.virtual_mode = 0
        service._startup_manual_target = False
        pm_status = {}
        self.assertIs(controller.components.state.apply_startup_manual_target(pm_status, 99.0), pm_status)
        self.assertIsNone(service._startup_manual_target)

        service._startup_manual_target = True
        pm_status = {"output": True, "apower": 12.0}
        self.assertIs(controller.components.state.apply_startup_manual_target(pm_status, 100.0), pm_status)
        self.assertIsNone(service._startup_manual_target)

    def test_startup_manual_target_applies_and_publishes_placeholder_or_fallback(self):
        service = SimpleNamespace(
            _startup_manual_target=True,
            virtual_mode=0,
            auto_shelly_soft_fail_seconds=10.0,
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _publish_local_pm_status=MagicMock(return_value={"output": True, "apower": 4.0}),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: 0)
        controller.components.relay.foundation.targets.apply_enabled_target = MagicMock(return_value=True)

        published = controller.components.state.apply_startup_manual_target({"output": False, "apower": 12.0}, 100.0)

        self.assertEqual(published, {"output": True, "apower": 4.0})
        self.assertIsNone(service._startup_manual_target)
        controller.components.relay.foundation.targets.apply_enabled_target.assert_called_once_with(service, True, 100.0)
        service._publish_local_pm_status.assert_called_once_with(True, 100.0)

        service._startup_manual_target = False
        service._publish_local_pm_status = MagicMock(return_value=None)
        controller.components.relay.foundation.targets.apply_enabled_target = MagicMock(return_value=True)

        fallback = controller.components.state.apply_startup_manual_target({"output": True, "apower": 12.0, "current": 3.0}, 101.0)

        self.assertEqual(fallback, {"output": False, "apower": 0.0, "current": 0.0})
        self.assertIsNone(service._startup_manual_target)

    def test_startup_manual_target_keeps_live_status_on_apply_failure_or_deferred_apply(self):
        service = SimpleNamespace(
            _startup_manual_target=False,
            virtual_mode=0,
            auto_shelly_soft_fail_seconds=8.0,
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: 0)
        controller.components.relay.foundation.health._enable_control_source_key = MagicMock(return_value="relay")
        controller.components.relay.foundation.health._enable_control_label = MagicMock(return_value="relay")
        controller.components.relay.foundation.targets.apply_enabled_target = MagicMock(side_effect=RuntimeError("offline"))
        pm_status = {"output": True, "apower": 12.0}

        self.assertIs(controller.components.state.apply_startup_manual_target(pm_status, 100.0), pm_status)
        self.assertFalse(service._startup_manual_target)
        service._mark_failure.assert_called_once_with("relay")
        service._warning_throttled.assert_called_once()

        service._mark_failure.reset_mock()
        service._warning_throttled.reset_mock()
        controller.components.relay.foundation.targets.apply_enabled_target = MagicMock(return_value=False)

        self.assertIs(controller.components.state.apply_startup_manual_target(pm_status, 101.0), pm_status)
        self.assertFalse(service._startup_manual_target)
        service._mark_failure.assert_not_called()
        service._warning_throttled.assert_not_called()

    def test_startup_manual_placeholder_warns_and_falls_back_when_publish_fails(self):
        service = SimpleNamespace(
            auto_shelly_soft_fail_seconds=6.0,
            _publish_local_pm_status=MagicMock(side_effect=RuntimeError("publish failed")),
            _warning_throttled=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: 0)

        fallback = controller.components.state._publish_startup_local_pm_status(
            {"output": False, "apower": 99.0, "current": 11.0},
            True,
            100.0,
        )

        self.assertEqual(fallback, {"output": True, "apower": 0.0, "current": 0.0})
        service._warning_throttled.assert_called_once()
        placeholder_args = service._warning_throttled.call_args.args
        placeholder_kwargs = service._warning_throttled.call_args.kwargs
        self.assertEqual(service._warning_throttled.call_args.args[0], "startup-manual-target-placeholder-failed")
        self.assertEqual(service._warning_throttled.call_args.args[1], 6.0)
        self.assertEqual(
            service._warning_throttled.call_args.args[2],
            "Failed to publish startup manual placeholder state %s: %s",
        )
        self.assertIs(placeholder_args[3], True)
        self.assertIs(placeholder_args[4], placeholder_kwargs["exc_info"])

    def test_startup_manual_apply_failure_warning_contract_is_explicit(self):
        service = SimpleNamespace(
            _startup_manual_target=True,
            virtual_mode=0,
            auto_shelly_soft_fail_seconds=9.0,
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: 0)
        controller.components.relay.foundation.health._enable_control_source_key = MagicMock(
            side_effect=lambda candidate: "native-charger" if candidate is service else "wrong-service"
        )
        controller.components.relay.foundation.health._enable_control_label = MagicMock(
            side_effect=lambda candidate: "native charger" if candidate is service else "wrong-service"
        )
        controller.components.relay.foundation.targets.apply_enabled_target = MagicMock(side_effect=ValueError("offline"))

        pm_status = {"output": False, "apower": 12.0}
        self.assertIs(controller.components.state.apply_startup_manual_target(pm_status, 100.0), pm_status)

        service._mark_failure.assert_called_once_with("native-charger")
        self.assertEqual(service._warning_throttled.call_args.args[0], "startup-manual-target-failed")
        self.assertEqual(service._warning_throttled.call_args.args[1], 9.0)
        self.assertEqual(
            service._warning_throttled.call_args.args[2],
            "Failed to apply startup manual %s state %s: %s",
        )
        self.assertEqual(service._warning_throttled.call_args.args[3:6], ("native charger", True, service._warning_throttled.call_args.kwargs["exc_info"]))

    def test_session_state_contract_covers_start_delta_clamp_and_tracking_clear(self):
        controller = UpdateCycleController(SimpleNamespace(), _phase_values, lambda reason: 0)
        service = SimpleNamespace(charging_started_at=None, energy_at_start=9.0)

        self.assertEqual(controller.components.state.session_state_from_status(service, 2, 10.0, False, 100.0), (0, 0.0))
        self.assertEqual(service.charging_started_at, 100.0)
        self.assertEqual(service.energy_at_start, 10.0)

        self.assertEqual(controller.components.state.session_state_from_status(service, 2, 10.75, False, 112.9), (12, 0.75))
        self.assertEqual(controller.components.state.session_state_from_status(service, 2, 9.5, False, 113.0), (13, 0.0))

        service.auto_start_condition_since = 1.0
        service.auto_stop_condition_since = 2.0
        service.auto_stop_condition_reason = "auto-stop-surplus"
        self.assertEqual(controller.components.state.session_state_from_status(service, 1, 11.0, True, 114.0), (0, 0.0))
        self.assertIsNone(service.charging_started_at)
        self.assertEqual(service.energy_at_start, 11.0)
        self.assertIsNone(service.auto_start_condition_since)
        self.assertIsNone(service.auto_stop_condition_since)
        self.assertIsNone(service.auto_stop_condition_reason)
        self.assertEqual(controller.components.state._session_energy(10.23456, 9.0), 1.235)

    def test_session_state_does_not_clear_tracking_when_relay_already_off(self):
        controller = UpdateCycleController(SimpleNamespace(), _phase_values, lambda reason: 0)
        service = SimpleNamespace(
            charging_started_at=90.0,
            energy_at_start=1.0,
            auto_start_condition_since=1.0,
            auto_stop_condition_since=2.0,
            auto_stop_condition_reason="auto-stop-surplus",
        )

        self.assertEqual(controller.components.state.session_state_from_status(service, 0, 2.0, False, 100.0), (0, 0.0))
        self.assertIsNone(service.charging_started_at)
        self.assertEqual(service.energy_at_start, 2.0)
        self.assertEqual(service.auto_start_condition_since, 1.0)
        self.assertEqual(service.auto_stop_condition_since, 2.0)
        self.assertEqual(service.auto_stop_condition_reason, "auto-stop-surplus")
        self.assertFalse(controller.components.state._session_was_active(SimpleNamespace()))

    def test_virtual_state_defaults_initializes_missing_health_and_preserves_existing(self):
        service = SimpleNamespace(_ensure_observability_state=MagicMock())
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 4, "running": 7}[reason])

        controller.components.state.ensure_virtual_state_defaults()

        service._ensure_observability_state.assert_called_once()
        self.assertEqual(service._last_health_reason, "init")
        self.assertEqual(service._last_health_code, 4)

        service._ensure_observability_state.reset_mock()
        service._last_health_reason = "running"
        service._last_health_code = 99
        controller.components.state.ensure_virtual_state_defaults()

        service._ensure_observability_state.assert_called_once()
        self.assertEqual(service._last_health_reason, "running")
        self.assertEqual(service._last_health_code, 99)

    def test_save_runtime_state_best_effort_warns_with_full_context_and_ignores_uncallable(self):
        controller = UpdateCycleController(SimpleNamespace(_save_runtime_state=object()), _phase_values, lambda reason: 0)
        controller.components.state.save_runtime_state_best_effort("uncallable")

        service = SimpleNamespace(
            _save_runtime_state=MagicMock(side_effect=OSError("disk")),
            _warning_throttled=MagicMock(),
            auto_shelly_soft_fail_seconds=12.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: 0)

        controller.components.state.save_runtime_state_best_effort("state-contract")

        service._save_runtime_state.assert_called_once_with()
        self.assertEqual(service._warning_throttled.call_args.args[0], "runtime-state-save-failed-state-contract")
        self.assertEqual(service._warning_throttled.call_args.args[1], 12.0)
        self.assertEqual(
            service._warning_throttled.call_args.args[2],
            "Unable to save runtime state during %s update: %s",
        )
        self.assertEqual(service._warning_throttled.call_args.args[3], "state-contract")
        self.assertIs(service._warning_throttled.call_args.args[4], service._warning_throttled.call_args.kwargs["exc_info"])

        service_without_delay = SimpleNamespace(
            _save_runtime_state=MagicMock(side_effect=RuntimeError("disk")),
            _warning_throttled=MagicMock(),
        )
        controller = UpdateCycleController(service_without_delay, _phase_values, lambda reason: 0)
        controller.components.state.save_runtime_state_best_effort("default-delay")
        self.assertEqual(service_without_delay._warning_throttled.call_args.args[1], 10.0)

    def test_phase_energy_contract_covers_all_single_phase_and_three_phase_modes(self):
        controller = UpdateCycleController(SimpleNamespace(), _phase_values, lambda reason: 0)

        self.assertEqual(
            controller.components.state.phase_energies_for_total(SimpleNamespace(phase="L1"), 9.0),
            {"L1": 9.0, "L2": 0.0, "L3": 0.0},
        )
        self.assertEqual(
            controller.components.state.phase_energies_for_total(SimpleNamespace(phase="L2"), 9.0),
            {"L1": 0.0, "L2": 9.0, "L3": 0.0},
        )
        self.assertEqual(
            controller.components.state.phase_energies_for_total(SimpleNamespace(phase="L3"), 9.0),
            {"L1": 0.0, "L2": 0.0, "L3": 9.0},
        )
        self.assertEqual(
            controller.components.state.phase_energies_for_total(SimpleNamespace(phase="3P"), 9.0),
            {"L1": 3.0, "L2": 3.0, "L3": 3.0},
        )
        self.assertEqual(
            controller.components.state.phase_energies_for_total(SimpleNamespace(phase="unknown"), 9.0),
            {"L1": 0.0, "L2": 0.0, "L3": 0.0},
        )
        self.assertEqual(
            controller.components.state.phase_energies_for_total(SimpleNamespace(), 9.0),
            {"L1": 9.0, "L2": 0.0, "L3": 0.0},
        )

    def test_total_phase_current_sums_all_declared_phases_only(self):
        controller = UpdateCycleController(SimpleNamespace(), _phase_values, lambda reason: 0)

        self.assertEqual(
            controller.components.state._total_phase_current(
                {
                    "L1": {"current": 1.5},
                    "L2": {"current": 2.5},
                    "L3": {"current": 3.5},
                    "extra": {"current": 99.0},
                }
            ),
            7.5,
        )

    def test_publish_virtual_state_paths_contract_uses_session_energy_for_total_and_phases(self):
        service = SimpleNamespace(
            phase="L2",
            _maintain_evcs_registration=MagicMock(return_value=True),
            _publish_energy_time_measurements=MagicMock(return_value=True),
            _publish_config_paths=MagicMock(return_value=True),
            _publish_diagnostic_paths=MagicMock(return_value=False),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: 0)

        self.assertTrue(controller.components.state.publish_virtual_state_paths(123.0, 45, 6.0, 1, 100.0))
        service._maintain_evcs_registration.assert_called_once_with()
        service._publish_energy_time_measurements.assert_called_once_with(
            6.0,
            {"L1": 0.0, "L2": 6.0, "L3": 0.0},
            45,
            6.0,
            100.0,
        )
        service._publish_config_paths.assert_called_once_with(1, 100.0)
        service._publish_diagnostic_paths.assert_called_once_with(100.0)

    def test_update_virtual_state_normalizes_inputs_and_records_status(self):
        service = SimpleNamespace(
            charging_started_at=None,
            energy_at_start=0.0,
            phase="L1",
            virtual_startstop=0,
            virtual_enable=0,
            virtual_mode=0,
            _charger_backend=None,
            time_now=MagicMock(return_value=100.0),
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _ensure_observability_state=MagicMock(),
            _publish_energy_time_measurements=MagicMock(return_value=False),
            _publish_config_paths=MagicMock(return_value=False),
            _publish_diagnostic_paths=MagicMock(return_value=True),
            _save_runtime_state=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertTrue(controller.components.state.update_virtual_state("2", "7.25", 1))
        self.assertEqual(service.last_status, 2)
        self.assertEqual(service.charging_started_at, 100.0)
        self.assertEqual(service.energy_at_start, 7.25)
        service._save_runtime_state.assert_called_once()

    def test_update_virtual_state_delegates_with_normalized_values_and_reason_contract(self):
        service = SimpleNamespace(time_now=MagicMock(return_value=100.0))
        controller = UpdateCycleController(service, _phase_values, lambda reason: 0)
        controller.components.state.ensure_virtual_state_defaults = MagicMock()
        controller.components.state.session_state_from_status = MagicMock(return_value=(7, 2.5))
        controller.components.state.startstop_display_for_state = MagicMock(return_value=1)
        controller.components.state.publish_virtual_state_paths = MagicMock(return_value=False)
        controller.components.state.save_runtime_state_best_effort = MagicMock()

        self.assertFalse(controller.components.state.update_virtual_state("2", "7.25", 1))

        controller.components.state.ensure_virtual_state_defaults.assert_called_once_with()
        controller.components.state.session_state_from_status.assert_called_once_with(service, 2, 7.25, True, 100.0)
        controller.components.state.startstop_display_for_state.assert_called_once_with(service, True, 100.0)
        controller.components.state.publish_virtual_state_paths.assert_called_once_with(7.25, 7, 2.5, 1, 100.0)
        controller.components.state.save_runtime_state_best_effort.assert_called_once_with("virtual-state")
        self.assertEqual(service.last_status, 2)

    def test_prepare_update_cycle_starts_io_worker_only_for_configured_topology(self):
        service = SimpleNamespace(
            topology_configured=True,
            host_configured=False,
            _start_io_worker=MagicMock(),
            _watchdog_recover=MagicMock(),
            _ensure_auto_input_helper_process=MagicMock(),
            _refresh_auto_input_snapshot=MagicMock(),
            _get_worker_snapshot=MagicMock(return_value={"pm_status": {"output": True}}),
        )
        install_update_cycle_roles(service)

        self.assertEqual(
            UpdateStateController.prepare_update_cycle(service, 100.0),
            {"pm_status": {"output": True}},
        )
        service._start_io_worker.assert_called_once_with()
        service._watchdog_recover.assert_called_once_with(100.0)
        service._ensure_auto_input_helper_process.assert_called_once_with()
        service._refresh_auto_input_snapshot.assert_called_once_with()
        service._get_worker_snapshot.assert_called_once_with()

        service = SimpleNamespace(
            topology_configured=False,
            host_configured=False,
            _start_io_worker=MagicMock(),
            _watchdog_recover=MagicMock(),
            _ensure_auto_input_helper_process=MagicMock(),
            _refresh_auto_input_snapshot=MagicMock(),
            _get_worker_snapshot=MagicMock(return_value={}),
        )
        install_update_cycle_roles(service)

        self.assertEqual(UpdateStateController.prepare_update_cycle(service, 101.0), {})
        service._start_io_worker.assert_not_called()
        service._watchdog_recover.assert_called_once_with(101.0)

        service_without_config_flags = SimpleNamespace(
            _start_io_worker=MagicMock(),
            _watchdog_recover=MagicMock(),
            _ensure_auto_input_helper_process=MagicMock(),
            _refresh_auto_input_snapshot=MagicMock(),
            _get_worker_snapshot=MagicMock(return_value={}),
        )
        install_update_cycle_roles(service_without_config_flags)
        self.assertEqual(UpdateStateController.prepare_update_cycle(service_without_config_flags, 101.5), {})
        service_without_config_flags._start_io_worker.assert_not_called()

    def test_prepare_update_cycle_uses_legacy_host_configured_when_topology_flag_is_absent(self):
        service = SimpleNamespace(
            _host_configured=True,
            _start_io_worker=MagicMock(),
            _watchdog_recover=MagicMock(),
            _ensure_auto_input_helper_process=MagicMock(),
            _refresh_auto_input_snapshot=MagicMock(),
            _get_worker_snapshot=MagicMock(return_value={"legacy": True}),
        )
        install_update_cycle_roles(service)

        self.assertEqual(UpdateStateController.prepare_update_cycle(service, 102.0), {"legacy": True})
        service._start_io_worker.assert_called_once_with()

    def test_auto_phase_selection_tracks_candidate_before_staged_upshift(self):
        service = _auto_phase_service(
            _last_confirmed_pm_status={"output": True},
            _last_confirmed_pm_status_at=99.5,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        override = controller.components.relay.foundation.auto_phase.maybe_apply_auto_phase_selection(
            service,
            True,
            True,
            230.0,
            100.0,
            True,
        )

        self.assertIsNone(override)
        self.assertEqual(service._auto_phase_target_candidate, "P1_P2")
        self.assertEqual(service._auto_phase_target_since, 100.0)
        self.assertIsNone(service._phase_switch_pending_selection)
        self.assertEqual(service._last_auto_metrics["phase_reason"], "phase-upshift-pending")

    def test_auto_phase_selection_stages_upshift_after_delay_when_relay_is_on(self):
        service = _auto_phase_service(
            _last_confirmed_pm_status={"output": True},
            _last_confirmed_pm_status_at=99.5,
            _auto_phase_target_candidate="P1_P2",
            _auto_phase_target_since=80.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        override = controller.components.relay.foundation.auto_phase.maybe_apply_auto_phase_selection(
            service,
            True,
            True,
            230.0,
            100.0,
            True,
        )

        self.assertFalse(override)
        self.assertEqual(service.requested_phase_selection, "P1_P2")
        self.assertEqual(service._phase_switch_pending_selection, "P1_P2")
        self.assertEqual(service._phase_switch_state, "waiting-relay-off")
        self.assertTrue(service._phase_switch_resume_relay)
        service._save_runtime_state.assert_called_once()
        service._publish_local_pm_status.assert_called_once_with(False, 100.0)

    def test_auto_phase_selection_blocks_repeated_upshift_after_confirmed_mismatch(self):
        service = _auto_phase_service(
            _last_confirmed_pm_status={"output": True},
            _last_confirmed_pm_status_at=99.5,
            _phase_switch_last_mismatch_selection="P1_P2",
            _phase_switch_last_mismatch_at=95.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        override = controller.components.relay.foundation.auto_phase.maybe_apply_auto_phase_selection(
            service,
            True,
            True,
            230.0,
            100.0,
            True,
        )

        self.assertIsNone(override)
        self.assertIsNone(service._auto_phase_target_candidate)
        self.assertIsNone(service._phase_switch_pending_selection)
        self.assertEqual(service._last_auto_metrics["phase_reason"], "phase-upshift-blocked-mismatch")

    def test_auto_phase_selection_retries_upshift_after_mismatch_cooldown_expires(self):
        service = _auto_phase_service(
            _last_confirmed_pm_status={"output": True},
            _last_confirmed_pm_status_at=99.5,
            _phase_switch_last_mismatch_selection="P1_P2",
            _phase_switch_last_mismatch_at=10.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        override = controller.components.relay.foundation.auto_phase.maybe_apply_auto_phase_selection(
            service,
            True,
            True,
            230.0,
            100.0,
            True,
        )

        self.assertIsNone(override)
        self.assertEqual(service._auto_phase_target_candidate, "P1_P2")
        self.assertEqual(service._last_auto_metrics["phase_reason"], "phase-upshift-pending")

    def test_auto_phase_selection_blocks_upshift_while_phase_lockout_is_active(self):
        service = _auto_phase_service(
            _last_confirmed_pm_status={"output": True},
            _last_confirmed_pm_status_at=99.5,
            _phase_switch_lockout_selection="P1_P2",
            _phase_switch_lockout_reason="mismatch-threshold",
            _phase_switch_lockout_at=95.0,
            _phase_switch_lockout_until=160.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        override = controller.components.relay.foundation.auto_phase.maybe_apply_auto_phase_selection(
            service,
            True,
            True,
            230.0,
            100.0,
            True,
        )

        self.assertIsNone(override)
        self.assertIsNone(service._auto_phase_target_candidate)
        self.assertEqual(service._last_auto_metrics["phase_reason"], "phase-upshift-blocked-lockout")

    def test_auto_phase_selection_applies_lowest_phase_while_idle_after_delay(self):
        service = _auto_phase_service(
            requested_phase_selection="P1_P2",
            active_phase_selection="P1_P2",
            _last_auto_metrics={"surplus": 400.0},
            _last_confirmed_pm_status={"output": False},
            _last_confirmed_pm_status_at=99.5,
            _auto_phase_target_candidate="P1",
            _auto_phase_target_since=90.0,
            _apply_phase_selection=MagicMock(return_value="P1"),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        override = controller.components.relay.foundation.auto_phase.maybe_apply_auto_phase_selection(
            service,
            False,
            False,
            230.0,
            100.0,
            True,
        )

        self.assertIsNone(override)
        service._apply_phase_selection.assert_called_once_with("P1")
        self.assertEqual(service.requested_phase_selection, "P1")
        self.assertEqual(service.active_phase_selection, "P1")
        self.assertIsNone(service._phase_switch_pending_selection)
        service._save_runtime_state.assert_called_once()

    def test_auto_phase_selection_keeps_unrelated_lockout_after_successful_apply(self):
        service = _auto_phase_service(
            requested_phase_selection="P1_P2",
            active_phase_selection="P1_P2",
            _last_auto_metrics={"surplus": 400.0},
            _last_confirmed_pm_status={"output": False},
            _last_confirmed_pm_status_at=99.5,
            _auto_phase_target_candidate="P1",
            _auto_phase_target_since=90.0,
            _apply_phase_selection=MagicMock(return_value="P1"),
            _phase_switch_lockout_selection="P1_P2",
            _phase_switch_lockout_reason="mismatch-threshold",
            _phase_switch_lockout_at=80.0,
            _phase_switch_lockout_until=140.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        override = controller.components.relay.foundation.auto_phase.maybe_apply_auto_phase_selection(
            service,
            False,
            False,
            230.0,
            100.0,
            True,
        )

        self.assertIsNone(override)
        service._apply_phase_selection.assert_called_once_with("P1")
        self.assertEqual(service.requested_phase_selection, "P1")
        self.assertEqual(service.active_phase_selection, "P1")
        self.assertEqual(service._phase_switch_lockout_selection, "P1_P2")
        self.assertEqual(service._phase_switch_lockout_until, 140.0)

    def test_auto_phase_helper_edges_cover_fallbacks_thresholds_and_lockouts(self):
        service = _auto_phase_service(
            requested_phase_selection="P1_P2_P3",
            active_phase_selection="P1_P2",
            _last_auto_metrics="bad",
            auto_policy=_phase_policy(
                enabled=False,
                mismatch_retry_seconds=-1.0,
                mismatch_lockout_count=-2,
                mismatch_lockout_seconds=-3.0,
            ),
            min_current=None,
            _phase_switch_last_mismatch_selection=None,
            _phase_switch_last_mismatch_at=None,
            _phase_switch_mismatch_counts={"P1_P2": 2},
            _phase_switch_lockout_selection="P1_P2",
            _phase_switch_lockout_reason="mismatch-threshold",
            _phase_switch_lockout_at=80.0,
            _phase_switch_lockout_until=90.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertEqual(controller.components.relay.foundation.selector._current_phase_selection(service, ("P1", "P1_P2")), "P1_P2")
        self.assertIs(controller.components.relay.foundation.mismatch._auto_phase_policy(service), service.auto_policy.phase)
        self.assertIsNone(controller.components.relay.foundation.selector._auto_phase_metric_surplus_watts(service))
        self.assertIsNone(controller.components.relay.foundation.selector._phase_selection_min_surplus_watts(service, "P1", 230.0))
        self.assertEqual(
            controller.components.relay.foundation.selector._auto_phase_policy_state(service, ("P1",)),
            (None, "phase-policy-disabled", None),
        )

        service.auto_policy.phase.enabled = True
        self.assertEqual(
            controller.components.relay.foundation.selector._auto_phase_policy_state(service, ("P1",)),
            (None, "single-phase-only", None),
        )
        self.assertEqual(
            controller.components.relay.foundation.selector._idle_auto_phase_target(service.auto_policy.phase, ("P1",), "P1", False, False),
            (None, "idle-hold-phase", None),
        )
        self.assertEqual(
            controller.components.relay.foundation.selector._surplus_auto_phase_target(service, service.auto_policy.phase, ("P1", "P1_P2"), "P1", 230.0, 100.0),
            (None, "phase-surplus-missing", None),
        )

        service._last_auto_metrics = {"surplus": 100.0}
        self.assertEqual(
            controller.components.relay.foundation.selector._surplus_auto_phase_target(service, service.auto_policy.phase, ("P1", "P1_P2"), "P1", 230.0, 100.0),
            (None, "phase-hold", None),
        )
        self.assertIsNone(
            controller.components.relay.foundation.selector._upshift_auto_phase_target(
                service,
                service.auto_policy.phase,
                ("P1", "P1_P2"),
                1,
                "P1_P2",
                1000.0,
                230.0,
                100.0,
            )
        )
        self.assertIsNone(
            controller.components.relay.foundation.selector._upshift_auto_phase_target(
                service,
                service.auto_policy.phase,
                ("P1", "P1_P2"),
                0,
                "P1",
                1000.0,
                230.0,
                100.0,
            )
        )
        service.min_current = 6.0
        self.assertIsNone(
            controller.components.relay.foundation.selector._upshift_auto_phase_target(
                service,
                service.auto_policy.phase,
                ("P1", "P1_P2"),
                0,
                "P1",
                100.0,
                230.0,
                100.0,
            )
        )

        self.assertFalse(controller.components.relay.foundation.mismatch._phase_switch_mismatch_retry_active(service, "P1_P2", "P1", 100.0))
        service._phase_switch_last_mismatch_selection = "P1"
        self.assertFalse(controller.components.relay.foundation.mismatch._phase_switch_mismatch_retry_active(service, "P1", "P1_P2", 100.0))
        service._phase_switch_last_mismatch_selection = "P1_P2"
        self.assertFalse(controller.components.relay.foundation.mismatch._phase_switch_mismatch_retry_active(service, "P1", "P1_P2", 100.0))
        service._phase_switch_last_mismatch_at = 90.0
        self.assertFalse(controller.components.relay.foundation.mismatch._phase_switch_mismatch_retry_active(service, "P1", "P1_P2", 100.0))
        service.auto_policy.phase.mismatch_retry_seconds = 20.0
        self.assertTrue(controller.components.relay.foundation.mismatch._phase_switch_mismatch_retry_active(service, "P1", "P1_P2", 100.0))
        self.assertEqual(controller.components.relay.foundation.mismatch._phase_switch_lockout_threshold(service), 0)
        self.assertEqual(controller.components.relay.foundation.mismatch._phase_switch_lockout_seconds(service), 0.0)

        controller.components.relay.foundation.mismatch._clear_phase_switch_mismatch_tracking(service)
        self.assertEqual(service._phase_switch_mismatch_counts, {})
        self.assertIsNone(service._phase_switch_last_mismatch_selection)
        self.assertIsNone(service._phase_switch_last_mismatch_at)
        service._phase_switch_mismatch_counts = {"P1_P2": 1}
        service._phase_switch_last_mismatch_selection = "P1_P2"
        service._phase_switch_last_mismatch_at = 95.0
        controller.components.relay.foundation.mismatch._clear_phase_switch_mismatch_tracking(service, "P1_P2")
        self.assertIsNone(service._phase_switch_last_mismatch_selection)
        self.assertIsNone(service._phase_switch_last_mismatch_at)

        controller.components.relay.foundation.mismatch._engage_phase_switch_lockout(service, "P1_P2", 100.0)
        self.assertIsNone(service._phase_switch_lockout_selection)
        service.auto_policy.phase.mismatch_lockout_seconds = 30.0
        controller.components.relay.foundation.mismatch._engage_phase_switch_lockout(service, "P1_P2", 100.0)
        self.assertTrue(controller.components.relay.foundation.mismatch._phase_switch_lockout_active(service, 110.0, "P1_P2"))
        self.assertFalse(controller.components.relay.foundation.mismatch._phase_switch_lockout_active(service, 131.0, "P1_P2"))

        self.assertEqual(controller.components.relay.foundation.mismatch._phase_switch_fallback_selection(service, "P1", "P1_P2"), "P1")
        self.assertEqual(
            controller.components.relay.foundation.mismatch._phase_switch_fallback_selection(
                service, None, "P1_P2"
            ),
            "P1_P2",
        )

        self.assertIsNone(
            controller.components.relay.foundation.selector._downshift_auto_phase_target(
                service,
                service.auto_policy.phase,
                ("P1", "P1_P2"),
                "P1",
                0,
                10.0,
                230.0,
            )
        )
        service.min_current = None
        self.assertIsNone(
            controller.components.relay.foundation.selector._downshift_auto_phase_target(
                service,
                service.auto_policy.phase,
                ("P1", "P1_P2"),
                "P1_P2",
                1,
                10.0,
                230.0,
            )
        )
        service.min_current = 6.0
        self.assertIsNone(
            controller.components.relay.foundation.selector._downshift_auto_phase_target(
                service,
                service.auto_policy.phase,
                ("P1", "P1_P2"),
                "P1_P2",
                1,
                5000.0,
                230.0,
            )
        )
        self.assertEqual(
            controller.components.relay.foundation.selector._downshift_auto_phase_target(
                service,
                service.auto_policy.phase,
                ("P1", "P1_P2"),
                "P1_P2",
                1,
                100.0,
                230.0,
            ),
            ("P1", "phase-downshift", 2610.0),
        )
