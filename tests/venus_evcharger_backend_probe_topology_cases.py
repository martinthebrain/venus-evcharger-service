# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_backend_probe_support import BackendProbeTestCase, Path, cast, tempfile, validate_wallbox_config


class TestShellyWallboxBackendProbeTopology(BackendProbeTestCase):
    def test_validate_wallbox_config_accepts_meterless_split_with_charger(self) -> None:
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
                "[Backends]\nMode=split\nMeterType=none\nSwitchType=shelly_combined\n"
                f"ChargerType=template_charger\nChargerConfigPath={charger_path}\n",
            )

            payload = validate_wallbox_config(wallbox_path)
            runtime = cast(dict[str, object], payload["runtime"])
            selection = cast(dict[str, object], payload["selection"])

            self.assertEqual(runtime["backend_mode"], "split")
            self.assertIsNone(runtime["meter_type"])
            self.assertEqual(runtime["charger_type"], "template_charger")
            self.assertTrue(cast(bool, runtime["topology_configured"]))
            self.assertFalse(cast(bool, runtime["primary_rpc_configured"]))
            self.assertEqual(selection["mode"], "split")
            self.assertEqual(selection["meter_type"], "none")
            self.assertEqual(selection["charger_type"], "template_charger")
            self.assertEqual(payload["resolved_roles"], {"meter": False, "switch": True, "charger": True})

    def test_validate_wallbox_config_accepts_simpleevse_plus_switch_group_topology(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            phase1_path = self._write_config(
                temp_dir,
                "phase1-switch.ini",
                "[Adapter]\nType=template_switch\nBaseUrl=http://phase1.local\n"
                "[StateRequest]\nUrl=/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nUrl=/control\n",
            )
            phase2_path = self._write_config(
                temp_dir,
                "phase2-switch.ini",
                "[Adapter]\nType=shelly_switch\nHost=192.0.2.61\n",
            )
            phase3_path = self._write_config(
                temp_dir,
                "phase3-switch.ini",
                "[Adapter]\nType=shelly_contactor_switch\nHost=192.0.2.62\n",
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
            wallbox_path = self._write_config(
                temp_dir,
                "config.ini",
                "[DEFAULT]\nHost=192.0.2.20\n"
                "[Backends]\nMode=split\nMeterType=none\nSwitchType=switch_group\n"
                f"SwitchConfigPath={switch_path}\n"
                "ChargerType=simpleevse_charger\n"
                f"ChargerConfigPath={charger_path}\n",
            )

            payload = validate_wallbox_config(wallbox_path)
            selection = cast(dict[str, object], payload["selection"])

            self.assertEqual(selection["mode"], "split")
            self.assertEqual(selection["meter_type"], "none")
            self.assertEqual(selection["switch_type"], "switch_group")
            self.assertEqual(selection["charger_type"], "simpleevse_charger")
            self.assertEqual(payload["resolved_roles"], {"meter": False, "switch": True, "charger": True})

    def test_validate_wallbox_config_accepts_smartevse_plus_switch_group_topology(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            phase1_path = self._write_config(
                temp_dir,
                "phase1-switch.ini",
                "[Adapter]\nType=template_switch\nBaseUrl=http://phase1.local\n"
                "[StateRequest]\nUrl=/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nUrl=/control\n",
            )
            phase2_path = self._write_config(
                temp_dir,
                "phase2-switch.ini",
                "[Adapter]\nType=shelly_switch\nHost=192.0.2.61\n",
            )
            phase3_path = self._write_config(
                temp_dir,
                "phase3-switch.ini",
                "[Adapter]\nType=shelly_contactor_switch\nHost=192.0.2.62\n",
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
            wallbox_path = self._write_config(
                temp_dir,
                "config.ini",
                "[DEFAULT]\nHost=192.0.2.20\n"
                "[Backends]\nMode=split\nMeterType=none\nSwitchType=switch_group\n"
                f"SwitchConfigPath={switch_path}\n"
                "ChargerType=smartevse_charger\n"
                f"ChargerConfigPath={charger_path}\n",
            )

            payload = validate_wallbox_config(wallbox_path)
            selection = cast(dict[str, object], payload["selection"])

            self.assertEqual(selection["mode"], "split")
            self.assertEqual(selection["meter_type"], "none")
            self.assertEqual(selection["switch_type"], "switch_group")
            self.assertEqual(selection["charger_type"], "smartevse_charger")
            self.assertEqual(payload["resolved_roles"], {"meter": False, "switch": True, "charger": True})

    def test_validate_wallbox_config_rejects_invalid_meterless_split_without_charger(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wallbox_path = self._write_config(
                temp_dir,
                "config.ini",
                "[DEFAULT]\nHost=192.0.2.20\n"
                "[Backends]\nMode=split\nMeterType=none\nSwitchType=shelly_combined\nChargerType=\n",
            )

            with self.assertRaisesRegex(ValueError, "MeterType=none requires a configured charger backend"):
                validate_wallbox_config(wallbox_path)

    def test_validate_wallbox_config_rejects_meterless_combined_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wallbox_path = self._write_config(
                temp_dir,
                "config.ini",
                "[DEFAULT]\nHost=192.0.2.20\n"
                "[Backends]\nMode=combined\nMeterType=none\nSwitchType=shelly_combined\nChargerType=\n",
            )

            with self.assertRaisesRegex(ValueError, "MeterType=none is only supported in split backend mode"):
                validate_wallbox_config(wallbox_path)

    def test_validate_wallbox_config_rejects_switchless_combined_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wallbox_path = self._write_config(
                temp_dir,
                "config.ini",
                "[DEFAULT]\nHost=192.0.2.20\n"
                "[Backends]\nMode=combined\nMeterType=shelly_combined\nSwitchType=none\nChargerType=\n",
            )

            with self.assertRaisesRegex(ValueError, "SwitchType=none is only supported in split backend mode"):
                validate_wallbox_config(wallbox_path)

    def test_validate_wallbox_config_accepts_representative_backend_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            switch_path = self._write_config(
                temp_dir,
                "switch.ini",
                "[Adapter]\nType=shelly_switch\nHost=192.0.2.21\nShellyProfile=switch_1ch_with_pm\n",
            )
            meter_path = self._write_config(
                temp_dir,
                "meter.ini",
                "[Adapter]\nType=shelly_meter\nHost=192.0.2.22\nShellyProfile=em1_meter_single_or_dual\n",
            )
            modbus_charger_path = self._write_config(
                temp_dir,
                "modbus-charger.ini",
                "[Adapter]\nType=modbus_charger\nProfile=generic\nTransport=serial_rtu\n"
                "[Transport]\nDevice=/dev/ttyUSB0\nBaudrate=9600\nParity=N\nStopBits=1\nUnitId=7\n"
                "[EnableWrite]\nRegisterType=coil\nAddress=20\nTrueValue=1\nFalseValue=0\n"
                "[CurrentWrite]\nRegisterType=holding\nAddress=30\nDataType=uint16\nScale=10\n",
            )
            goe_charger_path = self._write_config(
                temp_dir,
                "goe-charger.ini",
                "[Adapter]\nType=goe_charger\nBaseUrl=http://goe.local\n",
            )
            cases = (
                (
                    "combined-default",
                    "[DEFAULT]\nHost=192.0.2.20\n",
                    {"mode": "combined", "meter_type": "shelly_meter", "switch_type": "shelly_contactor_switch"},
                    {"meter": True, "switch": True, "charger": False},
                ),
                (
                    "split-meter-switch-modbus",
                    "[DEFAULT]\nHost=192.0.2.20\n"
                    "[Backends]\nMode=split\n"
                    "MeterType=shelly_meter\n"
                    f"MeterConfigPath={meter_path}\n"
                    "SwitchType=shelly_switch\n"
                    f"SwitchConfigPath={switch_path}\n"
                    "ChargerType=modbus_charger\n"
                    f"ChargerConfigPath={modbus_charger_path}\n",
                    {
                        "mode": "split",
                        "meter_type": "shelly_meter",
                        "switch_type": "shelly_switch",
                        "charger_type": "modbus_charger",
                    },
                    {"meter": True, "switch": True, "charger": True},
                ),
                (
                    "split-switchless-goe",
                    "[DEFAULT]\nHost=192.0.2.20\n"
                    "[Backends]\nMode=split\nMeterType=none\nSwitchType=none\n"
                    "ChargerType=goe_charger\n"
                    f"ChargerConfigPath={goe_charger_path}\n",
                    {
                        "mode": "split",
                        "meter_type": "none",
                        "switch_type": "none",
                        "charger_type": "goe_charger",
                    },
                    {"meter": False, "switch": False, "charger": True},
                ),
            )

            for name, payload, expected_selection, expected_roles in cases:
                with self.subTest(name=name):
                    wallbox_path = self._write_config(temp_dir, f"{name}.ini", payload)

                    result = validate_wallbox_config(wallbox_path)
                    selection = cast(dict[str, object], result["selection"])

                    for key, value in expected_selection.items():
                        self.assertEqual(selection[key], value)
                    self.assertEqual(result["resolved_roles"], expected_roles)
