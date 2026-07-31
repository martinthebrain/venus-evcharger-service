# SPDX-License-Identifier: GPL-3.0-or-later
from tests.energy_probe_cases_common import (
    Path,
    _EnergyProbeBase,
    _FieldProbeTransport,
    io,
    json,
    main,
    patch,
    redirect_stdout,
    tempfile,
)


class _EnergyProbeCliCases(_EnergyProbeBase):
    def test_main_validate_huawei_energy_prints_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "huawei.ini",
                "[Adapter]\nType=modbus\n"
                "[Transport]\nRequestTimeoutSeconds=2.0\n"
                "[SocRead]\nRegisterType=holding\nAddress=10\nDataType=uint16\nScale=0.1\n",
            )
            stdout = io.StringIO()

            def fake_transport(settings: object) -> object:
                port = getattr(settings, "port")
                if port != 502:
                    raise TimeoutError("tcp timeout")
                return _FieldProbeTransport({10: (500,), 37100: (1,), 37113: (0, 250,), 37119: (0, 10,), 37121: (0, 5,), 37125: (2,)})

            with patch("venus_evcharger.energy.probe.create_modbus_transport", side_effect=fake_transport):
                with redirect_stdout(stdout):
                    rc = main(["validate-huawei-energy", config_path, "--profile", "huawei_smartlogger_modbus_tcp", "--host", "198.51.100.30"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(rc, 0)
        self.assertTrue(payload["validation_ok"])
        self.assertTrue(payload["meter_block_detected"])
        self.assertIn("suggested_template", payload["recommendation"])
        self.assertIn("config_snippet", payload["recommendation"])
        self.assertIn("wizard_hint_block", payload["recommendation"])
        self.assertEqual(payload["recommendation"]["suggested_profile"], "huawei_smartlogger_modbus_tcp")

    def test_main_validate_huawei_energy_can_emit_config_snippet(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "huawei.ini",
                "[Adapter]\nType=modbus\n"
                "[Transport]\nRequestTimeoutSeconds=2.0\n"
                "[SocRead]\nRegisterType=holding\nAddress=10\nDataType=uint16\nScale=0.1\n",
            )
            stdout = io.StringIO()

            def fake_transport(settings: object) -> object:
                port = getattr(settings, "port")
                if port != 502:
                    raise TimeoutError("tcp timeout")
                return _FieldProbeTransport({10: (500,), 37100: (1,), 37113: (0, 250,), 37119: (0, 10,), 37121: (0, 5,), 37125: (2,)})

            with patch("venus_evcharger.energy.probe.create_modbus_transport", side_effect=fake_transport):
                with redirect_stdout(stdout):
                    rc = main(["validate-huawei-energy", config_path, "--profile", "huawei_smartlogger_modbus_tcp", "--host", "198.51.100.30", "--emit", "ini"])

        output = stdout.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("AutoEnergySource.huawei.Profile=huawei_smartlogger_modbus_tcp", output)
        self.assertIn("AutoEnergySource.huawei.ConfigPath=/data/etc/huawei-mb-modbus.ini", output)
        self.assertNotIn('"recommendation"', output)

    def test_main_validate_huawei_energy_can_emit_wizard_hint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "huawei.ini",
                "[Adapter]\nType=modbus\n"
                "[Transport]\nRequestTimeoutSeconds=2.0\n"
                "[SocRead]\nRegisterType=holding\nAddress=10\nDataType=uint16\nScale=0.1\n",
            )
            stdout = io.StringIO()

            def fake_transport(settings: object) -> object:
                port = getattr(settings, "port")
                if port != 502:
                    raise TimeoutError("tcp timeout")
                return _FieldProbeTransport({10: (500,), 37100: (1,), 37113: (0, 250,), 37119: (0, 10,), 37121: (0, 5,), 37125: (2,)})

            with patch("venus_evcharger.energy.probe.create_modbus_transport", side_effect=fake_transport):
                with redirect_stdout(stdout):
                    rc = main(["validate-huawei-energy", config_path, "--profile", "huawei_smartlogger_modbus_tcp", "--host", "198.51.100.30", "--emit", "wizard-hint"])

        output = stdout.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("Huawei recommendation", output)
        self.assertIn("- profile: huawei_smartlogger_modbus_tcp", output)
        self.assertIn("- meter block: present", output)

    def test_main_validate_huawei_energy_can_write_recommendation_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "huawei.ini",
                "[Adapter]\nType=modbus\n"
                "[Transport]\nRequestTimeoutSeconds=2.0\n"
                "[SocRead]\nRegisterType=holding\nAddress=10\nDataType=uint16\nScale=0.1\n",
            )
            output_prefix = Path(temp_dir) / "exports" / "huawei-rec"
            stdout = io.StringIO()

            def fake_transport(settings: object) -> object:
                port = getattr(settings, "port")
                if port != 502:
                    raise TimeoutError("tcp timeout")
                return _FieldProbeTransport({10: (500,), 37100: (1,), 37113: (0, 250,), 37119: (0, 10,), 37121: (0, 5,), 37125: (2,)})

            with patch("venus_evcharger.energy.probe.create_modbus_transport", side_effect=fake_transport):
                with redirect_stdout(stdout):
                    rc = main(
                        [
                            "validate-huawei-energy",
                            config_path,
                            "--profile",
                            "huawei_smartlogger_modbus_tcp",
                            "--host",
                            "198.51.100.30",
                            "--write-recommendation-prefix",
                            str(output_prefix),
                        ]
                    )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            written_files = payload["written_files"]
            self.assertEqual(written_files["config_snippet"], str(output_prefix) + ".ini")
            self.assertEqual(written_files["wizard_hint"], str(output_prefix) + ".wizard.txt")
            self.assertEqual(written_files["summary"], str(output_prefix) + ".summary.txt")
            self.assertEqual(written_files["manifest"], str(output_prefix) + ".manifest.json")
            self.assertIn("AutoEnergySource.huawei.Profile=huawei_smartlogger_modbus_tcp", Path(written_files["config_snippet"]).read_text(encoding="utf-8"))
            self.assertIn("Huawei recommendation", Path(written_files["wizard_hint"]).read_text(encoding="utf-8"))
            self.assertIn("Use profile huawei_smartlogger_modbus_tcp", Path(written_files["summary"]).read_text(encoding="utf-8"))
            manifest = json.loads(Path(written_files["manifest"]).read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_type"], "energy-recommendation-bundle")
            self.assertEqual(manifest["schema_version"], 1)
            self.assertEqual(manifest["source_id"], "huawei")
            self.assertEqual(manifest["profile"], "huawei_smartlogger_modbus_tcp")
            self.assertEqual(manifest["config_path"], "/data/etc/huawei-mb-modbus.ini")
