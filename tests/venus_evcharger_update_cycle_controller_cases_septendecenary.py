# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_update_cycle_controller_support import *


class TestUpdateCycleControllerSeptendecenary(UpdateCycleControllerTestBase):
    def test_phase_voltage_and_normalization_contracts(self):
        self.assertEqual(UpdateCycleController._normalized_phase_selection(None), "")
        self.assertEqual(UpdateCycleController._normalized_phase_selection(" p1_p2_p3 "), "P1_P2_P3")
        self.assertEqual(UpdateCycleController._normalized_voltage_mode(None), "phase")
        self.assertEqual(UpdateCycleController._normalized_voltage_mode(" LINE "), "line")
        self.assertFalse(UpdateCycleController._selection_uses_line_to_line_voltage("P1", "line"))
        self.assertFalse(UpdateCycleController._selection_uses_line_to_line_voltage("P1_P2_P3", "phase"))
        self.assertTrue(UpdateCycleController._selection_uses_line_to_line_voltage("P1_P2_P3", "line"))

        self.assertEqual(UpdateCycleController._phase_voltage(230.0, "P1", "line"), 230.0)
        self.assertAlmostEqual(UpdateCycleController._phase_voltage(0.5, "P1_P2_P3", "line"), 0.5 / math.sqrt(3.0))
        self.assertAlmostEqual(UpdateCycleController._phase_voltage(400.0, "P1_P2_P3", "line"), 400.0 / math.sqrt(3.0))
        self.assertEqual(UpdateCycleController._phase_voltage(0.0, "P1_P2_P3", "line"), 0.0)
        self.assertEqual(UpdateCycleController._phase_voltage(-10.0, "P1_P2_P3", "line"), 0.0)

    def test_phase_data_for_pm_status_prefers_backend_metadata_and_falls_back_to_phase_delegate(self):
        service = SimpleNamespace(phase="L1", voltage_mode="phase")
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        controller._phase_data_from_backend_metadata = MagicMock(return_value={"L1": {"power": 1.0}})
        controller._phase_values = MagicMock()

        self.assertEqual(controller._phase_data_for_pm_status({"pm": True}, 1200.0, 230.0), {"L1": {"power": 1.0}})
        controller._phase_data_from_backend_metadata.assert_called_once_with({"pm": True}, 230.0, "phase")
        controller._phase_values.assert_not_called()

        controller._phase_data_from_backend_metadata.return_value = None
        controller._phase_values = MagicMock(return_value={"L1": {"power": 1200, "voltage": 230, "current": 5}})
        self.assertEqual(
            controller._phase_data_for_pm_status(None, 1200.0, 230.0),
            {"L1": {"power": 1200.0, "voltage": 230.0, "current": 5.0}},
        )
        controller._phase_values.assert_called_once_with(1200.0, 230.0, "L1", "phase")

    def test_phase_data_for_pm_status_uses_default_voltage_mode_for_metadata_only(self):
        service = SimpleNamespace(phase="P1_P2", voltage_mode="line")
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        controller._phase_data_from_backend_metadata = MagicMock(return_value=None)
        controller._phase_values = MagicMock(return_value={"L1": {"power": 10, "voltage": 11, "current": 12}})

        self.assertEqual(
            controller._phase_data_for_pm_status({"pm": True}, 10.0, 11.0),
            {"L1": {"power": 10.0, "voltage": 11.0, "current": 12.0}},
        )
        controller._phase_data_from_backend_metadata.assert_called_once_with({"pm": True}, 11.0, "line")
        controller._phase_values.assert_called_once_with(10.0, 11.0, "P1_P2", "line")

        metadata_only_service = SimpleNamespace(phase="L1")
        metadata_controller = UpdateCycleController(
            metadata_only_service,
            _phase_values,
            lambda reason: {"init": 0}.get(reason, 99),
        )
        metadata_controller._phase_data_from_backend_metadata = MagicMock(return_value={"L1": {"power": 2.0}})
        self.assertEqual(
            metadata_controller._phase_data_for_pm_status({"pm": True}, 2.0, 230.0),
            {"L1": {"power": 2.0}},
        )
        metadata_controller._phase_data_from_backend_metadata.assert_called_once_with({"pm": True}, 230.0, "phase")

    def test_phase_data_validation_and_backend_metadata_contracts(self):
        self.assertEqual(UpdateCycleController._phase_tuple([1, 2.5, 3]), (1.0, 2.5, 3.0))
        self.assertIsNone(UpdateCycleController._phase_tuple((1, 2)))
        self.assertIsNone(UpdateCycleController._phase_tuple((1, True, 3)))
        self.assertEqual(
            UpdateCycleController._checked_phase_data(
                {
                    "L1": {"power": 1, "voltage": 2.5, "current": 3},
                    "L2": {"power": 4.5, "voltage": 5, "current": 6.5},
                }
            ),
            {
                "L1": {"power": 1.0, "voltage": 2.5, "current": 3.0},
                "L2": {"power": 4.5, "voltage": 5.0, "current": 6.5},
            },
        )
        for bad_value in ([], {"L1": []}, {1: {"power": 1}}, {"L1": {1: 2.0}}, {"L1": {"power": "1"}}):
            with self.subTest(bad_value=bad_value), self.assertRaisesRegex(
                TypeError,
                r"_phase_values must return dict",
            ):
                UpdateCycleController._checked_phase_data(bad_value)
        with self.assertRaises(TypeError) as phase_error:
            UpdateCycleController._checked_phase_data({"L1": []})
        self.assertEqual(str(phase_error.exception), "_phase_values must return dict[str, dict[str, float]]")
        with self.assertRaisesRegex(TypeError, r"_phase_values must return dict"):
            UpdateCycleController._checked_phase_values({"power": True})
        with self.assertRaises(TypeError) as value_error:
            UpdateCycleController._checked_phase_values({"power": True})
        self.assertEqual(str(value_error.exception), "_phase_values must return dict[str, dict[str, float]]")

        controller = UpdateCycleController(SimpleNamespace(), _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        self.assertIsNone(controller._phase_data_from_backend_metadata(None, 230.0, "phase"))
        self.assertIsNone(controller._phase_data_from_backend_metadata({}, 230.0, "phase"))
        self.assertEqual(
            controller._phase_data_from_backend_metadata(
                {
                    "_phase_powers_w": (100.0, 200.0, 300.0),
                    "_phase_currents_a": (1.0, 2.0, 3.0),
                    "_phase_selection": "P1_P2_P3",
                },
                400.0,
                "line",
            ),
            {
                "L1": {"power": 100.0, "voltage": 400.0 / math.sqrt(3.0), "current": 1.0},
                "L2": {"power": 200.0, "voltage": 400.0 / math.sqrt(3.0), "current": 2.0},
                "L3": {"power": 300.0, "voltage": 400.0 / math.sqrt(3.0), "current": 3.0},
            },
        )
        self.assertEqual(
            controller._phase_data_from_backend_metadata(
                {"_phase_powers_w": (230.0, 0.0, 115.0), "_phase_selection": "P1"},
                230.0,
                "phase",
            ),
            {
                "L1": {"power": 230.0, "voltage": 230.0, "current": 1.0},
                "L2": {"power": 0.0, "voltage": 230.0, "current": 0.0},
                "L3": {"power": 115.0, "voltage": 230.0, "current": 0.5},
            },
        )

    def test_phase_measurement_derives_current_only_when_needed(self):
        self.assertEqual(
            UpdateCycleController._phase_measurement(230.0, None, 230.0),
            {"power": 230.0, "voltage": 230.0, "current": 1.0},
        )
        self.assertEqual(
            UpdateCycleController._phase_measurement(230.0, None, 0.0),
            {"power": 230.0, "voltage": 0.0, "current": 0.0},
        )
        self.assertEqual(
            UpdateCycleController._phase_measurement(230.0, 2.5, 230.0),
            {"power": 230.0, "voltage": 230.0, "current": 2.5},
        )

    def test_auto_relay_change_log_formats_present_and_missing_metrics(self):
        service = SimpleNamespace(
            _last_auto_metrics={"surplus": 1234.5, "grid": -456.7, "soc": 78.91},
            _last_health_reason="running",
        )

        with patch("venus_evcharger.update.relay_phase_publish.logging.info") as info:
            UpdateCycleController.log_auto_relay_change(service, True)

        self.assertEqual(
            info.call_args.args,
            (
                "Auto relay %s reason=%s surplus=%sW grid=%sW soc=%s%%",
                "ON",
                "running",
                "1234",
                "-457",
                "78.9",
            ),
        )

        service._last_auto_metrics = {"surplus": None, "grid": None, "soc": None}
        with patch("venus_evcharger.update.relay_phase_publish.logging.info") as info:
            UpdateCycleController.log_auto_relay_change(service, False)
        self.assertEqual(info.call_args.args[1:], ("OFF", "running", "na", "na", "na"))

    def test_relay_sync_tracking_confirmation_and_pre_timeout_contracts(self):
        service = SimpleNamespace(
            _relay_sync_expected_state=True,
            _relay_sync_requested_at=90.0,
            _relay_sync_deadline_at=100.0,
            _relay_sync_failure_reported=True,
            _mark_recovery=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertFalse(controller._relay_sync_confirmed_match(service, False, True, True))
        self.assertFalse(controller._relay_sync_confirmed_match(service, True, False, True))
        self.assertTrue(controller._relay_sync_confirmed_match(service, True, True, True))
        service._mark_recovery.assert_called_once_with("shelly", "Shelly relay confirmation recovered")
        self.assertIsNone(service._relay_sync_expected_state)
        self.assertIsNone(service._relay_sync_requested_at)
        self.assertIsNone(service._relay_sync_deadline_at)
        self.assertIs(service._relay_sync_failure_reported, False)

        self.assertTrue(UpdateCycleController._relay_sync_before_deadline(None, 100.0))
        self.assertTrue(UpdateCycleController._relay_sync_before_deadline(101.0, 100.0))
        self.assertFalse(UpdateCycleController._relay_sync_before_deadline(100.0, 100.0))
        self.assertEqual(UpdateCycleController._relay_sync_pre_timeout_result(False, True, True), "command-mismatch")
        self.assertIsNone(UpdateCycleController._relay_sync_pre_timeout_result(False, False, True))
        self.assertIsNone(UpdateCycleController._relay_sync_pre_timeout_result(True, True, True))

    def test_pm_status_confirmed_and_local_publish_warning_contracts(self):
        self.assertFalse(UpdateCycleController._pm_status_confirmed({}))
        self.assertFalse(UpdateCycleController._pm_status_confirmed({"_pm_confirmed": 0}))
        self.assertFalse(UpdateCycleController._pm_status_confirmed({"_PM_CONFIRMED": 1}))
        self.assertTrue(UpdateCycleController._pm_status_confirmed({"_pm_confirmed": 1}))

        successful_service = SimpleNamespace(
            _publish_local_pm_status=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        successful_controller = UpdateCycleController(
            successful_service,
            _phase_values,
            lambda reason: {"init": 0}.get(reason, 99),
        )
        successful_controller._publish_local_pm_status_best_effort(False, 123.4)
        successful_service._publish_local_pm_status.assert_called_once_with(False, 123.4)
        successful_service._warning_throttled.assert_not_called()

        error = RuntimeError("publish failed")
        service = SimpleNamespace(
            relay_sync_timeout_seconds=0.0,
            _publish_local_pm_status=MagicMock(side_effect=error),
            _warning_throttled=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        controller._publish_local_pm_status_best_effort(True, 100.0)

        service._publish_local_pm_status.assert_called_once_with(True, 100.0)
        service._warning_throttled.assert_called_once_with(
            "relay-placeholder-publish-failed",
            2.0,
            "Local relay placeholder publish failed after queueing relay=%s: %s",
            1,
            error,
            exc_info=error,
        )

        tuned_error = RuntimeError("publish failed again")
        tuned_service = SimpleNamespace(
            relay_sync_timeout_seconds=1.5,
            _publish_local_pm_status=MagicMock(side_effect=tuned_error),
            _warning_throttled=MagicMock(),
        )
        tuned_controller = UpdateCycleController(tuned_service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        tuned_controller._publish_local_pm_status_best_effort(False, 101.5)
        tuned_service._warning_throttled.assert_called_once_with(
            "relay-placeholder-publish-failed",
            1.5,
            "Local relay placeholder publish failed after queueing relay=%s: %s",
            0,
            tuned_error,
            exc_info=tuned_error,
        )

        default_error = RuntimeError("default timeout publish failed")
        default_timeout_service = SimpleNamespace(
            _publish_local_pm_status=MagicMock(side_effect=default_error),
            _warning_throttled=MagicMock(),
        )
        default_timeout_controller = UpdateCycleController(
            default_timeout_service,
            _phase_values,
            lambda reason: {"init": 0}.get(reason, 99),
        )
        default_timeout_controller._publish_local_pm_status_best_effort(True, 102.0)
        default_timeout_service._warning_throttled.assert_called_once_with(
            "relay-placeholder-publish-failed",
            2.0,
            "Local relay placeholder publish failed after queueing relay=%s: %s",
            1,
            default_error,
            exc_info=default_error,
        )

    def test_relay_sync_health_override_delegates_with_exact_state(self):
        service = SimpleNamespace(
            _relay_sync_expected_state=True,
            _relay_sync_deadline_at=120.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        controller._relay_sync_confirmed_match = MagicMock(return_value=False)
        controller._relay_sync_before_deadline = MagicMock(return_value=True)
        controller._relay_sync_pre_timeout_result = MagicMock(return_value="command-mismatch")
        controller._record_relay_sync_timeout = MagicMock()
        controller._clear_relay_sync_tracking = MagicMock()

        self.assertEqual(controller.relay_sync_health_override(False, True, 110.0), "command-mismatch")
        controller._relay_sync_confirmed_match.assert_called_once_with(service, False, True, True)
        controller._relay_sync_before_deadline.assert_called_once_with(120.0, 110.0)
        controller._relay_sync_pre_timeout_result.assert_called_once_with(False, True, True)
        controller._record_relay_sync_timeout.assert_not_called()
        controller._clear_relay_sync_tracking.assert_not_called()

        controller._relay_sync_before_deadline.return_value = False
        controller._relay_sync_confirmed_match.reset_mock()
        controller._relay_sync_pre_timeout_result.reset_mock()
        self.assertEqual(controller.relay_sync_health_override(False, False, 121.0), "relay-sync-failed")
        controller._relay_sync_confirmed_match.assert_called_once_with(service, False, False, True)
        controller._record_relay_sync_timeout.assert_called_once_with(service, False, False, True, 120.0)
        controller._clear_relay_sync_tracking.assert_called_once_with(service)
        controller._relay_sync_pre_timeout_result.assert_not_called()

        missing_deadline_service = SimpleNamespace(_relay_sync_expected_state=False)
        missing_deadline_controller = UpdateCycleController(
            missing_deadline_service,
            _phase_values,
            lambda reason: {"init": 0}.get(reason, 99),
        )
        missing_deadline_controller._relay_sync_confirmed_match = MagicMock(return_value=False)
        missing_deadline_controller._relay_sync_pre_timeout_result = MagicMock(return_value=None)
        self.assertIsNone(missing_deadline_controller.relay_sync_health_override(True, False, 122.0))
        missing_deadline_controller._relay_sync_pre_timeout_result.assert_called_once_with(True, False, False)

    def test_relay_sync_timeout_records_once_and_clears_tracking(self):
        service = SimpleNamespace(
            _relay_sync_failure_reported=False,
            _relay_sync_requested_at=90.0,
            _relay_sync_expected_state=True,
            _relay_sync_deadline_at=100.0,
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        controller._record_relay_sync_timeout(service, False, False, True, 100.0)

        service._mark_failure.assert_called_once_with("shelly")
        service._warning_throttled.assert_called_once_with(
            "relay-sync-failed",
            10.0,
            "Shelly relay state did not confirm to %s within %.1fs (actual=%s confirmed=%s)",
            True,
            10.0,
            False,
            0,
        )
        self.assertTrue(service._relay_sync_expected_state)
        self.assertEqual(service._relay_sync_requested_at, 90.0)
        self.assertEqual(service._relay_sync_deadline_at, 100.0)
        self.assertTrue(service._relay_sync_failure_reported)

        service._mark_failure.reset_mock()
        service._warning_throttled.reset_mock()
        service._relay_sync_failure_reported = True
        controller._record_relay_sync_timeout(service, True, True, False, 110.0)
        service._mark_failure.assert_not_called()
        service._warning_throttled.assert_not_called()

    def test_relay_sync_timeout_uses_minimum_warning_window_and_missing_request_fallback(self):
        service = SimpleNamespace(
            _relay_sync_failure_reported=False,
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        controller._record_relay_sync_timeout(service, True, True, False, 100.0)

        service._mark_failure.assert_called_once_with("shelly")
        service._warning_throttled.assert_called_once_with(
            "relay-sync-failed",
            1.0,
            "Shelly relay state did not confirm to %s within %.1fs (actual=%s confirmed=%s)",
            False,
            0.0,
            True,
            1,
        )

        service._relay_sync_failure_reported = False
        service._relay_sync_requested_at = 99.5
        service._mark_failure.reset_mock()
        service._warning_throttled.reset_mock()
        controller._record_relay_sync_timeout(service, True, False, True, 100.0)
        service._warning_throttled.assert_called_once_with(
            "relay-sync-failed",
            1.0,
            "Shelly relay state did not confirm to %s within %.1fs (actual=%s confirmed=%s)",
            True,
            0.5,
            True,
            0,
        )

        missing_reported_service = SimpleNamespace(
            _relay_sync_requested_at=98.0,
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        controller._record_relay_sync_timeout(missing_reported_service, False, True, True, 100.0)
        missing_reported_service._mark_failure.assert_called_once_with("shelly")
        missing_reported_service._warning_throttled.assert_called_once_with(
            "relay-sync-failed",
            2.0,
            "Shelly relay state did not confirm to %s within %.1fs (actual=%s confirmed=%s)",
            True,
            2.0,
            False,
            1,
        )

    def test_relay_sync_confirmed_match_handles_false_expected_state_and_clean_success(self):
        service = SimpleNamespace(
            _relay_sync_expected_state=False,
            _relay_sync_requested_at=10.0,
            _relay_sync_deadline_at=20.0,
            _mark_recovery=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertTrue(controller._relay_sync_confirmed_match(service, False, True, False))
        service._mark_recovery.assert_not_called()
        self.assertIsNone(service._relay_sync_expected_state)
        self.assertIsNone(service._relay_sync_requested_at)
        self.assertIsNone(service._relay_sync_deadline_at)
        self.assertIs(service._relay_sync_failure_reported, False)

    def test_clear_relay_sync_tracking_resets_every_field_to_idle_values(self):
        service = SimpleNamespace(
            _relay_sync_expected_state=True,
            _relay_sync_requested_at=1.0,
            _relay_sync_deadline_at=2.0,
            _relay_sync_failure_reported=True,
        )

        UpdateCycleController._clear_relay_sync_tracking(service)

        self.assertIsNone(service._relay_sync_expected_state)
        self.assertIsNone(service._relay_sync_requested_at)
        self.assertIsNone(service._relay_sync_deadline_at)
        self.assertIs(service._relay_sync_failure_reported, False)
