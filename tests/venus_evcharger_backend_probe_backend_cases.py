# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_backend_probe_support import (
    BackendProbeTestCase,
    Path,
    main,
    patch,
    probe_charger_backend,
    probe_meter_backend,
    probe_switch_backend,
    read_charger_backend,
    validate_backend_config,
    io,
    json,
    redirect_stdout,
    tempfile,
)


class TestShellyWallboxBackendProbeBackend(BackendProbeTestCase):
    def test_validate_backend_config_accepts_meter_and_switch_types(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            meter_path = self._write_config(
                temp_dir,
                "meter.ini",
                "[Adapter]\nType=shelly_meter\nHost=192.168.1.10\n",
            )

            payload = validate_backend_config(meter_path)

            self.assertEqual(payload["type"], "shelly_meter")
            self.assertEqual(payload["roles"], ["meter"])

    def test_validate_backend_config_rejects_missing_and_unsupported_types(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_path = str(Path(temp_dir) / "missing.ini")
            with self.assertRaises(FileNotFoundError):
                validate_backend_config(missing_path)

            backend_path = self._write_config(
                temp_dir,
                "backend.ini",
                "[DEFAULT]\nType=unknown_backend\n",
            )
            with self.assertRaisesRegex(ValueError, "Unsupported backend type"):
                validate_backend_config(backend_path)

    def test_probe_helpers_reject_wrong_backend_roles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "backend.ini",
                "[Adapter]\nType=template_switch\nBaseUrl=http://switch.local\n"
                "[StateRequest]\nUrl=/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nUrl=/control\n",
            )

            with self.assertRaisesRegex(ValueError, "not a meter backend"):
                probe_meter_backend(config_path)
            with self.assertRaisesRegex(ValueError, "not a charger backend"):
                probe_charger_backend(config_path)
            with self.assertRaisesRegex(ValueError, "not a charger backend"):
                read_charger_backend(config_path)

    def test_probe_switch_backend_rejects_non_switch_backend_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "backend.ini",
                "[Adapter]\nType=template_meter\nBaseUrl=http://meter.local\n"
                "[MeterRequest]\nUrl=/meter/state\n"
                "[MeterResponse]\nPowerPath=power_w\n",
            )

            with self.assertRaisesRegex(ValueError, "not a switch backend"):
                probe_switch_backend(config_path)

    def test_read_charger_backend_rechecks_backend_registry_before_live_read(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "backend.ini",
                "[Adapter]\nType=template_charger\nBaseUrl=http://charger.local\n"
                "[EnableRequest]\nUrl=/charger/enable\n"
                "[CurrentRequest]\nUrl=/charger/current\n",
            )

            with (
                patch("venus_evcharger.backend.probe.probe_charger_backend", return_value={"path": config_path}),
                patch("venus_evcharger.backend.probe.CHARGER_BACKENDS", {}),
            ):
                with self.assertRaisesRegex(ValueError, "not a charger backend"):
                    read_charger_backend(config_path)

    def test_main_validate_command_prints_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            meter_path = self._write_config(
                temp_dir,
                "meter.ini",
                "[Adapter]\nType=shelly_meter\nHost=192.168.1.10\n",
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = main(["validate", meter_path])

            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(stdout.getvalue())["type"], "shelly_meter")

    def test_validate_backend_config_accepts_contactor_switch_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            switch_path = self._write_config(
                temp_dir,
                "switch.ini",
                "[Adapter]\nType=shelly_contactor_switch\nHost=192.168.1.11\n",
            )

            payload = validate_backend_config(switch_path)

            self.assertEqual(payload["type"], "shelly_contactor_switch")
            self.assertEqual(payload["roles"], ["switch"])

    def test_validate_backend_config_accepts_template_switch_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            switch_path = self._write_config(
                temp_dir,
                "switch.ini",
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n"
                "[StateRequest]\nUrl=/switch/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nUrl=/switch/control\n",
            )

            payload = validate_backend_config(switch_path)

            self.assertEqual(payload["type"], "template_switch")
            self.assertEqual(payload["roles"], ["switch"])

    def test_validate_backend_config_accepts_switch_group_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            self._write_config(
                temp_dir,
                "phase1-switch.ini",
                "[Adapter]\nType=template_switch\nBaseUrl=http://phase1.local\n"
                "[StateRequest]\nUrl=/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nUrl=/control\n",
            )
            switch_path = self._write_config(
                temp_dir,
                "switch.ini",
                "[Adapter]\nType=switch_group\n"
                "[Members]\nP1=phase1-switch.ini\n",
            )

            payload = validate_backend_config(switch_path)

            self.assertEqual(payload["type"], "switch_group")
            self.assertEqual(payload["roles"], ["switch"])

    def test_validate_backend_config_accepts_template_meter_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            meter_path = self._write_config(
                temp_dir,
                "meter.ini",
                "[Adapter]\nType=template_meter\nBaseUrl=http://adapter.local\n"
                "[MeterRequest]\nUrl=/meter/state\n"
                "[MeterResponse]\nPowerPath=power_w\n",
            )

            payload = validate_backend_config(meter_path)

            self.assertEqual(payload["type"], "template_meter")
            self.assertEqual(payload["roles"], ["meter"])

    def test_validate_backend_config_accepts_template_charger_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = self._write_config(
                temp_dir,
                "charger.ini",
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[EnableRequest]\nUrl=/charger/enable\n"
                "[CurrentRequest]\nUrl=/charger/current\n",
            )

            payload = validate_backend_config(charger_path)

            self.assertEqual(payload["type"], "template_charger")
            self.assertEqual(payload["roles"], ["charger"])

    def test_validate_backend_config_accepts_goe_charger_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = self._write_config(
                temp_dir,
                "charger.ini",
                "[Adapter]\nType=goe_charger\nBaseUrl=http://goe.local\n",
            )

            payload = validate_backend_config(charger_path)

            self.assertEqual(payload["type"], "goe_charger")
            self.assertEqual(payload["roles"], ["charger"])
