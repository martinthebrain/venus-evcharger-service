# SPDX-License-Identifier: GPL-3.0-or-later
from tests.energy_probe_cases_common import (
    _EnergyProbeBase,
    _FieldProbeTransport,
    patch,
    tempfile,
    validate_huawei_energy_source,
)
from venus_evcharger.energy import probe as probe_mod


class _EnergyProbeValidateCases(_EnergyProbeBase):
    def test_validate_huawei_energy_source_reads_required_and_meter_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "huawei.ini",
                "[Adapter]\nType=modbus\n"
                "[Transport]\nRequestTimeoutSeconds=2.0\n"
                "[SocRead]\nRegisterType=holding\nAddress=10\nDataType=uint16\nScale=0.1\n"
                "[BatteryPowerRead]\nRegisterType=holding\nAddress=30\nDataType=int16\nScale=-1\n"
                "[AcPowerRead]\nRegisterType=holding\nAddress=40\nDataType=uint16\n"
                "[PvInputPowerRead]\nRegisterType=holding\nAddress=50\nDataType=uint16\n"
                "[GridInteractionRead]\nRegisterType=holding\nAddress=60\nDataType=int32\nScale=-1\n"
                "[OperatingModeRead]\nRegisterType=holding\nAddress=70\nDataType=uint16\n",
            )

            def fake_transport(settings: object) -> object:
                port = getattr(settings, "port")
                if port != 502:
                    raise TimeoutError("tcp timeout")
                return _FieldProbeTransport(
                    {
                        10: (645,),
                        30: (0xF830,),
                        40: (3200,),
                        50: (2800,),
                        60: (0xFFFE, 0xD4F0,),
                        70: (4,),
                        37100: (1,),
                        37113: (0xFFFF, 0xFC18,),
                        37119: (0, 150,),
                        37121: (0, 75,),
                        37125: (2,),
                    }
                )

            with patch("venus_evcharger.energy.probe.create_modbus_transport", side_effect=fake_transport):
                payload = validate_huawei_energy_source(config_path, profile_name="huawei_mb_native_lan", host="198.51.100.25")

        self.assertTrue(payload["validation_ok"])
        self.assertTrue(payload["meter_block_detected"])
        self.assertEqual(payload["recommendation"]["suggested_profile"], "huawei_mb_native_lan")
        self.assertEqual(payload["recommendation"]["suggested_template"], "deploy/venus/template-energy-source-huawei-mb-modbus.ini")
        self.assertEqual(payload["recommendation"]["suggested_config_path"], "/data/etc/huawei-mb-modbus.ini")
        self.assertEqual(payload["recommendation"]["port"], 502)
        self.assertEqual(payload["recommendation"]["unit_id"], 0)
        self.assertIn("AutoEnergySource.huawei.Profile=huawei_mb_native_lan", payload["recommendation"]["config_snippet"])
        self.assertIn("AutoEnergySource.huawei.ConfigPath=/data/etc/huawei-mb-modbus.ini", payload["recommendation"]["config_snippet"])
        self.assertIn("AutoEnergySource.huawei.Port=502", payload["recommendation"]["config_snippet"])
        self.assertIn("AutoEnergySource.huawei.UnitId=0", payload["recommendation"]["config_snippet"])
        self.assertTrue(payload["recommendation"]["capacity_required_for_weighted_soc"])
        self.assertEqual(payload["recommendation"]["capacity_config_key"], "AutoEnergySource.huawei.UsableCapacityWh")
        self.assertIn("usable battery capacity in Wh", payload["recommendation"]["capacity_hint"])
        self.assertIn("- meter block: present", payload["recommendation"]["wizard_hint_block"])
        self.assertIn("- capacity follow-up: set AutoEnergySource.huawei.UsableCapacityWh for weighted combined SOC", payload["recommendation"]["wizard_hint_block"])
        sections = {entry["section"]: entry for entry in payload["field_results"]}
        self.assertTrue(sections["GridInteractionRead"]["ok"])
        self.assertEqual(sections["GridInteractionRead"]["scaled_value"], 76560.0)
        self.assertTrue(sections["HuaweiMeterActivePowerRead"]["ok"])
        self.assertEqual(sections["HuaweiMeterActivePowerRead"]["scaled_value"], 1000.0)

    def test_huawei_meter_block_detected_rejects_missing_or_failed_meter_fields(self) -> None:
        self.assertFalse(
            probe_mod._huawei_meter_block_detected(
                [
                    {"section": "SocRead", "ok": True},
                    {"section": "HuaweiMeterActivePowerRead", "ok": False},
                    {"section": "MeterStatusRead", "ok": False},
                ]
            )
        )

    def test_validate_huawei_energy_source_uses_family_templates_for_unit_and_smartlogger_variants(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "huawei.ini",
                "[Adapter]\nType=modbus\n"
                "[Transport]\nRequestTimeoutSeconds=2.0\n"
                "[SocRead]\nRegisterType=holding\nAddress=10\nDataType=uint16\nScale=0.1\n"
                "[BatteryPowerRead]\nRegisterType=holding\nAddress=30\nDataType=int16\nScale=-1\n"
                "[AcPowerRead]\nRegisterType=holding\nAddress=40\nDataType=uint16\n"
                "[PvInputPowerRead]\nRegisterType=holding\nAddress=50\nDataType=uint16\n"
                "[GridInteractionRead]\nRegisterType=holding\nAddress=60\nDataType=int32\nScale=-1\n"
                "[OperatingModeRead]\nRegisterType=holding\nAddress=70\nDataType=uint16\n",
            )

            def fake_transport(settings: object) -> object:
                port = getattr(settings, "port")
                if port != 502:
                    raise TimeoutError("tcp timeout")
                return _FieldProbeTransport(
                    {
                        10: (500,),
                        30: (0xFFF6,),
                        40: (3000,),
                        50: (2500,),
                        60: (0, 400,),
                        70: (2,),
                        37100: (1,),
                        37113: (0, 250,),
                        37119: (0, 10,),
                        37121: (0, 5,),
                        37125: (2,),
                    }
                )

            with patch("venus_evcharger.energy.probe.create_modbus_transport", side_effect=fake_transport):
                unit_payload = validate_huawei_energy_source(config_path, profile_name="huawei_map0_unit1", host="198.51.100.26")
                smartlogger_payload = validate_huawei_energy_source(
                    config_path,
                    profile_name="huawei_l1_smartlogger_modbus_tcp",
                    host="198.51.100.27",
                )

        self.assertEqual(unit_payload["recommendation"]["suggested_profile"], "huawei_map0_unit1")
        self.assertEqual(unit_payload["recommendation"]["suggested_template"], "deploy/venus/template-energy-source-huawei-mb-unit1-modbus.ini")
        self.assertEqual(unit_payload["recommendation"]["suggested_config_path"], "/data/etc/huawei-mb-unit1-modbus.ini")
        self.assertIn("AutoEnergySource.huawei.Profile=huawei_map0_unit1", unit_payload["recommendation"]["config_snippet"])
        self.assertEqual(smartlogger_payload["recommendation"]["suggested_profile"], "huawei_l1_smartlogger_modbus_tcp")
        self.assertEqual(smartlogger_payload["recommendation"]["suggested_template"], "deploy/venus/template-energy-source-huawei-ma-modbus.ini")
        self.assertEqual(smartlogger_payload["recommendation"]["suggested_config_path"], "/data/etc/huawei-ma-modbus.ini")

    def test_validate_huawei_energy_source_can_render_custom_source_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "huawei.ini",
                "[Adapter]\nType=modbus\n"
                "[Transport]\nRequestTimeoutSeconds=2.0\n"
                "[SocRead]\nRegisterType=holding\nAddress=10\nDataType=uint16\nScale=0.1\n",
            )

            def fake_transport(settings: object) -> object:
                port = getattr(settings, "port")
                if port != 502:
                    raise TimeoutError("tcp timeout")
                return _FieldProbeTransport(
                    {
                        10: (500,),
                        37100: (1,),
                        37113: (0, 250,),
                        37119: (0, 10,),
                        37121: (0, 5,),
                        37125: (2,),
                    }
                )

            with patch("venus_evcharger.energy.probe.create_modbus_transport", side_effect=fake_transport):
                payload = validate_huawei_energy_source(
                    config_path,
                    profile_name="huawei_mb_sdongle",
                    host="198.51.100.30",
                    source_id="hybrid_ext",
                )

        self.assertEqual(payload["recommendation"]["capacity_config_key"], "AutoEnergySource.hybrid_ext.UsableCapacityWh")
        self.assertIn("AutoEnergySource.hybrid_ext.Profile=huawei_mb_sdongle", payload["recommendation"]["config_snippet"])
        self.assertIn("- source id: hybrid_ext", payload["recommendation"]["wizard_hint_block"])
