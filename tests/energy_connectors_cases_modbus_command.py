# SPDX-License-Identifier: GPL-3.0-or-later
from tests.energy_connectors_cases_common import *


class _EnergyConnectorsModbusCommandCases:
    def test_read_energy_source_snapshot_dispatches_to_modbus_connector(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nTransport=tcp\n"
                "[Transport]\nHost=192.0.2.10\nPort=502\nUnitId=7\nRequestTimeoutSeconds=2.0\n"
                "[SocRead]\nRegisterType=holding\nAddress=10\nDataType=uint16\nScale=0.1\n"
                "[UsableCapacityRead]\nRegisterType=holding\nAddress=20\nDataType=uint16\n"
                "[BatteryPowerRead]\nRegisterType=holding\nAddress=30\nDataType=int16\n"
                "[ChargeLimitPowerRead]\nRegisterType=holding\nAddress=60\nDataType=uint16\n"
                "[DischargeLimitPowerRead]\nRegisterType=holding\nAddress=70\nDataType=uint16\n"
                "[AcPowerRead]\nRegisterType=holding\nAddress=40\nDataType=uint16\n"
                "[PvInputPowerRead]\nRegisterType=holding\nAddress=40\nDataType=uint16\n"
                "[GridInteractionRead]\nRegisterType=holding\nAddress=80\nDataType=int32\nScale=-1\n"
                "[OperatingModeRead]\nRegisterType=holding\nAddress=50\nDataType=uint16\n"
                "[OperatingModeMap]\n4=maximise_self_consumption\n"
                "[Aggregation]\nAcPowerScopeKey={host}:{port}:ac\nPvInputPowerScopeKey={host}:{port}:pv\n"
                "GridInteractionScopeKey={host}:{port}:meter\n",
            )
            runtime = SimpleNamespace(shelly_request_timeout_seconds=2.0)
            owner = SimpleNamespace(service=runtime)
            source = EnergySourceDefinition(
                source_id="modbus-battery",
                role="battery",
                connector_type="modbus",
                config_path=config_path,
            )

            with patch("venus_evcharger.energy.connectors.create_modbus_transport", return_value=_FakeModbusTransport()):
                snapshot = self._read_complete(owner, source, 300.0)

            self.assertEqual(snapshot.service_name, "192.0.2.10")
            self.assertEqual(snapshot.soc, 64.5)
            self.assertEqual(snapshot.usable_capacity_wh, 12000.0)
            self.assertEqual(snapshot.net_battery_power_w, -2000.0)
            self.assertEqual(snapshot.charge_power_w, 2000.0)
            self.assertEqual(snapshot.charge_limit_power_w, 900.0)
            self.assertEqual(snapshot.discharge_limit_power_w, 1400.0)
            self.assertEqual(snapshot.ac_power_w, 3200.0)
            self.assertEqual(snapshot.pv_input_power_w, 3200.0)
            self.assertEqual(snapshot.grid_interaction_w, 76560.0)
            self.assertEqual(snapshot.ac_power_scope_key, "192.0.2.10:502:ac")
            self.assertEqual(snapshot.pv_input_power_scope_key, "192.0.2.10:502:pv")
            self.assertEqual(snapshot.grid_interaction_scope_key, "192.0.2.10:502:meter")
            self.assertEqual(snapshot.operating_mode, "maximise_self_consumption")
            self.assertTrue(snapshot.online)

    def test_modbus_connector_supports_negative_scale_for_vendor_sign_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nTransport=tcp\n"
                "[Transport]\nHost=192.0.2.11\nPort=502\nUnitId=1\nRequestTimeoutSeconds=2.0\n"
                "[BatteryPowerRead]\nRegisterType=holding\nAddress=30\nDataType=int16\nScale=-1\n",
            )
            runtime = SimpleNamespace(shelly_request_timeout_seconds=2.0)
            owner = SimpleNamespace(service=runtime)
            source = EnergySourceDefinition(
                source_id="vendor-battery",
                role="battery",
                connector_type="modbus",
                config_path=config_path,
            )

            with patch("venus_evcharger.energy.connectors.create_modbus_transport", return_value=_FakeModbusTransport()):
                snapshot = self._read_complete(owner, source, 301.0)

            self.assertEqual(snapshot.net_battery_power_w, 2000.0)
            self.assertEqual(snapshot.charge_power_w, 0.0)
            self.assertEqual(snapshot.discharge_power_w, 2000.0)

    def test_read_energy_source_snapshot_dispatches_to_command_json_connector(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Command]\nArgs=python3 /tmp/fake-energy-helper.py --once\nTimeoutSeconds=1.5\n"
                "[Response]\nSocPath=data.soc\nBatteryPowerPath=data.battery_power_w\n"
                "AcPowerPath=data.ac_power_w\nPvInputPowerPath=data.pv_input_power_w\n"
                "GridInteractionPath=data.grid_power_w\nOperatingModePath=data.mode\n"
                "OnlinePath=data.online\nConfidencePath=data.confidence\n",
            )
            runtime = SimpleNamespace(shelly_request_timeout_seconds=2.0)
            owner = SimpleNamespace(service=runtime)
            source = EnergySourceDefinition(
                source_id="helper-energy",
                role="hybrid-inverter",
                connector_type="command_json",
                config_path=config_path,
                usable_capacity_wh=9000.0,
            )
            completed = SimpleNamespace(
                stdout='{"data":{"soc":57.0,"battery_power_w":1100.0,"ac_power_w":2500.0,"pv_input_power_w":1800.0,"grid_power_w":200.0,"mode":"support","online":true,"confidence":0.6}}'
            )

            with patch(
                "venus_evcharger.energy.connectors.run_bounded_command",
                return_value=completed,
            ) as run_mock:
                snapshot = self._read_complete(owner, source, 400.0)

            self.assertEqual(snapshot.service_name, "python3")
            self.assertEqual(snapshot.soc, 57.0)
            self.assertEqual(snapshot.usable_capacity_wh, 9000.0)
            self.assertEqual(snapshot.net_battery_power_w, 1100.0)
            self.assertEqual(snapshot.discharge_power_w, 1100.0)
            self.assertEqual(snapshot.ac_power_w, 2500.0)
            self.assertEqual(snapshot.pv_input_power_w, 1800.0)
            self.assertEqual(snapshot.grid_interaction_w, 200.0)
            self.assertEqual(snapshot.operating_mode, "support")
            self.assertTrue(snapshot.online)
            self.assertEqual(snapshot.confidence, 0.6)
            run_mock.assert_called_once()

    def test_command_json_connector_rejects_non_object_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Command]\nArgs=python3 /tmp/fake-helper.py\n"
                "[Response]\nSocPath=data.soc\n",
            )
            runtime = SimpleNamespace(shelly_request_timeout_seconds=2.0)
            owner = SimpleNamespace(service=runtime)
            source = EnergySourceDefinition(
                source_id="helper-energy",
                role="hybrid-inverter",
                connector_type="command_json",
                config_path=config_path,
            )
            completed = SimpleNamespace(stdout='["not-an-object"]')

            with patch(
                "venus_evcharger.energy.connectors.run_bounded_command",
                return_value=completed,
            ):
                with self.assertRaisesRegex(ValueError, "did not return a JSON object"):
                    self._read_complete(owner, source, 1.0)
