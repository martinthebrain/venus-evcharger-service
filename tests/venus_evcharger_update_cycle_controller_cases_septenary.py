# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_update_cycle_controller_support import *


class TestUpdateCycleControllerSeptenary(UpdateCycleControllerTestBase):
    def test_update_learned_charge_power_ignores_unconfirmed_measurements(self):
        service = SimpleNamespace(
            charging_started_at=50.0,
            learned_charge_power_watts=1900.0,
            learned_charge_power_updated_at=90.0,
            learned_charge_power_state="stable",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=3,
            auto_policy=_learning_policy(),
            phase="L1",
            max_current=16.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertFalse(
            controller.components.learning.update_learned_charge_power(
                True,
                2,
                2400.0,
                230.0,
                100.0,
                pm_confirmed=False,
            )
        )
        self.assertEqual(service.learned_charge_power_watts, 1900.0)
        self.assertEqual(service.learned_charge_power_state, "stable")

    def test_is_learned_charge_power_stale_covers_disabled_expiry_and_missing_timestamp(self):
        service = SimpleNamespace(
            auto_policy=_learning_policy(max_age_seconds=0.0),
            learned_charge_power_updated_at=None,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertFalse(controller.components.learning._is_learned_charge_power_stale(100.0))

        service.auto_policy.learn_charge_power.max_age_seconds = 60.0
        self.assertTrue(controller.components.learning._is_learned_charge_power_stale(100.0))

    def test_direct_pm_snapshot_max_age_seconds_ignores_invalid_worker_poll_interval(self):
        service = SimpleNamespace(_worker_poll_interval_seconds="bad")

        self.assertEqual(PmSnapshotResolver._direct_pm_snapshot_max_age_seconds(service), 1.0)

    def test_refresh_learned_charge_power_state_marks_stale_and_promotes_persisted_value_to_stable(self):
        stale_service = SimpleNamespace(
            learned_charge_power_watts=1980.0,
            learned_charge_power_updated_at=10.0,
            learned_charge_power_state="stable",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=3,
            learned_charge_power_phase="L1",
            learned_charge_power_voltage=230.0,
            learned_charge_power_signature_mismatch_sessions=0,
            learned_charge_power_signature_checked_session_started_at=None,
            phase="L1",
            auto_policy=_learning_policy(max_age_seconds=60.0),
        )
        stale_controller = UpdateCycleController(stale_service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        self.assertTrue(stale_controller.components.learning.refresh_learned_charge_power_state(100.0))
        self.assertEqual(stale_service.learned_charge_power_state, "stale")

        persisted_service = SimpleNamespace(
            learned_charge_power_watts=1980.0,
            learned_charge_power_updated_at=90.0,
            learned_charge_power_state="unknown",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=0,
            learned_charge_power_phase=None,
            learned_charge_power_voltage=None,
            learned_charge_power_signature_mismatch_sessions=0,
            learned_charge_power_signature_checked_session_started_at=None,
            phase="L1",
            auto_policy=_learning_policy(max_age_seconds=60.0),
        )
        persisted_controller = UpdateCycleController(
            persisted_service,
            _phase_values,
            lambda reason: {"init": 0}.get(reason, 99),
        )
        self.assertTrue(persisted_controller.components.learning.refresh_learned_charge_power_state(100.0))
        self.assertEqual(persisted_service.learned_charge_power_state, "stable")

        unchanged_stale_service = SimpleNamespace(
            learned_charge_power_watts=1980.0,
            learned_charge_power_updated_at=90.0,
            learned_charge_power_state="stale",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=0,
            learned_charge_power_phase="L1",
            learned_charge_power_voltage=230.0,
            learned_charge_power_signature_mismatch_sessions=0,
            learned_charge_power_signature_checked_session_started_at=None,
            phase="L1",
            auto_policy=_learning_policy(max_age_seconds=60.0),
        )
        unchanged_stale_controller = UpdateCycleController(
            unchanged_stale_service,
            _phase_values,
            lambda reason: {"init": 0}.get(reason, 99),
        )
        self.assertFalse(unchanged_stale_controller.components.learning.refresh_learned_charge_power_state(100.0))
        self.assertEqual(unchanged_stale_service.learned_charge_power_state, "stale")

        learning_service = SimpleNamespace(
            learned_charge_power_watts=1980.0,
            learned_charge_power_updated_at=90.0,
            learned_charge_power_state="learning",
            learned_charge_power_learning_since=85.0,
            learned_charge_power_sample_count=2,
            learned_charge_power_phase="L1",
            learned_charge_power_voltage=230.0,
            learned_charge_power_signature_mismatch_sessions=0,
            learned_charge_power_signature_checked_session_started_at=None,
            phase="L1",
            auto_policy=_learning_policy(max_age_seconds=60.0),
        )
        learning_controller = UpdateCycleController(
            learning_service,
            _phase_values,
            lambda reason: {"init": 0}.get(reason, 99),
        )
        self.assertFalse(learning_controller.components.learning.refresh_learned_charge_power_state(100.0))
        self.assertEqual(learning_service.learned_charge_power_state, "learning")

    def test_refresh_learned_charge_power_state_discards_value_when_phase_signature_changes(self):
        service = SimpleNamespace(
            learned_charge_power_watts=1980.0,
            learned_charge_power_updated_at=90.0,
            learned_charge_power_state="stable",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=3,
            learned_charge_power_phase="3P",
            learned_charge_power_voltage=230.0,
            learned_charge_power_signature_mismatch_sessions=1,
            learned_charge_power_signature_checked_session_started_at=50.0,
            phase="L1",
            auto_policy=_learning_policy(max_age_seconds=60.0),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertTrue(controller.components.learning.refresh_learned_charge_power_state(100.0))
        self.assertEqual(service.learned_charge_power_state, "unknown")
        self.assertIsNone(service.learned_charge_power_watts)
        self.assertIsNone(service.learned_charge_power_phase)
        self.assertEqual(service.learned_charge_power_signature_mismatch_sessions, 0)

    def test_update_learned_charge_power_discards_incomplete_learning_when_charge_stops(self):
        service = SimpleNamespace(
            charging_started_at=50.0,
            learned_charge_power_watts=1900.0,
            learned_charge_power_updated_at=100.0,
            learned_charge_power_state="learning",
            learned_charge_power_learning_since=100.0,
            learned_charge_power_sample_count=1,
            auto_policy=_learning_policy(),
            phase="L1",
            max_current=16.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertTrue(controller.components.learning.update_learned_charge_power(False, 2, 1900.0, 230.0, 110.0))
        self.assertIsNone(service.learned_charge_power_watts)
        self.assertEqual(service.learned_charge_power_state, "unknown")

    def test_update_learned_charge_power_keeps_non_learning_value_when_window_is_already_over(self):
        service = SimpleNamespace(
            charging_started_at=50.0,
            learned_charge_power_watts=1900.0,
            learned_charge_power_updated_at=100.0,
            learned_charge_power_state="stable",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=3,
            auto_policy=_learning_policy(window_seconds=10.0),
            phase="L1",
            max_current=16.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertFalse(controller.components.learning.update_learned_charge_power(True, 2, 1920.0, 230.0, 100.5))
        self.assertEqual(service.learned_charge_power_watts, 1900.0)
        self.assertEqual(service.learned_charge_power_state, "stable")

    def test_update_learned_charge_power_recovers_missing_learning_since_and_restarts_on_unstable_sample(self):
        service = SimpleNamespace(
            charging_started_at=50.0,
            learned_charge_power_watts=1900.0,
            learned_charge_power_updated_at=100.0,
            learned_charge_power_state="learning",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=1,
            auto_policy=_learning_policy(),
            phase="L1",
            max_current=16.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertTrue(controller.components.learning.update_learned_charge_power(True, 2, 1910.0, 230.0, 100.0))
        self.assertEqual(service.learned_charge_power_state, "learning")
        self.assertEqual(service.learned_charge_power_learning_since, 100.0)
        self.assertEqual(service.learned_charge_power_sample_count, 2)

        self.assertTrue(controller.components.learning.update_learned_charge_power(True, 2, 2300.0, 230.0, 101.0))
        self.assertEqual(service.learned_charge_power_state, "learning")
        self.assertEqual(service.learned_charge_power_watts, 2300.0)
        self.assertEqual(service.learned_charge_power_learning_since, 101.0)
        self.assertEqual(service.learned_charge_power_sample_count, 1)

    def test_reconcile_learned_charge_power_signature_discards_after_two_mismatching_sessions(self):
        service = _learning_service()
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertTrue(controller.components.learning.reconcile_learned_charge_power_signature(True, 2300.0, 230.0, 100.0))
        self.assertEqual(service.learned_charge_power_state, "stable")
        self.assertEqual(service.learned_charge_power_signature_mismatch_sessions, 1)
        self.assertEqual(service.learned_charge_power_signature_checked_session_started_at, 50.0)

        service.charging_started_at = 120.0
        self.assertTrue(controller.components.learning.reconcile_learned_charge_power_signature(True, 2320.0, 230.0, 160.0))
        self.assertEqual(service.learned_charge_power_state, "unknown")
        self.assertIsNone(service.learned_charge_power_watts)
        self.assertIsNone(service.learned_charge_power_phase)
        self.assertEqual(service.learned_charge_power_signature_mismatch_sessions, 0)

    def test_reconcile_learned_charge_power_signature_tracks_voltage_sessions_and_resets_on_match(self):
        service = _learning_service()
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertTrue(controller.components.learning.reconcile_learned_charge_power_signature(True, 1910.0, 255.0, 100.0))
        self.assertEqual(service.learned_charge_power_state, "stable")
        self.assertEqual(service.learned_charge_power_signature_mismatch_sessions, 1)

        service.charging_started_at = 130.0
        self.assertTrue(controller.components.learning.reconcile_learned_charge_power_signature(True, 1910.0, 231.0, 170.0))
        self.assertEqual(service.learned_charge_power_state, "stable")
        self.assertEqual(service.learned_charge_power_signature_mismatch_sessions, 0)
        self.assertEqual(service.learned_charge_power_signature_checked_session_started_at, 130.0)

    def test_reconcile_learned_charge_power_signature_covers_phase_mismatch_and_early_session_guards(self):
        service = _learning_service(
            charging_started_at=None,
            learned_charge_power_phase="3P",
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertTrue(controller.components.learning.reconcile_learned_charge_power_signature(True, 1900.0, 230.0, 100.0))
        self.assertEqual(service.learned_charge_power_state, "unknown")

        service.learned_charge_power_watts = 1900.0
        service.learned_charge_power_updated_at = 90.0
        service.learned_charge_power_state = "stable"
        service.learned_charge_power_phase = "L1"
        service.learned_charge_power_voltage = 230.0
        self.assertFalse(controller.components.learning.reconcile_learned_charge_power_signature(True, 1900.0, 230.0, 100.0))

        service.charging_started_at = 90.0
        self.assertFalse(controller.components.learning.reconcile_learned_charge_power_signature(True, 1900.0, 230.0, 100.0))

        service.charging_started_at = 50.0
        service.learned_charge_power_signature_checked_session_started_at = 50.0
        self.assertFalse(controller.components.learning.reconcile_learned_charge_power_signature(True, 1900.0, 230.0, 100.0))

    def test_reconcile_learned_charge_power_signature_ignores_unconfirmed_measurements(self):
        service = _learning_service(
            learned_charge_power_signature_mismatch_sessions=1,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertFalse(
            controller.components.learning.reconcile_learned_charge_power_signature(
                True,
                2600.0,
                250.0,
                100.0,
                pm_confirmed=False,
            )
        )
        self.assertEqual(service.learned_charge_power_watts, 1900.0)
        self.assertEqual(service.learned_charge_power_signature_mismatch_sessions, 1)

    def test_apply_stable_learning_persists_all_diagnostic_contract_fields(self):
        service = _learning_service(
            learned_charge_power_sample_count=1,
            learned_charge_power_confidence=0.1,
            learned_charge_power_stability_score=0.2,
            learned_charge_power_reason="old",
            learned_charge_power_detail="stale",
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertTrue(
            controller.components.learning._apply_stable_learning(
                2140.44,
                updated_at=123.0,
                phase_signature="L1",
                voltage_signature=229.94,
                signature_mismatch_sessions=1,
                checked_session_started_at=50.0,
                confidence=0.81,
                stability_score=0.73,
                reason="signature-ok",
                detail="stable-window",
            )
        )

        self.assertEqual(service.learned_charge_power_state, "stable")
        self.assertEqual(service.learned_charge_power_watts, 2140.4)
        self.assertEqual(service.learned_charge_power_updated_at, 123.0)
        self.assertIsNone(service.learned_charge_power_learning_since)
        self.assertEqual(service.learned_charge_power_sample_count, controller.components.learning.LEARNED_POWER_STABLE_MIN_SAMPLES)
        self.assertEqual(service.learned_charge_power_phase, "L1")
        self.assertEqual(service.learned_charge_power_voltage, 229.9)
        self.assertEqual(service.learned_charge_power_signature_mismatch_sessions, 1)
        self.assertEqual(service.learned_charge_power_signature_checked_session_started_at, 50.0)
        self.assertEqual(service.learned_charge_power_confidence, 0.81)
        self.assertEqual(service.learned_charge_power_stability_score, 0.73)
        self.assertEqual(service.learned_charge_power_reason, "signature-ok")
        self.assertEqual(service.learned_charge_power_detail, "stable-window")
