# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_update_cycle_controller_support import *


class TestUpdateCycleControllerSenary(UpdateCycleControllerTestBase):
    def test_native_smartevse_backend_handles_update_and_write_cycle_without_external_switch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = self._write_config(
                temp_dir,
                "charger.ini",
                "[Adapter]\nType=smartevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.60\nPort=502\nUnitId=1\n",
            )
            fake_transport = _FakeSmartEvseTransport()
            service = SimpleNamespace(
                backend_mode="split",
                meter_backend_type="none",
                switch_backend_type="none",
                charger_backend_type="smartevse_charger",
                meter_backend_config_path="",
                switch_backend_config_path="",
                charger_backend_config_path=str(charger_path),
                host="192.168.1.20",
                pm_component="Switch",
                pm_id=0,
                shelly_request_timeout_seconds=2.0,
                use_digest_auth=False,
                username="",
                password="",
                _last_auto_metrics={"surplus": None, "grid": None, "soc": None},
                _last_health_reason="init",
                auto_audit_log=False,
                auto_shelly_soft_fail_seconds=10.0,
                _queue_relay_command=MagicMock(),
                _mark_failure=MagicMock(),
                _mark_recovery=MagicMock(),
                _warning_throttled=MagicMock(),
                _publish_local_pm_status=MagicMock(side_effect=lambda relay, now: {"output": relay, "at": now}),
                _relay_sync_expected_state=None,
                _relay_sync_requested_at=None,
                _relay_sync_deadline_at=None,
                _relay_sync_failure_reported=False,
                _startup_manual_target=False,
                virtual_mode=0,
                _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            )

            with patch(
                "venus_evcharger.backend.native_modbus_backend.create_modbus_transport",
                return_value=fake_transport,
            ):
                sync_backend_runtime_test_service(service)
                resolved = build_service_backends(service)
                service._backend_bundle = resolved
                service._meter_backend = resolved.meter
                service._switch_backend = resolved.switch
                service._charger_backend = resolved.charger

                controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

                relay_on, power, current, confirmed = controller.components.relay.status.apply_relay_decision(
                    service,
                    True,
                    False,
                    {"output": False, "_pm_confirmed": True},
                    0.0,
                    0.0,
                    100.0,
                    False,
                )

                self.assertEqual((relay_on, power, current, confirmed), (True, 0.0, 0.0, False))
                self.assertEqual(fake_transport.holding_registers[0x0005], 1)
                service._queue_relay_command.assert_not_called()
                service._publish_local_pm_status.assert_called_once_with(True, 100.0)

                updated = controller.components.state.apply_startup_manual_target(
                    {"output": True, "apower": 1200.0, "current": 5.2},
                    123.0,
                )

            self.assertEqual(fake_transport.holding_registers[0x0005], 0)
            self.assertEqual(updated, {"output": False, "at": 123.0})
            self.assertIsNone(service._startup_manual_target)

    def test_orchestrate_pending_phase_switch_resumes_native_charger_after_stabilization(self):
        charger_backend = SimpleNamespace(set_enabled=MagicMock())
        service = SimpleNamespace(
            _phase_switch_pending_selection="P1_P2",
            _phase_switch_state="stabilizing",
            _phase_switch_requested_at=98.0,
            _phase_switch_stable_until=99.0,
            _phase_switch_resume_relay=True,
            _charger_backend=charger_backend,
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _save_runtime_state=MagicMock(),
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        relay_on, power, current, confirmed, desired_override = controller.components.relay.foundation.phase_switch.orchestrate_pending_phase_switch(
            service,
            {"output": False, "_phase_selection": "P1_P2"},
            False,
            0.0,
            0.0,
            True,
            100.0,
            False,
        )

        self.assertTrue(relay_on)
        self.assertEqual(power, 0.0)
        self.assertEqual(current, 0.0)
        self.assertFalse(confirmed)
        self.assertIsNone(desired_override)
        charger_backend.set_enabled.assert_called_once_with(True)
        service._queue_relay_command.assert_not_called()
        service._publish_local_pm_status.assert_called_once_with(True, 100.0)
        self.assertIsNone(service._phase_switch_pending_selection)
        self.assertIsNone(service._phase_switch_state)
        self.assertFalse(service._phase_switch_resume_relay)

    def test_orchestrate_pending_phase_switch_allows_auto_resume_after_stabilization(self):
        service = SimpleNamespace(
            _phase_switch_pending_selection="P1_P2",
            _phase_switch_state="stabilizing",
            _phase_switch_requested_at=98.0,
            _phase_switch_stable_until=99.0,
            _phase_switch_resume_relay=True,
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _ignore_min_offtime_once=False,
            _save_runtime_state=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        relay_on, power, current, confirmed, desired_override = controller.components.relay.foundation.phase_switch.orchestrate_pending_phase_switch(
            service,
            {"output": False, "_phase_selection": "P1_P2"},
            False,
            0.0,
            0.0,
            True,
            100.0,
            True,
        )

        self.assertFalse(relay_on)
        self.assertEqual(power, 0.0)
        self.assertEqual(current, 0.0)
        self.assertTrue(confirmed)
        self.assertIsNone(desired_override)
        self.assertTrue(service._ignore_min_offtime_once)
        self.assertIsNone(service._phase_switch_pending_selection)
        self.assertIsNone(service._phase_switch_state)

    def test_orchestrate_pending_phase_switch_waits_for_observed_phase_match(self):
        service = SimpleNamespace(
            _phase_switch_pending_selection="P1_P2",
            _phase_switch_state="stabilizing",
            _phase_switch_requested_at=98.0,
            _phase_switch_stable_until=99.0,
            _phase_switch_resume_relay=True,
            requested_phase_selection="P1_P2",
            active_phase_selection="P1",
            auto_shelly_soft_fail_seconds=10.0,
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _save_runtime_state=MagicMock(),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
            _set_health=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        relay_on, power, current, confirmed, desired_override = controller.components.relay.foundation.phase_switch.orchestrate_pending_phase_switch(
            service,
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
        self.assertEqual(service.active_phase_selection, "P1")
        self.assertEqual(service._phase_switch_state, "stabilizing")
        service._queue_relay_command.assert_not_called()
        service._set_health.assert_not_called()

    def test_orchestrate_pending_phase_switch_marks_mismatch_after_timeout(self):
        service = SimpleNamespace(
            _phase_switch_pending_selection="P1_P2",
            _phase_switch_state="stabilizing",
            _phase_switch_requested_at=80.0,
            _phase_switch_stable_until=81.0,
            _phase_switch_resume_relay=True,
            requested_phase_selection="P1_P2",
            active_phase_selection="P1",
            auto_shelly_soft_fail_seconds=10.0,
            _phase_switch_mismatch_counts={},
            _phase_switch_lockout_selection=None,
            _phase_switch_lockout_reason="",
            _phase_switch_lockout_at=None,
            _phase_switch_lockout_until=None,
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _save_runtime_state=MagicMock(),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
            _set_health=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        relay_on, power, current, confirmed, desired_override = controller.components.relay.foundation.phase_switch.orchestrate_pending_phase_switch(
            service,
            {"output": False, "_phase_selection": "P1"},
            False,
            0.0,
            0.0,
            True,
            100.0,
            False,
        )

        self.assertTrue(relay_on)
        self.assertEqual(power, 0.0)
        self.assertEqual(current, 0.0)
        self.assertFalse(confirmed)
        self.assertIsNone(desired_override)
        self.assertEqual(service.active_phase_selection, "P1")
        self.assertEqual(service.requested_phase_selection, "P1")
        self.assertIsNone(service._phase_switch_state)
        self.assertIsNone(service._phase_switch_pending_selection)
        service._queue_relay_command.assert_called_once_with(True, 100.0)
        service._publish_local_pm_status.assert_called_once_with(True, 100.0)
        service._mark_failure.assert_called_once_with("shelly")
        service._set_health.assert_called_once_with("phase-switch-mismatch", cached=False)
        service._warning_throttled.assert_called_once()
        self.assertFalse(service._phase_switch_mismatch_active)
        self.assertEqual(service._phase_switch_mismatch_counts["P1_P2"], 1)
        self.assertEqual(service._phase_switch_last_mismatch_selection, "P1_P2")
        self.assertEqual(service._phase_switch_last_mismatch_at, 100.0)
        self.assertIsNone(service._phase_switch_lockout_selection)

    def test_orchestrate_pending_phase_switch_engages_lockout_after_repeated_mismatches(self):
        service = SimpleNamespace(
            _phase_switch_pending_selection="P1_P2",
            _phase_switch_state="stabilizing",
            _phase_switch_requested_at=80.0,
            _phase_switch_stable_until=81.0,
            _phase_switch_resume_relay=True,
            requested_phase_selection="P1_P2",
            active_phase_selection="P1",
            auto_shelly_soft_fail_seconds=10.0,
            auto_policy=_phase_policy(
                mismatch_lockout_count=3,
                mismatch_lockout_seconds=60.0,
            ),
            _phase_switch_mismatch_counts={"P1_P2": 2},
            _phase_switch_lockout_selection=None,
            _phase_switch_lockout_reason="",
            _phase_switch_lockout_at=None,
            _phase_switch_lockout_until=None,
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _save_runtime_state=MagicMock(),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
            _set_health=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        relay_on, power, current, confirmed, desired_override = controller.components.relay.foundation.phase_switch.orchestrate_pending_phase_switch(
            service,
            {"output": False, "_phase_selection": "P1"},
            False,
            0.0,
            0.0,
            True,
            100.0,
            False,
        )

        self.assertTrue(relay_on)
        self.assertEqual(power, 0.0)
        self.assertEqual(current, 0.0)
        self.assertFalse(confirmed)
        self.assertIsNone(desired_override)
        self.assertEqual(service.requested_phase_selection, "P1")
        self.assertEqual(service.active_phase_selection, "P1")
        self.assertEqual(service._phase_switch_mismatch_counts["P1_P2"], 3)
        self.assertEqual(service._phase_switch_lockout_selection, "P1_P2")
        self.assertEqual(service._phase_switch_lockout_reason, "mismatch-threshold")
        self.assertEqual(service._phase_switch_lockout_at, 100.0)
        self.assertEqual(service._phase_switch_lockout_until, 160.0)

    def test_phase_change_scenario_repeated_feedback_mismatches_escalate_to_lockout(self):
        service = SimpleNamespace(
            _phase_switch_pending_selection="P1_P2",
            _phase_switch_state="stabilizing",
            _phase_switch_requested_at=80.0,
            _phase_switch_stable_until=81.0,
            _phase_switch_resume_relay=True,
            requested_phase_selection="P1_P2",
            active_phase_selection="P1",
            auto_shelly_soft_fail_seconds=10.0,
            auto_policy=_phase_policy(
                mismatch_lockout_count=2,
                mismatch_lockout_seconds=60.0,
            ),
            _phase_switch_mismatch_counts={},
            _phase_switch_lockout_selection=None,
            _phase_switch_lockout_reason="",
            _phase_switch_lockout_at=None,
            _phase_switch_lockout_until=None,
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _save_runtime_state=MagicMock(),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
            _set_health=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        controller.components.relay.foundation.phase_switch.orchestrate_pending_phase_switch(
            service,
            {"output": False, "_phase_selection": "P1"},
            False,
            0.0,
            0.0,
            True,
            100.0,
            False,
        )
        self.assertEqual(service._phase_switch_mismatch_counts, {"P1_P2": 1})
        self.assertIsNone(service._phase_switch_lockout_selection)

        service._phase_switch_pending_selection = "P1_P2"
        service._phase_switch_state = "stabilizing"
        service._phase_switch_requested_at = 140.0
        service._phase_switch_stable_until = 141.0
        service._phase_switch_resume_relay = True
        service.requested_phase_selection = "P1_P2"

        controller.components.relay.foundation.phase_switch.orchestrate_pending_phase_switch(
            service,
            {"output": False, "_phase_selection": "P1"},
            False,
            0.0,
            0.0,
            True,
            170.0,
            False,
        )

        self.assertEqual(service._phase_switch_mismatch_counts, {"P1_P2": 2})
        self.assertEqual(service._phase_switch_lockout_selection, "P1_P2")
        self.assertEqual(service._phase_switch_lockout_reason, "mismatch-threshold")
        self.assertEqual(service._phase_switch_lockout_at, 170.0)
        self.assertEqual(service._phase_switch_lockout_until, 230.0)
