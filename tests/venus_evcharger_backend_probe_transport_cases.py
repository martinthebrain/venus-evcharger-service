# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_backend_probe_support import (
    BackendProbeTestCase,
    _FakeSimpleEvseTransport,
    _FakeSmartEvseTransport,
    cast,
    io,
    json,
    main,
    patch,
    redirect_stdout,
    tempfile,
    validate_backend_config,
)


class TestShellyWallboxBackendProbeTransport(BackendProbeTestCase):
    def test_validate_backend_config_accepts_modbus_charger_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = self._write_config(
                temp_dir,
                "charger.ini",
                "[Adapter]\nType=modbus_charger\nProfile=generic\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.40\nPort=502\nUnitId=7\n"
                "[EnableWrite]\nRegisterType=coil\nAddress=20\nTrueValue=1\nFalseValue=0\n"
                "[CurrentWrite]\nRegisterType=holding\nAddress=30\nDataType=uint16\nScale=10\n",
            )

            payload = validate_backend_config(charger_path)

            self.assertEqual(payload["type"], "modbus_charger")
            self.assertEqual(payload["roles"], ["charger"])

    def test_validate_backend_config_accepts_simpleevse_charger_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = self._write_config(
                temp_dir,
                "charger.ini",
                "[Adapter]\nType=simpleevse_charger\nTransport=tcp\n"
                "[Capabilities]\nSupportedPhaseSelections=P1_P2_P3\n"
                "[Transport]\nHost=192.168.1.50\nPort=502\nUnitId=1\n",
            )

            payload = validate_backend_config(charger_path)

            self.assertEqual(payload["type"], "simpleevse_charger")
            self.assertEqual(payload["roles"], ["charger"])

    def test_validate_backend_config_accepts_smartevse_charger_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = self._write_config(
                temp_dir,
                "charger.ini",
                "[Adapter]\nType=smartevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.60\nPort=502\nUnitId=1\n",
            )

            payload = validate_backend_config(charger_path)

            self.assertEqual(payload["type"], "smartevse_charger")
            self.assertEqual(payload["roles"], ["charger"])

    def test_probe_modbus_charger_prints_transport_and_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = self._write_config(
                temp_dir,
                "charger.ini",
                "[Adapter]\nType=modbus_charger\nProfile=generic\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.40\nPort=502\nUnitId=7\n"
                "[EnableWrite]\nRegisterType=coil\nAddress=20\nTrueValue=1\nFalseValue=0\n"
                "[CurrentWrite]\nRegisterType=holding\nAddress=30\nDataType=uint16\nScale=10\n",
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = main(["probe-charger", charger_path])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["type"], "modbus_charger")
            self.assertEqual(payload["profile_name"], "generic")
            self.assertEqual(payload["transport_kind"], "tcp")
            self.assertEqual(payload["transport_unit_id"], 7)
            self.assertIsNone(payload["transport_device"])
            self.assertEqual(payload["transport_serial_port_owner"], "none")
            self.assertEqual(payload["transport_serial_retry_count"], 0)

    def test_probe_simpleevse_charger_prints_transport_and_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = self._write_config(
                temp_dir,
                "charger.ini",
                "[Adapter]\nType=simpleevse_charger\nTransport=tcp\n"
                "[Capabilities]\nSupportedPhaseSelections=P1_P2_P3\n"
                "[Transport]\nHost=192.168.1.50\nPort=502\nUnitId=1\n",
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = main(["probe-charger", charger_path])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["type"], "simpleevse_charger")
            self.assertEqual(payload["profile_name"], "simpleevse")
            self.assertEqual(payload["transport_kind"], "tcp")
            self.assertEqual(payload["transport_unit_id"], 1)
            self.assertIsNone(payload["transport_device"])
            self.assertEqual(payload["transport_serial_port_owner"], "none")
            self.assertEqual(payload["transport_serial_retry_count"], 0)
            self.assertEqual(payload["supported_phase_selections"], ["P1_P2_P3"])

    def test_probe_simpleevse_charger_prints_serial_owner_details(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = self._write_config(
                temp_dir,
                "charger.ini",
                "[Adapter]\nType=simpleevse_charger\nTransport=serial_rtu\n"
                "[Transport]\nDevice=/dev/ttyS7\nBaudrate=9600\nParity=N\nStopBits=1\nUnitId=1\n"
                "PortOwner=venus\nRetryCount=2\nRetryDelaySeconds=0.5\n",
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = main(["probe-charger", charger_path])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["type"], "simpleevse_charger")
            self.assertEqual(payload["transport_kind"], "serial_rtu")
            self.assertEqual(payload["transport_device"], "/dev/ttyS7")
            self.assertEqual(payload["transport_serial_port_owner"], "venus_serial_starter")
            self.assertEqual(payload["transport_serial_retry_count"], 2)
            self.assertEqual(payload["transport_serial_retry_delay_seconds"], 0.5)

    def test_probe_smartevse_charger_prints_transport_and_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = self._write_config(
                temp_dir,
                "charger.ini",
                "[Adapter]\nType=smartevse_charger\nTransport=tcp\n"
                "[Capabilities]\nSupportedPhaseSelections=P1_P2\n"
                "[Transport]\nHost=192.168.1.60\nPort=502\nUnitId=1\n",
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = main(["probe-charger", charger_path])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["type"], "smartevse_charger")
            self.assertEqual(payload["profile_name"], "smartevse")
            self.assertEqual(payload["transport_kind"], "tcp")
            self.assertEqual(payload["transport_unit_id"], 1)
            self.assertIsNone(payload["transport_device"])
            self.assertEqual(payload["transport_serial_port_owner"], "none")
            self.assertEqual(payload["transport_serial_retry_count"], 0)
            self.assertEqual(payload["supported_phase_selections"], ["P1_P2"])

    def test_read_simpleevse_charger_returns_live_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = self._write_config(
                temp_dir,
                "charger.ini",
                "[Adapter]\nType=simpleevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.50\nPort=502\nUnitId=1\n",
            )

            stdout = io.StringIO()
            with (
                redirect_stdout(stdout),
                patch(
                    "venus_evcharger.backend.native_modbus_backend.create_modbus_transport",
                    return_value=_FakeSimpleEvseTransport(),
                ),
            ):
                rc = main(["read-charger", charger_path])

            payload = json.loads(stdout.getvalue())
            charger_state = cast(dict[str, object], payload["charger_state"])
            self.assertEqual(rc, 0)
            self.assertEqual(payload["type"], "simpleevse_charger")
            self.assertEqual(charger_state["enabled"], True)
            self.assertEqual(charger_state["current_amps"], 16.0)
            self.assertEqual(charger_state["actual_current_amps"], 13.0)
            self.assertEqual(charger_state["status_text"], "charging")

    def test_read_smartevse_charger_returns_live_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = self._write_config(
                temp_dir,
                "charger.ini",
                "[Adapter]\nType=smartevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.60\nPort=502\nUnitId=1\n",
            )

            stdout = io.StringIO()
            with (
                redirect_stdout(stdout),
                patch(
                    "venus_evcharger.backend.native_modbus_backend.create_modbus_transport",
                    return_value=_FakeSmartEvseTransport(),
                ),
            ):
                rc = main(["read-charger", charger_path])

            payload = json.loads(stdout.getvalue())
            charger_state = cast(dict[str, object], payload["charger_state"])
            self.assertEqual(rc, 0)
            self.assertEqual(payload["type"], "smartevse_charger")
            self.assertEqual(charger_state["enabled"], True)
            self.assertEqual(charger_state["current_amps"], 16.0)
            self.assertEqual(charger_state["phase_selection"], "P1")
            self.assertEqual(charger_state["status_text"], "charging")
