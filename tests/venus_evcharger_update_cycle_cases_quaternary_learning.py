# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_update_cycle_controller_support import *


class _UpdateCycleQuaternaryLearningCases:
    def test_update_learned_charge_power_requires_stable_active_charge(self):
        service = _learning_service(
            charging_started_at=None,
            learned_charge_power_watts=None,
            learned_charge_power_updated_at=None,
            learned_charge_power_state="unknown",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=0,
            auto_learn_charge_power_enabled=True,
            auto_learn_charge_power_start_delay_seconds=30.0,
            auto_learn_charge_power_window_seconds=180.0,
            auto_learn_charge_power_max_age_seconds=21600.0,
            auto_learn_charge_power_min_watts=500.0,
            auto_learn_charge_power_alpha=0.2,
            phase="L1",
            max_current=16.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertFalse(controller.update_learned_charge_power(False, 2, 1900.0, 230.0, 100.0))
        self.assertFalse(controller.update_learned_charge_power(True, 1, 1900.0, 230.0, 100.0))

        service.charging_started_at = 90.0
        self.assertFalse(controller.update_learned_charge_power(True, 2, 1900.0, 230.0, 100.0))
        self.assertFalse(controller.update_learned_charge_power(True, 2, 400.0, 230.0, 130.0))
        self.assertIsNone(service.learned_charge_power_watts)

    def test_learning_window_status_waits_without_session_start(self):
        service = _learning_service(
            charging_started_at=None,
            auto_learn_charge_power_start_delay_seconds=30.0,
            auto_learn_charge_power_window_seconds=180.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertEqual(controller._learning_window_status(100.0), ("waiting", None))

    def test_update_learned_charge_power_learns_and_smooths_stable_power(self):
        service = _learning_service(
            charging_started_at=50.0,
            learned_charge_power_watts=None,
            learned_charge_power_updated_at=None,
            learned_charge_power_state="unknown",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=0,
            auto_learn_charge_power_enabled=True,
            auto_learn_charge_power_start_delay_seconds=30.0,
            auto_learn_charge_power_window_seconds=180.0,
            auto_learn_charge_power_max_age_seconds=21600.0,
            auto_learn_charge_power_min_watts=500.0,
            auto_learn_charge_power_alpha=0.2,
            phase="L1",
            max_current=16.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertTrue(controller.update_learned_charge_power(True, 2, 1900.0, 230.0, 100.0))
        self.assertEqual(service.learned_charge_power_watts, 1900.0)
        self.assertEqual(service.learned_charge_power_updated_at, 100.0)
        self.assertEqual(service.learned_charge_power_state, "learning")
        self.assertEqual(service.learned_charge_power_learning_since, 100.0)
        self.assertEqual(service.learned_charge_power_sample_count, 1)

        self.assertTrue(controller.update_learned_charge_power(True, 2, 1940.0, 230.0, 110.0))
        self.assertEqual(service.learned_charge_power_watts, 1908.0)
        self.assertEqual(service.learned_charge_power_updated_at, 110.0)
        self.assertEqual(service.learned_charge_power_state, "learning")
        self.assertEqual(service.learned_charge_power_sample_count, 2)

        self.assertTrue(controller.update_learned_charge_power(True, 2, 1920.0, 230.0, 116.0))
        self.assertEqual(service.learned_charge_power_watts, 1910.4)
        self.assertEqual(service.learned_charge_power_updated_at, 116.0)
        self.assertEqual(service.learned_charge_power_state, "stable")
        self.assertIsNone(service.learned_charge_power_learning_since)
        self.assertEqual(service.learned_charge_power_sample_count, 3)

    def test_update_learned_charge_power_respects_disable_and_configurable_learning_parameters(self):
        disabled_service = _learning_service(
            charging_started_at=50.0,
            learned_charge_power_watts=None,
            learned_charge_power_updated_at=None,
            learned_charge_power_state="unknown",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=0,
            auto_learn_charge_power_enabled=False,
            auto_learn_charge_power_start_delay_seconds=30.0,
            auto_learn_charge_power_window_seconds=180.0,
            auto_learn_charge_power_max_age_seconds=21600.0,
            auto_learn_charge_power_min_watts=500.0,
            auto_learn_charge_power_alpha=0.2,
            phase="L1",
            max_current=16.0,
        )
        disabled_controller = UpdateCycleController(
            disabled_service,
            _phase_values,
            lambda reason: {"init": 0}.get(reason, 99),
        )
        self.assertFalse(disabled_controller.update_learned_charge_power(True, 2, 1900.0, 230.0, 100.0))
        self.assertIsNone(disabled_service.learned_charge_power_watts)

        tuned_service = _learning_service(
            charging_started_at=50.0,
            learned_charge_power_watts=1800.0,
            learned_charge_power_updated_at=80.0,
            learned_charge_power_state="stable",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=3,
            auto_learn_charge_power_enabled=True,
            auto_learn_charge_power_start_delay_seconds=40.0,
            auto_learn_charge_power_window_seconds=180.0,
            auto_learn_charge_power_max_age_seconds=21600.0,
            auto_learn_charge_power_min_watts=700.0,
            auto_learn_charge_power_alpha=0.5,
            phase="L1",
            max_current=16.0,
        )
        tuned_controller = UpdateCycleController(
            tuned_service,
            _phase_values,
            lambda reason: {"init": 0}.get(reason, 99),
        )
        self.assertFalse(tuned_controller.update_learned_charge_power(True, 2, 650.0, 230.0, 95.0))
        self.assertTrue(tuned_controller.update_learned_charge_power(True, 2, 2000.0, 230.0, 100.0))
        self.assertEqual(tuned_service.learned_charge_power_watts, 1900.0)
        self.assertEqual(tuned_service.learned_charge_power_updated_at, 100.0)
        self.assertEqual(tuned_service.learned_charge_power_state, "stable")

    def test_update_learned_charge_power_uses_early_session_window_and_restarts_from_stale_value(self):
        service = _learning_service(
            charging_started_at=50.0,
            learned_charge_power_watts=2400.0,
            learned_charge_power_updated_at=-30.0,
            learned_charge_power_state="stale",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=0,
            auto_learn_charge_power_enabled=True,
            auto_learn_charge_power_start_delay_seconds=30.0,
            auto_learn_charge_power_window_seconds=60.0,
            auto_learn_charge_power_max_age_seconds=120.0,
            auto_learn_charge_power_min_watts=500.0,
            auto_learn_charge_power_alpha=0.2,
            phase="L1",
            max_current=16.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertTrue(controller.update_learned_charge_power(True, 2, 1900.0, 230.0, 100.0))
        self.assertEqual(service.learned_charge_power_watts, 1900.0)
        self.assertEqual(service.learned_charge_power_state, "learning")
        self.assertTrue(controller.update_learned_charge_power(True, 2, 2000.0, 230.0, 150.5))
        self.assertIsNone(service.learned_charge_power_watts)
        self.assertEqual(service.learned_charge_power_state, "unknown")

    def test_stored_positive_learned_charge_power_rejects_non_positive_values(self):
        service = _learning_service(learned_charge_power_watts=0.0)
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertIsNone(controller._stored_positive_learned_charge_power())

    def test_learning_signature_contract_helpers_cover_session_delay_and_stable_power(self):
        service = _learning_service(
            charging_started_at=50.0,
            learned_charge_power_state="stable",
            learned_charge_power_watts=1900.0,
            auto_learn_charge_power_start_delay_seconds=30.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertFalse(controller._signature_session_delay_elapsed(50.0, 79.999))
        self.assertTrue(controller._signature_session_delay_elapsed(50.0, 80.0))
        self.assertEqual(controller._eligible_signature_session_started_at(True, 80.0), 50.0)

        service.learned_charge_power_signature_checked_session_started_at = 50.0
        self.assertTrue(controller._signature_session_already_checked(50.0))
        self.assertIsNone(controller._eligible_signature_session_started_at(True, 100.0))
        service.learned_charge_power_signature_checked_session_started_at = None
        self.assertIsNone(controller._eligible_signature_session_started_at(False, 100.0))
        service.charging_started_at = None
        self.assertIsNone(controller._eligible_signature_session_started_at(True, 100.0))

        self.assertEqual(controller._stable_learned_power(), 1900.0)
        service.learned_charge_power_state = "learning"
        self.assertIsNone(controller._stable_learned_power())
        service.learned_charge_power_state = "stable"
        service.learned_charge_power_watts = 0.0
        self.assertIsNone(controller._stable_learned_power())
        service.learned_charge_power_watts = 0.5
        self.assertEqual(controller._stable_learned_power(), 0.5)

    def test_learning_signature_contract_helpers_normalize_snapshot_and_samples(self):
        service = _learning_service(
            learned_charge_power_phase="bad-phase",
            learned_charge_power_voltage=float("nan"),
            learned_charge_power_sample_count=1,
            learned_charge_power_signature_mismatch_sessions=-4,
            learned_charge_power_signature_checked_session_started_at=float("inf"),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertEqual(
            controller._signature_preserving_snapshot(),
            {
                "phase_signature": None,
                "voltage_signature": None,
                "signature_mismatch_sessions": 0,
                "checked_session_started_at": None,
            },
        )
        self.assertEqual(controller._stable_sample_count(), controller.LEARNED_POWER_STABLE_MIN_SAMPLES)
        service.learned_charge_power_sample_count = 5
        self.assertEqual(controller._stable_sample_count(), 5)
        service.learned_charge_power_phase = "3P"
        service.learned_charge_power_voltage = 231.04
        service.learned_charge_power_signature_mismatch_sessions = 2
        service.learned_charge_power_signature_checked_session_started_at = 55.0
        self.assertEqual(
            controller._signature_preserving_snapshot(),
            {
                "phase_signature": "3P",
                "voltage_signature": 231.04,
                "signature_mismatch_sessions": 2,
                "checked_session_started_at": 55.0,
            },
        )

    def test_learning_signature_contract_helpers_cover_phase_reset_and_stable_apply(self):
        service = _learning_service(
            learned_charge_power_state="stable",
            learned_charge_power_watts=1900.0,
            learned_charge_power_phase="3P",
            phase="L1",
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertIsNone(controller._phase_change_reset(None, "L1"))
        self.assertIsNone(controller._phase_change_reset("L1", None))
        self.assertIsNone(controller._phase_change_reset("L1", "L1"))
        with self.assertLogs(level="WARNING") as phase_logs:
            self.assertTrue(controller._phase_change_reset("3P", "L1"))
        self.assertRegex(
            phase_logs.output[0],
            r"^WARNING:root:Discarding learned charge power after phase signature changed from 3P to L1$",
        )
        self.assertEqual(service.learned_charge_power_state, "unknown")
        self.assertIsNone(service.learned_charge_power_watts)
        self.assertIsNone(service.learned_charge_power_updated_at)
        self.assertIsNone(service.learned_charge_power_learning_since)
        self.assertEqual(service.learned_charge_power_sample_count, 0)
        self.assertIsNone(service.learned_charge_power_phase)
        self.assertIsNone(service.learned_charge_power_voltage)
        self.assertEqual(service.learned_charge_power_signature_mismatch_sessions, 0)
        self.assertIsNone(service.learned_charge_power_signature_checked_session_started_at)

        self.assertTrue(
            controller._apply_stable_learning(
                1900.04,
                updated_at=100.0,
                phase_signature="L1",
                voltage_signature=230.04,
                signature_mismatch_sessions=2,
                checked_session_started_at=50.0,
            )
        )
        self.assertEqual(service.learned_charge_power_state, "stable")
        self.assertEqual(service.learned_charge_power_watts, 1900.0)
        self.assertEqual(service.learned_charge_power_updated_at, 100.0)
        self.assertEqual(service.learned_charge_power_sample_count, controller.LEARNED_POWER_STABLE_MIN_SAMPLES)
        self.assertEqual(service.learned_charge_power_phase, "L1")
        self.assertEqual(service.learned_charge_power_voltage, 230.0)
        self.assertEqual(service.learned_charge_power_signature_mismatch_sessions, 2)
        self.assertEqual(service.learned_charge_power_signature_checked_session_started_at, 50.0)
        self.assertFalse(
            controller._apply_stable_learning(
                1900.04,
                updated_at=100.0,
                phase_signature="L1",
                voltage_signature=230.04,
                signature_mismatch_sessions=2,
                checked_session_started_at=50.0,
            )
        )
        with patch.object(controller, "_set_learning_tracking", return_value=True) as set_tracking:
            self.assertTrue(controller._clear_learning_tracking())
        set_tracking.assert_called_once()
        self.assertEqual(set_tracking.call_args.kwargs["state"], "unknown")
        with patch.object(controller, "_set_learning_tracking", return_value=True) as set_tracking:
            self.assertTrue(
                controller._apply_stable_learning(
                    1234.5,
                    updated_at=12.0,
                    phase_signature="L1",
                    voltage_signature=230.0,
                    signature_mismatch_sessions=0,
                    checked_session_started_at=50.0,
                )
            )
        self.assertEqual(set_tracking.call_args.kwargs["state"], "stable")

    def test_learning_signature_contract_helpers_cover_mismatch_reasons_and_reconcile(self):
        service = _learning_service(
            charging_started_at=50.0,
            learned_charge_power_watts=1900.0,
            learned_charge_power_state="stable",
            learned_charge_power_phase="L1",
            learned_charge_power_voltage=230.0,
            learned_charge_power_signature_mismatch_sessions=0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        reasons, voltage_signature = controller._signature_mismatch_reasons(1900.0, 230.0, 1900.0)
        self.assertEqual(reasons, [])
        self.assertEqual(voltage_signature, 230.0)
        service.learned_charge_power_voltage = 100.0
        voltage_tolerance = controller._voltage_signature_tolerance(100.0)
        power_tolerance = controller._learning_stability_tolerance(1900.0)
        reasons, voltage_signature = controller._signature_mismatch_reasons(
            1900.0 + power_tolerance,
            100.0 + voltage_tolerance,
            1900.0,
        )
        self.assertEqual(reasons, [])
        self.assertEqual(voltage_signature, 100.0 + voltage_tolerance)
        service.learned_charge_power_voltage = 230.0
        reasons, voltage_signature = controller._signature_mismatch_reasons(2200.0, 255.0, 1900.0)
        self.assertEqual(reasons, ["voltage", "power"])
        self.assertEqual(voltage_signature, 255.0)
        service._last_voltage = 231.0
        reasons, voltage_signature = controller._signature_mismatch_reasons(1900.0, 0.0, 1900.0)
        self.assertEqual(reasons, [])
        self.assertEqual(voltage_signature, 231.0)

        self.assertTrue(
            controller._apply_signature_reconcile_result(
                1900.0,
                2200.0,
                "L1",
                255.0,
                50.0,
                ["voltage", "power"],
            )
        )
        self.assertEqual(service.learned_charge_power_signature_mismatch_sessions, 1)
        self.assertEqual(service.learned_charge_power_signature_checked_session_started_at, 50.0)
        self.assertEqual(service.learned_charge_power_updated_at, 90.0)

        service.learned_charge_power_signature_mismatch_sessions = 1
        with self.assertLogs(level="WARNING") as terminal_logs:
            self.assertTrue(
                controller._apply_signature_reconcile_result(
                    1900.0,
                    2200.0,
                    "L1",
                    255.0,
                    90.0,
                    ["power"],
                )
            )
        self.assertRegex(
            terminal_logs.output[0],
            r"^WARNING:root:Discarding learned charge power after 2 mismatching sessions "
            r"\(power\): learned=1900\.0W measured=2200\.0W phase=L1/L1 voltage=230\.0/255\.0V$",
        )
        self.assertEqual(service.learned_charge_power_state, "unknown")
        self.assertIsNone(service.learned_charge_power_watts)
        service.learned_charge_power_state = "stable"
        service.learned_charge_power_watts = 1900.0
        service.learned_charge_power_updated_at = 91.0
        service.learned_charge_power_phase = None
        service.learned_charge_power_voltage = 230.0
        service.learned_charge_power_signature_mismatch_sessions = 0
        self.assertTrue(
            controller._apply_signature_reconcile_result(
                1911.0,
                2200.0,
                "3P",
                255.0,
                120.0,
                ["power"],
            )
        )
        self.assertEqual(service.learned_charge_power_watts, 1911.0)
        self.assertEqual(service.learned_charge_power_phase, "3P")
        self.assertEqual(service.learned_charge_power_voltage, 230.0)
        self.assertEqual(service.learned_charge_power_updated_at, 91.0)
        service.learned_charge_power_phase = None
        service.learned_charge_power_updated_at = 92.0
        self.assertTrue(
            controller._apply_signature_reconcile_result(
                1912.0,
                1912.0,
                "3P",
                230.0,
                130.0,
                [],
            )
        )
        self.assertEqual(service.learned_charge_power_watts, 1912.0)
        self.assertEqual(service.learned_charge_power_phase, "3P")
        self.assertEqual(service.learned_charge_power_updated_at, 92.0)
        self.assertEqual(service.learned_charge_power_signature_checked_session_started_at, 130.0)

    def test_learning_signature_contract_helpers_cover_stable_reconcile_labels_and_logging(self):
        service = _learning_service(
            charging_started_at=50.0,
            learned_charge_power_watts=1900.0,
            learned_charge_power_state="stable",
            learned_charge_power_phase=None,
            learned_charge_power_voltage=230.0,
            learned_charge_power_signature_mismatch_sessions=1,
            learned_charge_power_signature_checked_session_started_at=None,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertEqual(controller._signature_reason_label(["voltage", "power"]), "voltage, power")
        self.assertIsNone(controller._rounded_signature_value(None))
        self.assertEqual(controller._rounded_signature_value(230.04), 230.0)

        snapshot = controller._signature_preserving_snapshot()
        self.assertTrue(controller._stable_signature_reconcile_result(1900.0, "L1", 50.0, snapshot))
        self.assertEqual(service.learned_charge_power_watts, 1900.0)
        self.assertEqual(service.learned_charge_power_phase, "L1")
        self.assertEqual(service.learned_charge_power_voltage, 230.0)
        self.assertEqual(service.learned_charge_power_signature_mismatch_sessions, 0)
        self.assertEqual(service.learned_charge_power_signature_checked_session_started_at, 50.0)

        with self.assertLogs(level="WARNING") as logs:
            controller._log_terminal_signature_mismatch(
                2,
                "power",
                1900.04,
                2200.04,
                "L1",
                255.0,
                {
                    "phase_signature": "L1",
                    "voltage_signature": 230.0,
                    "signature_mismatch_sessions": 1,
                    "checked_session_started_at": 50.0,
                },
            )
        self.assertRegex(
            logs.output[0],
            r"^WARNING:root:Discarding learned charge power after 2 mismatching sessions "
            r"\(power\): learned=1900\.0W measured=2200\.0W phase=L1/L1 voltage=230\.0/255\.0V$",
        )
        self.assertIn("learned=1900.0W measured=2200.0W", logs.output[0])
        self.assertIn("phase=L1/L1 voltage=230.0/255.0V", logs.output[0])

        service.learned_charge_power_signature_mismatch_sessions = 0
        with self.assertLogs(level="INFO") as info_logs:
            self.assertTrue(
                controller._mismatching_signature_reconcile_result(
                    1900.0,
                    2200.0,
                    "L1",
                    255.0,
                    70.0,
                    ["power"],
                    {
                        "phase_signature": "L1",
                        "voltage_signature": 230.0,
                        "signature_mismatch_sessions": 0,
                        "checked_session_started_at": None,
                    },
                )
            )
        self.assertRegex(
            info_logs.output[0],
            r"^INFO:root:Observed learned charge-power signature mismatch session 1/2 \(power\)$",
        )

    def test_update_learned_charge_power_rejects_implausible_spike(self):
        service = _learning_service(
            charging_started_at=50.0,
            learned_charge_power_watts=1900.0,
            learned_charge_power_updated_at=90.0,
            learned_charge_power_state="stable",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=3,
            auto_learn_charge_power_enabled=True,
            auto_learn_charge_power_start_delay_seconds=30.0,
            auto_learn_charge_power_window_seconds=180.0,
            auto_learn_charge_power_max_age_seconds=21600.0,
            auto_learn_charge_power_min_watts=500.0,
            auto_learn_charge_power_alpha=0.2,
            phase="L1",
            max_current=16.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertFalse(controller.update_learned_charge_power(True, 2, 5000.0, 230.0, 100.0))
        self.assertEqual(service.learned_charge_power_watts, 1900.0)

    def test_orchestrate_pending_phase_switch_enters_stabilization_after_confirmed_relay_off(self):
        service = SimpleNamespace(
            _phase_switch_pending_selection="P1_P2",
            _phase_switch_state="waiting-relay-off",
            _phase_switch_requested_at=98.0,
            _phase_switch_stable_until=None,
            _phase_switch_resume_relay=True,
            requested_phase_selection="P1_P2",
            active_phase_selection="P1",
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _apply_phase_selection=MagicMock(return_value="P1_P2"),
            _save_runtime_state=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        relay_on, power, current, confirmed, desired_override = controller.orchestrate_pending_phase_switch(
            {"output": False, "_phase_selection": "P1"},
            False,
            0.0,
            0.0,
            True,
            100.0,
            False,
        )

        self.assertFalse(relay_on)
        self.assertEqual(power, 0.0)
        self.assertEqual(current, 0.0)
        self.assertFalse(confirmed)
        self.assertFalse(desired_override)
        service._apply_phase_selection.assert_called_once_with("P1_P2")
        self.assertEqual(service.requested_phase_selection, "P1_P2")
        self.assertEqual(service.active_phase_selection, "P1")
        self.assertEqual(service._phase_switch_state, "stabilizing")
        self.assertEqual(service._phase_switch_stable_until, 102.0)
