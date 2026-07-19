# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_update_cycle_controller_support import *


class TestUpdateCycleControllerQuinary(UpdateCycleControllerTestBase):
    def test_orchestrate_pending_phase_switch_resumes_manual_relay_after_stabilization(self):
        service = SimpleNamespace(
            _phase_switch_pending_selection="P1_P2",
            _phase_switch_state="stabilizing",
            _phase_switch_requested_at=98.0,
            _phase_switch_stable_until=99.0,
            _phase_switch_resume_relay=True,
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
        service._queue_relay_command.assert_called_once_with(True, 100.0)
        service._publish_local_pm_status.assert_called_once_with(True, 100.0)
        self.assertIsNone(service._phase_switch_pending_selection)
        self.assertIsNone(service._phase_switch_state)
        self.assertFalse(service._phase_switch_resume_relay)

    def test_auto_phase_switch_end_to_end_with_simpleevse_and_switch_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            phase1_path = self._write_config(
                temp_dir,
                "phase1-switch.ini",
                "[Adapter]\nType=template_switch\nBaseUrl=http://phase1.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1\nSwitchingMode=contactor\nRequiresChargePauseForPhaseChange=1\n"
                "[StateRequest]\nMethod=GET\nUrl=/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nMethod=POST\nUrl=/control?on=${enabled_int}\n",
            )
            phase2_path = self._write_config(
                temp_dir,
                "phase2-switch.ini",
                "[Adapter]\nType=template_switch\nBaseUrl=http://phase2.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1\n"
                "[StateRequest]\nMethod=GET\nUrl=/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nMethod=POST\nUrl=/control?on=${enabled_int}\n",
            )
            phase3_path = self._write_config(
                temp_dir,
                "phase3-switch.ini",
                "[Adapter]\nType=template_switch\nBaseUrl=http://phase3.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1\n"
                "[StateRequest]\nMethod=GET\nUrl=/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nMethod=POST\nUrl=/control?on=${enabled_int}\n",
            )
            switch_path = self._write_config(
                temp_dir,
                "switch-group.ini",
                "[Adapter]\nType=switch_group\n"
                f"[Members]\nP1={Path(phase1_path).name}\nP2={Path(phase2_path).name}\nP3={Path(phase3_path).name}\n",
            )
            charger_path = self._write_config(
                temp_dir,
                "charger.ini",
                "[Adapter]\nType=simpleevse_charger\nTransport=serial_rtu\n"
                "[Transport]\nDevice=/dev/ttyUSB0\nBaudrate=9600\nParity=N\nStopBits=1\nUnitId=1\n",
            )
            session = MagicMock()
            session.post.return_value = _FakeTemplateResponse()
            service = _auto_phase_service(
                _last_confirmed_pm_status={"output": True},
                _last_confirmed_pm_status_at=99.5,
                _auto_phase_target_candidate="P1_P2",
                _auto_phase_target_since=80.0,
                backend_mode="split",
                meter_backend_type="none",
                switch_backend_type="switch_group",
                charger_backend_type="simpleevse_charger",
                meter_backend_config_path="",
                switch_backend_config_path=str(switch_path),
                charger_backend_config_path=str(charger_path),
                phase="L1",
                host="192.168.1.20",
                pm_component="Switch",
                pm_id=0,
                max_current=16.0,
                session=session,
                use_digest_auth=False,
                username="",
                password="",
                shelly_request_timeout_seconds=2.0,
            )

            sync_backend_runtime_test_service(service)
            sync_readback_test_service(service)
            resolved = build_service_backends(service)
            service._backend_bundle = resolved
            service._meter_backend = resolved.meter
            service._switch_backend = resolved.switch
            service._charger_backend = resolved.charger
            service.supported_phase_selections = resolved.switch.capabilities().supported_phase_selections

            io_controller = ShellyIoController(service)
            service._phase_selection_requires_pause = io_controller.phase_selection_requires_pause
            service._apply_phase_selection = io_controller.set_phase_selection

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

            relay_on, power, current, confirmed, desired_override = controller.components.relay.foundation.phase_switch.orchestrate_pending_phase_switch(
                service,
                {"output": False, "_phase_selection": "P1"},
                False,
                0.0,
                0.0,
                True,
                102.0,
                True,
            )

            self.assertFalse(relay_on)
            self.assertEqual(power, 0.0)
            self.assertEqual(current, 0.0)
            self.assertFalse(confirmed)
            self.assertFalse(desired_override)
            self.assertEqual(service.requested_phase_selection, "P1_P2")
            self.assertEqual(service._phase_switch_state, "stabilizing")

            relay_on, power, current, confirmed, desired_override = controller.components.relay.foundation.phase_switch.orchestrate_pending_phase_switch(
                service,
                {"output": False, "_phase_selection": "P1_P2"},
                False,
                0.0,
                0.0,
                True,
                105.0,
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

            session.post.reset_mock()
            self.assertEqual(io_controller.set_relay(True), {"output": True})

            urls = [call.kwargs["url"] for call in session.post.call_args_list]
            self.assertCountEqual(
                urls,
                [
                    "http://phase1.local/control?on=1",
                    "http://phase2.local/control?on=1",
                    "http://phase3.local/control?on=0",
                ],
            )

    def test_auto_phase_switch_end_to_end_with_smartevse_and_switch_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            phase1_path = self._write_config(
                temp_dir,
                "phase1-switch.ini",
                "[Adapter]\nType=template_switch\nBaseUrl=http://phase1.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1\nSwitchingMode=contactor\nRequiresChargePauseForPhaseChange=1\n"
                "[StateRequest]\nMethod=GET\nUrl=/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nMethod=POST\nUrl=/control?on=${enabled_int}\n",
            )
            phase2_path = self._write_config(
                temp_dir,
                "phase2-switch.ini",
                "[Adapter]\nType=template_switch\nBaseUrl=http://phase2.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1\n"
                "[StateRequest]\nMethod=GET\nUrl=/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nMethod=POST\nUrl=/control?on=${enabled_int}\n",
            )
            phase3_path = self._write_config(
                temp_dir,
                "phase3-switch.ini",
                "[Adapter]\nType=template_switch\nBaseUrl=http://phase3.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1\n"
                "[StateRequest]\nMethod=GET\nUrl=/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nMethod=POST\nUrl=/control?on=${enabled_int}\n",
            )
            switch_path = self._write_config(
                temp_dir,
                "switch-group.ini",
                "[Adapter]\nType=switch_group\n"
                f"[Members]\nP1={Path(phase1_path).name}\nP2={Path(phase2_path).name}\nP3={Path(phase3_path).name}\n",
            )
            charger_path = self._write_config(
                temp_dir,
                "charger.ini",
                "[Adapter]\nType=smartevse_charger\nTransport=serial_rtu\n"
                "[Transport]\nDevice=/dev/ttyUSB0\nBaudrate=9600\nParity=N\nStopBits=1\nUnitId=1\n",
            )
            session = MagicMock()
            session.post.return_value = _FakeTemplateResponse()
            service = _auto_phase_service(
                _last_confirmed_pm_status={"output": True},
                _last_confirmed_pm_status_at=99.5,
                _auto_phase_target_candidate="P1_P2",
                _auto_phase_target_since=80.0,
                backend_mode="split",
                meter_backend_type="none",
                switch_backend_type="switch_group",
                charger_backend_type="smartevse_charger",
                meter_backend_config_path="",
                switch_backend_config_path=str(switch_path),
                charger_backend_config_path=str(charger_path),
                phase="L1",
                host="192.168.1.20",
                pm_component="Switch",
                pm_id=0,
                max_current=16.0,
                session=session,
                use_digest_auth=False,
                username="",
                password="",
                shelly_request_timeout_seconds=2.0,
            )

            sync_backend_runtime_test_service(service)
            sync_readback_test_service(service)
            resolved = build_service_backends(service)
            service._backend_bundle = resolved
            service._meter_backend = resolved.meter
            service._switch_backend = resolved.switch
            service._charger_backend = resolved.charger
            service.supported_phase_selections = resolved.switch.capabilities().supported_phase_selections

            io_controller = ShellyIoController(service)
            service._phase_selection_requires_pause = io_controller.phase_selection_requires_pause
            service._apply_phase_selection = io_controller.set_phase_selection

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

            relay_on, power, current, confirmed, desired_override = controller.components.relay.foundation.phase_switch.orchestrate_pending_phase_switch(
                service,
                {"output": False, "_phase_selection": "P1"},
                False,
                0.0,
                0.0,
                True,
                102.0,
                True,
            )

            self.assertFalse(relay_on)
            self.assertEqual(power, 0.0)
            self.assertEqual(current, 0.0)
            self.assertFalse(confirmed)
            self.assertFalse(desired_override)
            self.assertEqual(service.requested_phase_selection, "P1_P2")
            self.assertEqual(service._phase_switch_state, "stabilizing")

            relay_on, power, current, confirmed, desired_override = controller.components.relay.foundation.phase_switch.orchestrate_pending_phase_switch(
                service,
                {"output": False, "_phase_selection": "P1_P2"},
                False,
                0.0,
                0.0,
                True,
                105.0,
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

            session.post.reset_mock()
            self.assertEqual(io_controller.set_relay(True), {"output": True})

            urls = [call.kwargs["url"] for call in session.post.call_args_list]
            self.assertCountEqual(
                urls,
                [
                    "http://phase1.local/control?on=1",
                    "http://phase2.local/control?on=1",
                    "http://phase3.local/control?on=0",
                ],
            )
