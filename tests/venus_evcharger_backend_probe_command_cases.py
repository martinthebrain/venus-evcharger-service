# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_backend_probe_support import (
    BackendProbeTestCase,
    MagicMock,
    _FakeResponse,
    io,
    json,
    main,
    patch,
    redirect_stdout,
    tempfile,
)
from venus_evcharger.backend.probe import _probe_command_payload


class TestShellyWallboxBackendProbeCommands(BackendProbeTestCase):
    def test_probe_command_payload_requires_mapping_payloads(self) -> None:
        with patch("venus_evcharger.backend.probe.validate_backend_config", return_value=[]):
            with self.assertRaisesRegex(TypeError, "must return dict"):
                _probe_command_payload("validate", "/tmp/config.ini")

    def test_probe_meter_command_prints_normalized_meter_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            meter_path = self._write_config(
                temp_dir,
                "meter.ini",
                "[Adapter]\nType=shelly_meter\nHost=192.0.2.10\nShellyProfile=em_3phase_profiled\n"
                "[Phase]\nMeasuredPhaseSelection=P1_P2_P3\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse(
                {
                    "a_act_power": 1150.0,
                    "b_act_power": 1150.0,
                    "c_act_power": 1150.0,
                    "a_current": 5.0,
                    "b_current": 5.0,
                    "c_current": 5.0,
                    "a_voltage": 229.0,
                    "b_voltage": 230.0,
                    "c_voltage": 231.0,
                    "a_total_act_energy": 2000.0,
                    "b_total_act_energy": 2000.0,
                    "c_total_act_energy": 2789.0,
                }
            )

            stdout = io.StringIO()
            with patch("venus_evcharger.backend.probe.requests.Session", return_value=session):
                with redirect_stdout(stdout):
                    rc = main(["probe-meter", meter_path])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["type"], "shelly_meter")
            self.assertEqual(payload["shelly_profile"], "em_3phase_profiled")
            self.assertEqual(payload["component"], "EM")
            self.assertEqual(payload["device_id"], 0)
            self.assertEqual(payload["meter"]["phase_selection"], "P1_P2_P3")
            self.assertEqual(payload["meter"]["phase_powers_w"], [1150.0, 1150.0, 1150.0])

    def test_probe_template_meter_prints_normalized_meter_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            meter_path = self._write_config(
                temp_dir,
                "meter.ini",
                "[Adapter]\nType=template_meter\nBaseUrl=http://adapter.local\n"
                "[Phase]\nMeasuredPhaseSelection=P1\n"
                "[MeterRequest]\nUrl=/meter/state\n"
                "[MeterResponse]\nPowerPath=data.power_w\nCurrentPath=data.current_a\n"
                "VoltagePath=data.voltage_v\nEnergyKwhPath=data.energy_kwh\n"
                "PhaseSelectionPath=data.phase_selection\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse(
                {
                    "data": {
                        "power_w": 3450.0,
                        "current_a": 15.0,
                        "voltage_v": 230.0,
                        "energy_kwh": 6.789,
                        "phase_selection": "P1_P2_P3",
                    }
                }
            )

            stdout = io.StringIO()
            with patch("venus_evcharger.backend.probe.requests.Session", return_value=session):
                with redirect_stdout(stdout):
                    rc = main(["probe-meter", meter_path])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["type"], "template_meter")
            self.assertEqual(payload["meter"]["phase_selection"], "P1_P2_P3")
            self.assertEqual(payload["meter"]["phase_powers_w"], [1150.0, 1150.0, 1150.0])

    def test_probe_switch_command_prints_capabilities_and_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            switch_path = self._write_config(
                temp_dir,
                "switch.ini",
                "[Adapter]\nType=shelly_switch\nHost=192.0.2.11\nShellyProfile=switch_1ch_with_pm\nId=1\n"
                "[Capabilities]\nSwitchingMode=contactor\nSupportedPhaseSelections=P1,P1_P2_P3\n"
                "RequiresChargePauseForPhaseChange=1\n"
                "[PhaseMap]\nP1=1\nP1_P2_P3=1,2,3\n",
            )
            session = MagicMock()
            session.get.side_effect = [
                _FakeResponse({"output": False}),
                _FakeResponse({"output": False}),
                _FakeResponse({"output": False}),
            ]

            stdout = io.StringIO()
            with patch("venus_evcharger.backend.probe.requests.Session", return_value=session):
                with redirect_stdout(stdout):
                    rc = main(["probe-switch", switch_path])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["type"], "shelly_switch")
            self.assertEqual(payload["shelly_profile"], "switch_1ch_with_pm")
            self.assertEqual(payload["component"], "Switch")
            self.assertEqual(payload["device_id"], 1)
            self.assertEqual(payload["capabilities"]["switching_mode"], "contactor")
            self.assertEqual(payload["capabilities"]["supported_phase_selections"], ["P1", "P1_P2_P3"])
            self.assertEqual(payload["phase_switch_targets"], {"P1": [1], "P1_P2_P3": [1, 2, 3]})
            self.assertFalse(payload["switch_state"]["enabled"])

    def test_probe_contactor_switch_defaults_to_contactor_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            switch_path = self._write_config(
                temp_dir,
                "switch.ini",
                "[Adapter]\nType=shelly_contactor_switch\nHost=192.0.2.11\nComponent=Switch\nId=1\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse({"output": False})

            stdout = io.StringIO()
            with patch("venus_evcharger.backend.probe.requests.Session", return_value=session):
                with redirect_stdout(stdout):
                    rc = main(["probe-switch", switch_path])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["type"], "shelly_contactor_switch")
            self.assertEqual(payload["capabilities"]["switching_mode"], "contactor")
            self.assertIsNone(payload["capabilities"]["max_direct_switch_power_w"])

    def test_probe_switch_prints_native_feedback_and_interlock_readback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            switch_path = self._write_config(
                temp_dir,
                "switch.ini",
                "[Adapter]\nType=shelly_contactor_switch\nHost=192.0.2.11\nComponent=Switch\nId=1\n"
                "[Feedback]\nComponent=Input\nId=7\nValuePath=state\n"
                "[Interlock]\nComponent=Input\nId=8\nValuePath=state\nInvert=1\n",
            )
            session = MagicMock()
            session.get.side_effect = [
                _FakeResponse({"output": True}),
                _FakeResponse({"state": True}),
                _FakeResponse({"state": False}),
            ]

            stdout = io.StringIO()
            with patch("venus_evcharger.backend.probe.requests.Session", return_value=session):
                with redirect_stdout(stdout):
                    rc = main(["probe-switch", switch_path])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["switch_state"]["feedback_closed"], True)
            self.assertEqual(payload["switch_state"]["interlock_ok"], True)
            self.assertEqual(payload["feedback_readback"]["component"], "Input")
            self.assertEqual(payload["feedback_readback"]["device_id"], 7)
            self.assertEqual(payload["feedback_readback"]["value_path"], "state")
            self.assertEqual(payload["interlock_readback"]["component"], "Input")
            self.assertEqual(payload["interlock_readback"]["device_id"], 8)
            self.assertEqual(payload["interlock_readback"]["invert"], True)

    def test_probe_switch_group_prints_phase_member_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            self._write_config(
                temp_dir,
                "phase1-switch.ini",
                "[Adapter]\nType=template_switch\nBaseUrl=http://phase1.local\n"
                "[StateRequest]\nUrl=/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nUrl=/control\n",
            )
            self._write_config(
                temp_dir,
                "phase2-switch.ini",
                "[Adapter]\nType=shelly_switch\nHost=192.0.2.22\nComponent=Switch\nId=0\n",
            )
            self._write_config(
                temp_dir,
                "phase3-switch.ini",
                "[Adapter]\nType=template_switch\nBaseUrl=http://phase3.local\n"
                "[StateRequest]\nUrl=/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nUrl=/control\n",
            )
            switch_path = self._write_config(
                temp_dir,
                "switch.ini",
                "[Adapter]\nType=switch_group\n"
                "[Members]\nP1=phase1-switch.ini\nP2=phase2-switch.ini\nP3=phase3-switch.ini\n",
            )
            session = MagicMock()
            session.get.side_effect = [
                _FakeResponse({"enabled": True}),
                _FakeResponse({"output": True}),
                _FakeResponse({"enabled": True}),
            ]

            stdout = io.StringIO()
            with patch("venus_evcharger.backend.probe.requests.Session", return_value=session):
                with redirect_stdout(stdout):
                    rc = main(["probe-switch", switch_path])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["type"], "switch_group")
            self.assertEqual(
                payload["phase_switch_targets"],
                {"P1": ["P1"], "P1_P2": ["P1", "P2"], "P1_P2_P3": ["P1", "P2", "P3"]},
            )
            self.assertEqual(payload["phase_members"]["P2"]["backend_type"], "shelly_switch")
            self.assertEqual(payload["switch_state"]["phase_selection"], "P1_P2_P3")
            self.assertTrue(payload["switch_state"]["enabled"])

    def test_probe_template_switch_prints_normalized_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            switch_path = self._write_config(
                temp_dir,
                "switch.ini",
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2_P3\n"
                "[StateRequest]\nUrl=/switch/state\n"
                "[StateResponse]\nEnabledPath=data.enabled\nPhaseSelectionPath=data.phase_selection\n"
                "[CommandRequest]\nUrl=/switch/control\n"
                "[PhaseRequest]\nUrl=/switch/phase\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse({"data": {"enabled": True, "phase_selection": "P1_P2_P3"}})

            stdout = io.StringIO()
            with patch("venus_evcharger.backend.probe.requests.Session", return_value=session):
                with redirect_stdout(stdout):
                    rc = main(["probe-switch", switch_path])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["type"], "template_switch")
            self.assertEqual(payload["capabilities"]["supported_phase_selections"], ["P1", "P1_P2_P3"])
            self.assertEqual(payload["switch_state"]["phase_selection"], "P1_P2_P3")
            self.assertTrue(payload["switch_state"]["enabled"])

    def test_probe_template_charger_prints_normalized_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = self._write_config(
                temp_dir,
                "charger.ini",
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2_P3\n"
                "[StateRequest]\nUrl=/charger/state\n"
                "[StateResponse]\nActualCurrentPath=data.actual_current\n"
                "PowerWattsPath=data.power_w\nEnergyKwhPath=data.energy_kwh\n"
                "StatusPath=data.status\nFaultPath=data.fault\n"
                "[EnableRequest]\nUrl=/charger/enable\n"
                "[CurrentRequest]\nUrl=/charger/current\n"
                "[PhaseRequest]\nUrl=/charger/phase\n",
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = main(["probe-charger", charger_path])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["type"], "template_charger")
            self.assertEqual(payload["supported_phase_selections"], ["P1", "P1_P2_P3"])
            self.assertEqual(payload["state_url"], "http://adapter.local/charger/state")
            self.assertEqual(payload["state_actual_current_path"], "data.actual_current")
            self.assertEqual(payload["state_power_watts_path"], "data.power_w")
            self.assertEqual(payload["state_energy_kwh_path"], "data.energy_kwh")
            self.assertEqual(payload["state_status_path"], "data.status")
            self.assertEqual(payload["state_fault_path"], "data.fault")
            self.assertEqual(payload["enable_url"], "http://adapter.local/charger/enable")
            self.assertEqual(payload["current_url"], "http://adapter.local/charger/current")
            self.assertEqual(payload["phase_url"], "http://adapter.local/charger/phase")

    def test_probe_goe_charger_prints_normalized_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = self._write_config(
                temp_dir,
                "charger.ini",
                "[Adapter]\nType=goe_charger\nBaseUrl=http://goe.local\n",
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = main(["probe-charger", charger_path])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["type"], "goe_charger")
            self.assertEqual(payload["supported_phase_selections"], ["P1"])
            self.assertEqual(payload["state_url"], "http://goe.local/api/status")
            self.assertEqual(payload["enable_url"], "http://goe.local/api/set")
            self.assertEqual(payload["current_url"], "http://goe.local/api/set")
            self.assertIsNone(payload["phase_url"])

    def test_validate_wallbox_command_prints_normalized_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = self._write_config(
                temp_dir,
                "charger.ini",
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[EnableRequest]\nUrl=/charger/enable\n"
                "[CurrentRequest]\nUrl=/charger/current\n",
            )
            wallbox_path = self._write_config(
                temp_dir,
                "config.ini",
                "[DEFAULT]\nHost=192.0.2.20\n"
                "[Backends]\nMode=split\nMeterType=none\nSwitchType=none\n"
                f"ChargerType=template_charger\nChargerConfigPath={charger_path}\n",
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = main(["validate-wallbox", wallbox_path])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["runtime"]["backend_mode"], "split")
            self.assertIsNone(payload["runtime"]["meter_type"])
            self.assertIsNone(payload["runtime"]["switch_type"])
            self.assertEqual(payload["runtime"]["charger_type"], "template_charger")
            self.assertEqual(payload["selection"]["mode"], "split")
            self.assertEqual(payload["selection"]["meter_type"], "none")
            self.assertEqual(payload["selection"]["switch_type"], "none")
            self.assertEqual(payload["selection"]["charger_type"], "template_charger")
            self.assertEqual(payload["resolved_roles"], {"meter": False, "switch": False, "charger": True})
